"""Re-planning: what is done stays done, nothing violates hard constraints."""

import pytest

from dispatcher.domain import Order
from dispatcher.replan import Change, diff_plans, replan


def test_breakdown_before_departure_drops_vehicle(small):
    problem, plan = small
    vid = plan.routes[0].vehicle_id
    res = replan(problem, plan, Change("breakdown", vehicle_id=vid), None, 2.0)
    assert res.mode == "pre_departure"
    assert res.plan.route_of(vid) is None
    assert res.plan.kpis["hard_violations"] == 0
    assert res.plan.kpis["served"] + res.plan.kpis["unserved"] == len(problem.orders)


def test_breakdown_during_day_keeps_done_stops(small):
    problem, plan = small
    clock = 600
    route = max(plan.routes, key=lambda r: r.end)
    done = [s.order_id for t in route.trips for s in t.stops if s.depart <= clock]
    open_ = [s.order_id for t in route.trips for s in t.stops if s.depart > clock]
    assert open_, "test needs a vehicle still working at 10:00"
    res = replan(problem, plan, Change("breakdown", vehicle_id=route.vehicle_id), clock, 2.0)
    assert res.mode == "intraday"
    new_route = res.plan.route_of(route.vehicle_id)
    assert [s.order_id for t in new_route.trips for s in t.stops] == done
    assert new_route.broken_at == clock
    # other vehicles keep the stops they already did, in the same order and at the same times
    for r in plan.routes:
        if r.vehicle_id == route.vehicle_id:
            continue
        old_done = [(s.order_id, s.start) for t in r.trips for s in t.stops if s.depart <= clock]
        new_r = res.plan.route_of(r.vehicle_id)
        new_done = [(s.order_id, s.start) for t in new_r.trips for s in t.stops if s.depart <= clock]
        assert new_done == old_done
    assert res.plan.kpis["hard_violations"] == 0
    served = set(res.plan.vehicle_of_order())
    unserved = {u["order_id"] for u in res.plan.unserved}
    assert served | unserved == {o.id for o in problem.orders}
    # every re-planned stop goes on a trip that starts after the clock
    for oid in open_:
        hit = res.plan.stop_of(oid)
        if hit:
            assert hit[1].depart >= clock


def test_urgent_order_is_placed(small):
    problem, plan = small
    ordered = {o.customer_id for o in problem.orders}
    cid = next(c for c in sorted(problem.customers) if c not in ordered)
    o = Order("U001", cid, 5, 250, 1, urgent=True, tw_start=660, tw_end=840)
    res = replan(problem, plan, Change("add_order", order=o), 600, 2.0)
    assert res.plan.stop_of("U001") is not None
    d = diff_plans(problem, plan, res.problem, res.plan)
    assert d["added"] == ["U001"]
    assert d["delta"]["served"] == 1


@pytest.mark.parametrize("clock", [None, 480])
def test_cancel_removes_stop(small, clock):
    problem, plan = small
    stop = plan.routes[0].trips[0].stops[-1]
    res = replan(problem, plan, Change("cancel", order_id=stop.order_id), clock, 2.0)
    assert res.plan.stop_of(stop.order_id) is None
    assert all(u["order_id"] != stop.order_id for u in res.plan.unserved)
    assert res.plan.kpis["hard_violations"] == 0


def test_new_window_is_respected_or_flagged(small):
    problem, plan = small
    stop = plan.routes[0].trips[0].stops[-1]
    res = replan(problem, plan, Change("set_window", order_id=stop.order_id, tw_start=780, tw_end=840), 480, 2.0)
    hit = res.plan.stop_of(stop.order_id)
    assert hit is not None
    assert hit[2].start >= 780
