"""Re-planning when something changes: a vehicle breaks down, an urgent order arrives, a customer
cancels or asks for another time slot.

Two modes:
- Before departure: nothing is loaded yet, so the whole day is solved again, warm-started from the
  current plan so that the new plan stays close to the old one (fewer surprises for drivers and the
  warehouse).
- During the day (clock after the first departure): stops already done stay done, and goods that are on a
  working truck stay on it, so its current trip keeps its sequence. Open work (stops of a broken truck,
  new orders, stops that no longer fit their new window) is served by *second trips* from the depot:
  vehicles that never left, and vehicles after they return and reload. Windows become soft (a late
  arrival costs money per minute, up to 2 hours), because "late and called ahead" beats "not delivered".
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from .domain import Order, Problem
from .plan import Plan, evaluate
from .solver import Slot, add_second_trips, skeleton_from, solve

MAX_LATE_MIN = 120


@dataclass
class Change:
    kind: str  # breakdown | add_order | cancel | set_window
    vehicle_id: int | None = None
    order: Order | None = None
    order_id: str | None = None
    tw_start: int | None = None
    tw_end: int | None = None
    note: str = ""


@dataclass
class ReplanResult:
    mode: str  # pre_departure | intraday
    clock: int
    problem: Problem
    plan: Plan
    open_orders: list[str] = field(default_factory=list)
    solve_s: float = 0.0


def first_departure(plan: Plan) -> int | None:
    starts = [r.start for r in plan.routes if r.start is not None]
    return min(starts) if starts else None


def _apply_to_problem(problem: Problem, change: Change) -> Problem:
    p = copy.deepcopy(problem)
    if change.kind == "breakdown":
        if change.vehicle_id not in p.unavailable_vehicles:
            p.unavailable_vehicles.append(change.vehicle_id)
    elif change.kind == "add_order":
        assert change.order is not None
        p.orders.append(copy.deepcopy(change.order))
    elif change.kind == "cancel":
        p.orders = [o for o in p.orders if o.id != change.order_id]
    elif change.kind == "set_window":
        o = p.order(change.order_id)
        o.tw_start, o.tw_end = change.tw_start, change.tw_end
    else:
        raise ValueError(change.kind)
    return p


def replan(problem: Problem, plan: Plan, change: Change, clock: int | None,
           time_limit_s: float = 5.0) -> ReplanResult:
    new_problem = _apply_to_problem(problem, change)
    dep = first_departure(plan)
    if clock is None or dep is None or clock <= dep:
        return _pre_departure(new_problem, plan, change, clock or 0, time_limit_s)
    return _intraday(new_problem, plan, change, clock, time_limit_s)


# ------------------------------------------------------------------------------------ pre-departure
def _pre_departure(problem: Problem, plan: Plan, change: Change, clock: int, limit: float) -> ReplanResult:
    vehicles = problem.available_vehicles()
    slots = [Slot(v, max(v.shift_start, problem.depot.opens), v.shift_end, v.max_duration) for v in vehicles]
    by_vehicle = {int(k): [oid for _, oids in trips for oid in oids] for k, trips in plan.skeleton["routes"].items()}
    valid = {o.id for o in problem.orders}
    initial = {k: [oid for oid in by_vehicle.get(s.vehicle.id, []) if oid in valid] for k, s in enumerate(slots)}
    res = solve(problem, slots=slots, time_limit_s=limit, initial=initial, previous_vehicle=plan.vehicle_of_order())
    # What no longer fits goes on second trips; late (called ahead) is better than not delivered.
    skeleton, still = add_second_trips(problem, skeleton_from(res), res.dropped, max_late_min=MAX_LATE_MIN)
    reasons = {oid: "ne staje u kapacitete / vremenske prozore (+2 h)" for oid in still}
    new_plan = evaluate(problem, skeleton, "or-tools (ponovo pre polaska)", reasons, allow_late=True)
    return ReplanResult("pre_departure", clock, problem, new_plan, [], round(res.wall_s, 2))


# ------------------------------------------------------------------------------------------ intraday
def _intraday(problem: Problem, plan: Plan, change: Change, clock: int, limit: float) -> ReplanResult:
    valid = {o.id: o for o in problem.orders}
    broken_vid = change.vehicle_id if change.kind == "breakdown" else None
    frozen: dict[int, list] = {}
    open_ids: list[str] = []
    busy_until: dict[int, int] = {}
    first_start: dict[int, int] = {}
    broken = {int(k): v for k, v in plan.skeleton.get("broken", {}).items()}

    for route in plan.routes:
        vid = route.vehicle_id
        if route.start is not None:
            first_start[vid] = route.start
        keep = []
        for trip in route.trips:
            oids = [s.order_id for s in trip.stops]
            if trip.depart > clock:  # not loaded yet: free to re-plan
                open_ids += [o for o in oids if o in valid]
                continue
            if vid == broken_vid:
                done = [s.order_id for s in trip.stops if s.depart <= clock]
                open_ids += [s.order_id for s in trip.stops if s.depart > clock and s.order_id in valid]
                keep.append([trip.depart, done])
                continue
            seq = []
            for s in trip.stops:
                if s.depart <= clock:
                    seq.append(s.order_id)  # done
                elif s.order_id not in valid:
                    continue  # cancelled: goods stay on the truck and come back
                elif change.kind == "set_window" and s.order_id == change.order_id:
                    tw_s, tw_e = problem.window(valid[s.order_id])
                    if not (tw_s <= s.arrival + 15 and s.start <= tw_e):
                        open_ids.append(s.order_id)  # does not fit the new slot on this truck
                    else:
                        seq.append(s.order_id)
                else:
                    seq.append(s.order_id)
            keep.append([trip.depart, seq])
        if keep:
            frozen[vid] = keep
    # Orders that were unserved before, or are new, are open too.
    planned = {s.order_id for r in plan.routes for t in r.trips for s in t.stops}
    for o in problem.orders:
        if o.id not in planned and o.id not in open_ids:
            open_ids.append(o.id)

    skeleton = {"routes": frozen, "broken": {**broken, **({broken_vid: clock} if broken_vid else {})}}
    interim = evaluate(problem, skeleton, "interim", allow_late=True)
    for r in interim.routes:
        busy_until[r.vehicle_id] = r.end if r.broken_at is None else 10**6

    slots: list[Slot] = []
    for v in problem.available_vehicles():
        if v.id in skeleton["broken"]:
            continue
        ready = max(clock, busy_until.get(v.id, clock)) + problem.depot.reload_min
        start0 = first_start.get(v.id)
        max_dur = v.max_duration - (ready - start0) if start0 is not None else v.max_duration
        if ready >= v.shift_end or max_dur <= 30:
            continue
        slots.append(Slot(v, ready, v.shift_end, max_dur, trip_no=2 if v.id in frozen else 1))

    open_orders = [valid[o] for o in open_ids if o in valid]
    res = solve(problem, orders=open_orders, slots=slots, time_limit_s=limit, max_late_min=MAX_LATE_MIN)
    for slot, depart, seq in res.trips:
        frozen.setdefault(slot.vehicle.id, []).append([depart, seq])
    reasons = {oid: "danas nije izvodljivo: nema slobodnog vozila u prozoru (+2 h)" for oid in res.dropped}
    new_plan = evaluate(problem, {"routes": frozen, "broken": skeleton["broken"]},
                        "or-tools (preraspodela u toku dana)", reasons, allow_late=True)
    return ReplanResult("intraday", clock, problem, new_plan, [o.id for o in open_orders], round(res.wall_s, 2))


# ------------------------------------------------------------------------------------------------ diff
def diff_plans(problem_old: Problem, old: Plan, problem_new: Problem, new: Plan) -> dict:
    ov, nv = old.vehicle_of_order(), new.vehicle_of_order()
    moved = []
    for oid, v_new in nv.items():
        v_old = ov.get(oid)
        if v_old is not None and v_old != v_new:
            cid = problem_new.order(oid).customer_id
            moved.append({"order_id": oid, "customer": problem_new.customers[cid].name,
                          "from_vehicle": v_old, "to_vehicle": v_new})
    added = [oid for oid in nv if oid not in ov and oid not in {o.id for o in problem_old.orders}]
    old_late = {s.order_id for r in old.routes for t in r.trips for s in t.stops if s.late_min > 0}
    late = []
    for r in new.routes:
        for t in r.trips:
            for s in t.stops:
                if s.late_min > 0 and s.order_id not in old_late:
                    c = problem_new.customers[s.customer_id]
                    late.append({"order_id": s.order_id, "customer": c.name, "town": c.town,
                                 "vehicle": r.vehicle_id, "late_min": s.late_min, "arrival": s.start})
    old_unserved = {u["order_id"] for u in old.unserved}
    newly_unserved = [{**u, "customer": problem_new.customers[u["customer_id"]].name}
                      for u in new.unserved if u["order_id"] not in old_unserved]
    second_trips = []
    for r in new.routes:
        if len(r.trips) > 1:
            for t in r.trips[1:]:
                second_trips.append({"vehicle": r.vehicle_id, "depart": t.depart, "stops": len(t.stops)})
    keys = ["cost_rsd", "km", "vehicles_used", "served", "unserved", "late_stops", "crew_hours", "co2_kg"]
    delta = {k: round(new.kpis[k] - old.kpis[k], 1) for k in keys}
    new_ids = {o.id for o in problem_new.orders}
    removed = []
    for r in old.routes:
        for t in r.trips:
            for s in t.stops:
                if s.order_id not in new_ids:
                    removed.append({"order_id": s.order_id, "customer": problem_old.customers[s.customer_id].name,
                                    "vehicle": r.vehicle_id})
    return {"moved": moved, "added": added, "removed": removed, "newly_late": late, "newly_unserved": newly_unserved,
            "second_trips": second_trips, "delta": delta,
            "old": {k: old.kpis[k] for k in keys}, "new": {k: new.kpis[k] for k in keys},
            "hard_violations": new.kpis["hard_violations"]}
