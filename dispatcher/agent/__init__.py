"""Entry point for dispatcher commands: the LLM agent when an API key is set, otherwise the rule parser."""

from __future__ import annotations

from .. import llm, service
from ..domain import hhmm
from ..store import Store
from .parser import parse
from .runner import DispatchResult


def handle_command(store: Store, command: str, clock: int, user: str = "dispecer",
                   use_llm: bool | None = None) -> DispatchResult:
    if (llm.llm_available() if use_llm is None else use_llm):
        from .runner import run

        return run(store, command, clock, user)
    return handle_with_rules(store, command, clock, user)


def handle_with_rules(store: Store, command: str, clock: int, user: str) -> DispatchResult:
    intent = parse(command)
    _, problem, plan = service.current(store)
    kind = intent["intent"]
    if kind == "overview":
        o = service.overview(problem, plan)
        k = o["kpis"]
        from ..explain import rsd

        lines = [f"Plan za {problem.day}: {k['served']}/{k['orders']} isporuka, {k['vehicles_used']} vozila, "
                 f"{k['km']:.0f} km, trošak {rsd(k['cost_rsd'])}, na vreme {k['on_time_pct']:.0f}%, "
                 f"kasni {k['late_stops']}, neisporučeno {k['unserved']}."]
        for v in o["vehicles"]:
            if v.get("stops"):
                extra = f", u kvaru od {v['broken_at']}" if v.get("broken_at") else ""
                lines.append(f"- Vozilo {v['vehicle']}: {v['stops']} stanica, {v['start']}-{v['end']}, "
                             f"{v['km']:.0f} km, popunjenost {v['load_pct']}%{extra}")
        return DispatchResult("\n".join(lines), intent, source="rules")
    if kind == "route":
        r = service.route_view(problem, plan, intent["vehicle"])
        if not r.get("trips"):
            return DispatchResult(f"Vozilo {intent['vehicle']} danas nema turu.", intent, source="rules")
        lines = [f"Vozilo {intent['vehicle']}:"]
        for t in r["trips"]:
            lines.append(f"Tura {t['trip']} ({t['depart']}-{t['end']}):")
            lines += [f"  {s['eta']} {s['customer']} ({s['town']}){' KASNI ' + str(s['late_min']) + ' min' if s['late_min'] else ''}"
                      for s in t["stops"]]
        return DispatchResult("\n".join(lines), intent, source="rules")
    if kind == "unknown":
        return DispatchResult("Nisam razumeo komandu. Primeri: 'kamion 3 je u kvaru', 'hitna porudžbina za "
                              "Kafić Lipa 0,5 paleta', 'Market Sunce otkazao', 'Hotel Dunav može tek od 13 do 15'.",
                              intent, source="rules")
    try:
        p = service.propose(store, intent, clock, "rules", user)
    except service.ServiceError as e:
        return DispatchResult(f"Ne mogu da napravim predlog: {e}.", intent, source="rules")
    answer = p["explanation"].replace("Plan čeka odobrenje dispečera.", f"Predlog #{p['proposal_id']} čeka tvoje odobrenje.")
    return DispatchResult(answer, intent, [p["proposal_id"]], source="rules", proposal=p)


__all__ = ["handle_command", "handle_with_rules", "parse", "hhmm"]
