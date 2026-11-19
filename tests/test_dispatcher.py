"""Command parser, service rules (approval, staleness, audit), LLM agent loop with a scripted model."""

from types import SimpleNamespace

import pytest

from dispatcher import llm, service
from dispatcher.agent import handle_command
from dispatcher.agent.parser import parse


@pytest.mark.parametrize("text,expected", [
    ("Kamion 3 je u kvaru, preraspodeli", {"intent": "breakdown", "vehicle": 3, "time": None}),
    ("Vozilo 5 se pokvarilo u 10:30", {"intent": "breakdown", "vehicle": 5, "time": 630}),
    ("Market Sunce otkazao isporuku", {"intent": "cancel", "customer": "market sunce"}),
    ("Hotel Dunav može tek od 13 do 15h", {"intent": "set_window", "customer": "hotel dunav",
                                            "tw_start": 780, "tw_end": 900}),
    ("gde je kamion 2", {"intent": "route", "vehicle": 2}),
    ("napiši pesmu", {"intent": "unknown"}),
])
def test_parser(text, expected):
    got = parse(text)
    for k, v in expected.items():
        assert got[k] == v


def test_parser_add_order():
    got = parse("Dodaj porudžbinu: Supermarket Avala Zemun 2 palete 1100 kg od 11 do 14")
    assert got["intent"] == "add_order" and got["pallets"] == 2.0 and got["kg"] == 1100
    assert (got["tw_start"], got["tw_end"]) == (660, 840)


def test_proposal_needs_a_person(store):
    active_before = store.active()["id"]
    r = handle_command(store, "kamion 1 je u kvaru", 600, "lazar")
    pid = r.proposal_ids[0]
    assert store.active()["id"] == active_before  # nothing changed yet
    with pytest.raises(service.ServiceError):
        service.apply_proposal(store, pid, "agent (za lazar)")
    out = service.apply_proposal(store, pid, "lazar")
    assert out["status"] == "APPLIED"
    assert store.active()["id"] != active_before


def test_stale_proposal_is_refused(store):
    a = handle_command(store, "kamion 1 je u kvaru", 600, "lazar").proposal_ids[0]
    b = handle_command(store, "kamion 2 je u kvaru", 600, "lazar").proposal_ids[0]
    service.apply_proposal(store, a, "lazar")
    with pytest.raises(service.ServiceError, match="zastareo"):
        service.apply_proposal(store, b, "lazar")
    assert store.proposal(b)["status"] == "STALE"


def test_cannot_cancel_delivered_stop(store):
    _, problem, plan = service.current(store)
    first = min((s for r in plan.routes for t in r.trips for s in t.stops), key=lambda s: s.depart)
    name = problem.customers[first.customer_id].name
    with pytest.raises(service.ServiceError, match="već obavljena"):
        service.propose(store, {"intent": "cancel", "customer": first.customer_id}, first.depart + 5, "test", "lazar")
    assert name


def test_audit_chain_detects_tampering(store):
    handle_command(store, "kamion 1 je u kvaru", 600, "lazar")
    assert store.verify_audit() == (True, None)
    store.conn.execute("UPDATE audit SET actor='mallory' WHERE id=1")
    store.conn.commit()
    ok, bad = store.verify_audit()
    assert not ok and bad == 1


class FakeClient:
    """Plays back a scripted conversation and records the requests."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.requests.append(kw)
        return self.script.pop(0)


def _resp(stop, blocks):
    return SimpleNamespace(stop_reason=stop, content=blocks,
                           usage=SimpleNamespace(input_tokens=1000, output_tokens=200, cache_read_input_tokens=0))


def test_llm_agent_loop_creates_pending_proposal(store, monkeypatch):
    script = [
        _resp("tool_use", [SimpleNamespace(type="tool_use", id="t1", name="predlozi_kvar",
                                           input={"vozilo": 2, "vreme": "10:15"})]),
        _resp("end_turn", [SimpleNamespace(type="text", text="Vozilo 2 je u kvaru. Predlog #1 čeka tvoje odobrenje.")]),
    ]
    fake = FakeClient(script)
    monkeypatch.setattr(llm, "_client", fake)
    monkeypatch.setattr(llm, "llm_available", lambda: True)
    active_before = store.active()["id"]
    r = handle_command(store, "dvojka je stala u 10:15", 540, "lazar")
    assert r.source == "llm"
    assert r.intent == {"intent": "breakdown", "vehicle": 2, "time": 615}
    assert r.proposal_ids == [1]
    assert store.proposal(1)["status"] == "PENDING"
    assert store.proposal(1)["created_by"].startswith("agent")
    assert store.active()["id"] == active_before
    # the tool result went back to the model with the facts it must use
    tool_result = fake.requests[1]["messages"][2]["content"][0]
    assert tool_result["type"] == "tool_result" and "proposal_id" in tool_result["content"]
    assert r.cost_usd > 0


def test_llm_tool_error_is_returned_not_raised(store, monkeypatch):
    script = [
        _resp("tool_use", [SimpleNamespace(type="tool_use", id="t1", name="predlozi_otkazivanje",
                                           input={"kupac_id": "nepostojeci kupac xyz"})]),
        _resp("end_turn", [SimpleNamespace(type="text", text="Ne mogu da nađem kupca.")]),
    ]
    fake = FakeClient(script)
    monkeypatch.setattr(llm, "_client", fake)
    monkeypatch.setattr(llm, "llm_available", lambda: True)
    r = handle_command(store, "otkazi xyz", 540, "lazar")
    assert r.proposal_ids == []
    assert fake.requests[1]["messages"][2]["content"][0].get("is_error") is True
