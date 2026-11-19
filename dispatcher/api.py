"""REST API and the dashboard."""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import llm, roi, service
from .agent import handle_command
from .domain import Problem, hhmm, parse_hhmm
from .plan import Plan, position_at
from .store import Store

ROOT = Path(__file__).resolve().parent.parent
WEB_DIST = ROOT / "web" / "dist"

app = FastAPI(title="AI Route Dispatcher", version="0.1.0")
_store: Store | None = None


def store() -> Store:
    global _store
    if _store is None:
        _store = Store()
        if _store.active() is None:
            service.seed(_store, int(os.environ.get("SEED", "1")), float(os.environ.get("SOLVE_SECONDS", "10")))
    return _store


def set_store(s: Store) -> None:
    global _store
    _store = s


def _clock(clock: str | None) -> int:
    return parse_hhmm(clock) if clock else parse_hhmm(os.environ.get("SIM_CLOCK", "10:00"))


def _problem_view(problem: Problem, plan: Plan) -> dict:
    orders = {o.customer_id: o for o in problem.orders}
    return {
        "day": problem.day,
        "depot": {"name": problem.depot.name, "lat": problem.depot.lat, "lon": problem.depot.lon},
        "vehicles": [{"id": v.id, "kind": v.kind, "plate": v.plate, "capacity_kg": v.capacity_kg,
                      "capacity_pallets": v.capacity_dp / 10, "available": v.id not in problem.unavailable_vehicles}
                     for v in problem.vehicles],
        "customers": [{"id": c.id, "name": c.name, "type": c.type, "town": c.town, "lat": c.lat, "lon": c.lon,
                       "access": c.access, "key_account": c.key_account,
                       "order": ({"id": orders[c.id].id, "pallets": orders[c.id].deliver_dp / 10,
                                  "kg": orders[c.id].deliver_kg, "empties": orders[c.id].pickup_dp / 10,
                                  "urgent": orders[c.id].urgent,
                                  "window": "{}-{}".format(*map(hhmm, problem.window(orders[c.id])))}
                                 if c.id in orders else None)}
                      for c in problem.customers.values()],
    }


def _scenario_payload(scn: dict, clock: int) -> dict:
    problem, plan = Problem.from_dict(scn["problem"]), Plan.from_dict(scn["plan"])
    return {"scenario_id": scn["id"], "label": scn["label"], "status": scn["status"], "clock": hhmm(clock),
            "problem": _problem_view(problem, plan), "plan": plan.to_dict(),
            "positions": {v.id: position_at(problem, plan, v.id, clock) for v in problem.vehicles}}


@app.get("/api/state")
def state(clock: str | None = None) -> dict:
    s = store()
    scn = s.active()
    base = s.baseline()
    out = _scenario_payload(scn, _clock(clock))
    out["baseline_kpis"] = base["plan"]["kpis"] if base else None
    out["llm"] = llm.llm_available()
    out["pending"] = len(s.proposals("PENDING"))
    return out


@app.get("/api/baseline")
def baseline(clock: str | None = None) -> dict:
    base = store().baseline()
    if base is None:
        raise HTTPException(404, "nema osnovnog plana")
    return _scenario_payload(base, _clock(clock))


class Command(BaseModel):
    text: str
    clock: str | None = None


@app.post("/api/command")
def command(cmd: Command, x_user: str = Header(default="dispecer")) -> dict:
    s = store()
    res = handle_command(s, cmd.text, _clock(cmd.clock), x_user)
    return {"answer": res.answer, "intent": res.intent, "proposal_ids": res.proposal_ids, "source": res.source,
            "tool_calls": res.tool_calls, "cost_usd": round(res.cost_usd, 4), "latency_ms": res.latency_ms}


@app.get("/api/proposals")
def proposals(status: str | None = None) -> list[dict]:
    return store().proposals(status)


@app.get("/api/proposals/{pid}")
def proposal(pid: int, clock: str | None = None) -> dict:
    s = store()
    p = s.proposal(pid)
    if p is None:
        raise HTTPException(404, "predlog ne postoji")
    return {**p, "scenario": _scenario_payload(s.scenario(p["new_id"]), _clock(clock))}


@app.post("/api/proposals/{pid}/apply")
def apply(pid: int, x_user: str = Header(default="")) -> dict:
    try:
        return service.apply_proposal(store(), pid, x_user)
    except service.ServiceError as e:
        raise HTTPException(409, str(e))


@app.post("/api/proposals/{pid}/reject")
def reject(pid: int, x_user: str = Header(default="")) -> dict:
    try:
        return service.reject_proposal(store(), pid, x_user or "dispecer")
    except service.ServiceError as e:
        raise HTTPException(409, str(e))


class Reset(BaseModel):
    seed: int = 1
    solve_seconds: float = 10.0


@app.post("/api/reset")
def reset(body: Reset) -> dict:
    return service.seed(store(), body.seed, body.solve_seconds)


@app.get("/api/history")
def history() -> list[dict]:
    return store().history()


@app.get("/api/audit")
def audit() -> dict:
    s = store()
    ok, bad = s.verify_audit()
    return {"valid": ok, "first_bad_id": bad, "entries": s.audit_log()}


@app.get("/api/roi")
def roi_view(working_days: int = 300, dispatcher_rsd_per_hour: float = 1200, disruptions_per_day: float = 1.5,
             manual_plan_min: float = 90, manual_replan_min: float = 35) -> dict:
    s = store()
    base, scn = s.baseline(), s.active()
    return roi.business_case(base["plan"]["kpis"], scn["plan"]["kpis"], working_days, dispatcher_rsd_per_hour,
                             disruptions_per_day, manual_plan_min, manual_replan_min)


@app.get("/api/eval")
def eval_report() -> dict:
    p = ROOT / "docs" / "eval_report.json"
    if not p.exists():
        raise HTTPException(404, "pokreni 'dispatcher eval'")
    return json.loads(p.read_text(encoding="utf-8"))


if WEB_DIST.exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):  # noqa: ARG001
        return FileResponse(WEB_DIST / "index.html")
