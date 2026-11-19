"""API, business case and the OSRM adapter."""

from types import SimpleNamespace

from fastapi.testclient import TestClient

from dispatcher import api
from dispatcher.geo import osrm_matrix
from dispatcher.roi import business_case


def test_api_flow(store):
    api.set_store(store)
    c = TestClient(api.app)
    st = c.get("/api/state", params={"clock": "09:00"}).json()
    assert st["plan"]["kpis"]["hard_violations"] == 0
    assert set(map(int, st["positions"])) == {v["id"] for v in st["problem"]["vehicles"]}
    r = c.post("/api/command", json={"text": "kamion 1 je u kvaru", "clock": "10:00"}, headers={"X-User": "lazar"}).json()
    pid = r["proposal_ids"][0]
    assert c.get(f"/api/proposals/{pid}").json()["status"] == "PENDING"
    assert c.post(f"/api/proposals/{pid}/apply", headers={"X-User": "agent-bot"}).status_code == 409
    assert c.post(f"/api/proposals/{pid}/apply", headers={"X-User": "lazar"}).json()["status"] == "APPLIED"
    assert c.get("/api/audit").json()["valid"] is True
    roi = c.get("/api/roi").json()
    assert roi["per_year"]["total_rsd"] > 0


def test_business_case_arithmetic():
    base = {"cost_rsd": 200_000, "km": 1100, "fuel_l": 200, "co2_kg": 530, "late_stops": 10}
    opt = {"cost_rsd": 180_000, "km": 1000, "fuel_l": 180, "co2_kg": 477, "late_stops": 0}
    bc = business_case(base, opt, working_days=300, dispatcher_rsd_per_hour=1200, disruptions_per_day=2,
                       manual_plan_min=90, manual_replan_min=35, tool_plan_min=15, tool_replan_min=5)
    assert bc["per_day"]["route_cost_rsd"] == 20_000
    assert bc["per_day"]["dispatcher_minutes"] == 75 + 60
    assert bc["per_year"]["total_rsd"] == round((20_000 + 135 / 60 * 1200) * 300)
    assert bc["route_cost_pct"] == 10.0


def test_osrm_adapter_parses_table():
    body = {"code": "Ok", "distances": [[0, 1500.4], [1499.6, 0]], "durations": [[0, 180], [175, 0]]}

    class Http:
        def get(self, url, params):
            assert "/table/v1/driving/20.40000,44.80000;20.41000,44.81000" in url
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: body)

    m = osrm_matrix([(44.80, 20.40), (44.81, 20.41)], "http://osrm", client=Http())
    assert m.dist == [[0, 1500], [1500, 0]]
    assert m.time == [[0, 3], [3, 0]]
