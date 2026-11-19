"""Application service: seeding, reading the current plan, proposing changes, approving them.

Both the rule-based command parser and the LLM dispatcher end up here, so the approval rules are the same
whatever produced the proposal: nothing changes the active plan until a named person applies it.
"""

from __future__ import annotations

import difflib
import unicodedata

from .baseline import plan_baseline
from .data import make_problem
from .domain import Order, Problem, hhmm
from .explain import explain
from .plan import Plan
from .replan import Change, diff_plans, replan
from .solver import plan_day
from .store import Store

DEFAULT_KG_PER_PALLET = 550


class ServiceError(Exception):
    pass


def seed(store: Store, seed_no: int = 1, time_limit_s: float = 10.0, n_orders: int = 95) -> dict:
    store.reset()
    problem = make_problem(seed_no, n_orders)
    base = plan_baseline(problem)
    store.add_scenario("Ručni plan (heuristika: teritorije + najbliži sused)", problem.to_dict(), base.to_dict(),
                       "baseline")
    plan = plan_day(problem, time_limit_s)
    sid = store.add_scenario("Optimizovan plan dana (OR-Tools)", problem.to_dict(), plan.to_dict(), "active")
    store.audit("system", "plan_created", {"scenario": sid, "orders": len(problem.orders), "kpis": plan.kpis})
    return {"scenario_id": sid, "baseline": base.kpis, "optimized": plan.kpis}


def current(store: Store) -> tuple[dict, Problem, Plan]:
    scn = store.active()
    if scn is None:
        raise ServiceError("nema aktivnog plana; pokreni 'dispatcher seed'")
    return scn, Problem.from_dict(scn["problem"]), Plan.from_dict(scn["plan"])


# ------------------------------------------------------------------------------------- customer search
def norm(text: str) -> str:
    text = text.lower().replace("đ", "dj")
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def find_customers(problem: Problem, plan: Plan | None, query: str, limit: int = 5) -> list[dict]:
    q = norm(query).strip()
    order_by_customer = {o.customer_id: o for o in problem.orders}
    where = plan.vehicle_of_order() if plan else {}
    scored = []
    for c in problem.customers.values():
        name, town = norm(c.name), norm(c.town)
        score = difflib.SequenceMatcher(None, q, name).ratio()
        tokens = [t for t in q.split() if len(t) > 2]
        if tokens:
            hits = sum(1 for t in tokens if t in name or t in town)
            score += 0.6 * hits / len(tokens)
        if q in name:
            score += 0.5
        if c.id.lower() == q:
            score += 2
        scored.append((score, c))
    scored.sort(key=lambda x: -x[0])
    out = []
    for score, c in scored[:limit]:
        o = order_by_customer.get(c.id)
        item = {"customer_id": c.id, "name": c.name, "town": c.town, "type": c.type,
                "window": f"{hhmm(c.tw_start)}-{hhmm(c.tw_end)}", "score": round(score, 2),
                "order_today": o.id if o else None}
        if o and plan:
            hit = plan.stop_of(o.id)
            if hit:
                item["vehicle"] = hit[0].vehicle_id
                item["eta"] = hhmm(hit[2].start)
        out.append(item)
    return out


def resolve_customer(problem: Problem, plan: Plan, ref: str) -> str:
    if ref in problem.customers:
        return ref
    if ref.upper() in problem.customers:
        return ref.upper()
    for o in problem.orders:
        if o.id.lower() == ref.lower():
            return o.customer_id
    hits = find_customers(problem, plan, ref, 2)
    if not hits or hits[0]["score"] < 0.75:
        raise ServiceError(f"kupac '{ref}' nije pronađen")
    if len(hits) > 1 and hits[0]["score"] - hits[1]["score"] < 0.05:
        raise ServiceError(f"'{ref}' nije jednoznačno: {hits[0]['name']} ili {hits[1]['name']}?")
    return hits[0]["customer_id"]


# ----------------------------------------------------------------------------------------- proposals
def change_from_intent(problem: Problem, plan: Plan, intent: dict) -> tuple[Change, dict]:
    kind = intent.get("intent")
    if kind == "breakdown":
        vid = intent.get("vehicle")
        if vid is None or vid not in {v.id for v in problem.vehicles}:
            raise ServiceError(f"vozilo {vid} ne postoji (vozila su 1-{len(problem.vehicles)})")
        if vid in problem.unavailable_vehicles:
            raise ServiceError(f"vozilo {vid} je već van upotrebe")
        return Change("breakdown", vehicle_id=vid), {"kind": "breakdown", "vehicle_id": vid}
    if kind == "add_order":
        cid = resolve_customer(problem, plan, str(intent.get("customer")))
        pallets = float(intent.get("pallets") or 0.5)
        dp = max(1, round(pallets * 10))
        kg = int(intent.get("kg") or round(pallets * DEFAULT_KG_PER_PALLET))
        pickup = round(dp * 0.3) if intent.get("empties") is None else round(float(intent["empties"]) * 10)
        n = sum(1 for o in problem.orders if o.id.startswith("U")) + 1
        o = Order(f"U{n:03d}", cid, dp, kg, pickup, urgent=True,
                  tw_start=intent.get("tw_start"), tw_end=intent.get("tw_end"))
        if (o.tw_start is None) != (o.tw_end is None):
            c = problem.customers[cid]
            o.tw_start = o.tw_start if o.tw_start is not None else c.tw_start
            o.tw_end = o.tw_end if o.tw_end is not None else max(c.tw_end, o.tw_start + 60)
        from dataclasses import asdict

        return Change("add_order", order=o), {"kind": "add_order", "order": asdict(o)}
    if kind in ("cancel", "set_window"):
        cid = resolve_customer(problem, plan, str(intent.get("customer")))
        order = next((o for o in problem.orders if o.customer_id == cid), None)
        if order is None:
            raise ServiceError(f"{problem.customers[cid].name} danas nema porudžbinu")
        name = problem.customers[cid].name
        if kind == "cancel":
            return Change("cancel", order_id=order.id), {"kind": "cancel", "order_id": order.id, "customer_name": name}
        ts, te = intent.get("tw_start"), intent.get("tw_end")
        if ts is None and te is None:
            raise ServiceError("nedostaje novi vremenski prozor")
        c = problem.customers[cid]
        ts = ts if ts is not None else c.tw_start
        te = te if te is not None else max(ts + 120, c.tw_end)
        if te <= ts:
            raise ServiceError("kraj prozora mora biti posle početka")
        return (Change("set_window", order_id=order.id, tw_start=ts, tw_end=te),
                {"kind": "set_window", "order_id": order.id, "customer_name": name, "tw_start": ts, "tw_end": te})
    raise ServiceError(f"nepoznata komanda: {kind}")


def propose(store: Store, intent: dict, clock: int | None, source: str, user: str,
            time_limit_s: float = 5.0) -> dict:
    scn, problem, plan = current(store)
    change, change_d = change_from_intent(problem, plan, intent)
    when = intent.get("time") if intent.get("time") is not None else clock
    if change.kind in ("cancel", "set_window") and when is not None:
        hit = plan.stop_of(change.order_id)
        if hit and hit[2].depart <= when:
            raise ServiceError(f"isporuka za {change_d['customer_name']} je već obavljena u {hhmm(hit[2].start)}")
    res = replan(problem, plan, change, when, time_limit_s)
    change_d.update({"mode": res.mode, "clock": res.clock})
    diff = diff_plans(problem, plan, res.problem, res.plan)
    text = explain(res.problem, res.plan, diff, change_d)
    new_id = store.add_scenario(f"Predlog: {change_d['kind']}", res.problem.to_dict(), res.plan.to_dict(),
                                "proposed", parent_id=scn["id"])
    pid = store.add_proposal(scn["id"], new_id, change_d, diff, text, source, user)
    store.audit(user, "proposal_created", {"proposal": pid, "change": change_d, "source": source,
                                           "delta": diff["delta"], "solve_s": res.solve_s})
    return {"proposal_id": pid, "mode": res.mode, "change": change_d, "diff": diff, "explanation": text,
            "solve_s": res.solve_s, "status": "PENDING"}


def apply_proposal(store: Store, pid: int, user: str) -> dict:
    if not user or user.lower().startswith("agent"):
        raise ServiceError("predlog može da primeni samo imenovana osoba, ne agent")
    p = store.proposal(pid)
    if p is None:
        raise ServiceError("predlog ne postoji")
    if p["status"] != "PENDING":
        raise ServiceError(f"predlog je već {p['status']}")
    active = store.active()
    if active is None or active["id"] != p["base_id"]:
        store.decide(pid, "STALE", user)
        raise ServiceError("plan se u međuvremenu promenio; predlog je zastareo, napravi novi")
    store.set_status(active["id"], "superseded")
    store.set_status(p["new_id"], "active")
    store.decide(pid, "APPLIED", user)
    store.audit(user, "proposal_applied", {"proposal": pid, "scenario": p["new_id"]})
    return {"proposal_id": pid, "status": "APPLIED", "scenario_id": p["new_id"]}


def reject_proposal(store: Store, pid: int, user: str, reason: str = "") -> dict:
    p = store.proposal(pid)
    if p is None:
        raise ServiceError("predlog ne postoji")
    if p["status"] != "PENDING":
        raise ServiceError(f"predlog je već {p['status']}")
    store.decide(pid, "REJECTED", user)
    store.set_status(p["new_id"], "rejected")
    store.audit(user, "proposal_rejected", {"proposal": pid, "reason": reason})
    return {"proposal_id": pid, "status": "REJECTED"}


# ------------------------------------------------------------------------------------------- read views
def overview(problem: Problem, plan: Plan) -> dict:
    vehicles = []
    for v in problem.vehicles:
        r = plan.route_of(v.id)
        item = {"vehicle": v.id, "type": v.kind, "available": v.id not in problem.unavailable_vehicles}
        if r:
            item.update({"stops": sum(len(t.stops) for t in r.trips), "trips": len(r.trips),
                         "start": hhmm(r.start), "end": hhmm(r.end), "km": r.km, "cost_rsd": r.cost,
                         "broken_at": hhmm(r.broken_at) if r.broken_at is not None else None,
                         "load_pct": round(100 * max(max(t.load_kg / v.capacity_kg, t.load_dp / v.capacity_dp)
                                                     for t in r.trips))})
        vehicles.append(item)
    return {"day": problem.day, "kpis": plan.kpis, "vehicles": vehicles,
            "unserved": [{**u, "name": problem.customers[u["customer_id"]].name} for u in plan.unserved]}


def route_view(problem: Problem, plan: Plan, vid: int) -> dict:
    r = plan.route_of(vid)
    if r is None:
        return {"vehicle": vid, "stops": [], "note": "vozilo danas nema turu"}
    trips = []
    for k, t in enumerate(r.trips, start=1):
        trips.append({"trip": k, "depart": hhmm(t.depart), "end": hhmm(t.end), "stops": [
            {"order_id": s.order_id, "customer": problem.customers[s.customer_id].name,
             "town": problem.customers[s.customer_id].town, "eta": hhmm(s.start),
             "window": "{}-{}".format(*map(hhmm, problem.window(problem.order(s.order_id)))),
             "late_min": s.late_min} for s in t.stops]})
    return {"vehicle": vid, "broken_at": hhmm(r.broken_at) if r.broken_at is not None else None, "trips": trips}
