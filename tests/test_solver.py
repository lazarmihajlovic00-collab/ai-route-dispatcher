"""Optimizer: constraints hold, and the simulator catches violations."""

from dispatcher.baseline import plan_baseline
from dispatcher.domain import Access, Customer, Depot, Order, Problem, Vehicle
from dispatcher.plan import evaluate
from dispatcher.solver import plan_day


def independent_check(problem, plan):
    """Re-check every route without using the simulator's own violation list."""
    for r in plan.routes:
        v = problem.vehicle(r.vehicle_id)
        assert r.end <= v.shift_end and r.end - r.start <= v.max_duration
        for t in r.trips:
            orders = [problem.order(s.order_id) for s in t.stops]
            assert sum(o.deliver_kg for o in orders) <= v.capacity_kg
            onboard = sum(o.deliver_dp for o in orders)
            assert onboard <= v.capacity_dp
            for o, s in zip(orders, t.stops):
                onboard += o.pickup_dp - o.deliver_dp
                assert 0 <= onboard <= v.capacity_dp
                ws, we = problem.window(o)
                assert ws <= s.start <= we, (o.id, s.start, ws, we)
                assert problem.allowed(o, v)


def test_plan_day_is_feasible_and_complete(small):
    problem, plan = small
    assert plan.kpis["hard_violations"] == 0
    assert plan.kpis["late_stops"] == 0
    assert plan.kpis["served"] + plan.kpis["unserved"] == len(problem.orders)
    assert plan.kpis["unserved"] == 0
    served = [s.order_id for r in plan.routes for t in r.trips for s in t.stops]
    assert len(served) == len(set(served))
    independent_check(problem, plan)


def test_optimizer_beats_baseline(small):
    problem, plan = small
    base = plan_baseline(problem)
    assert base.kpis["hard_violations"] == 0
    assert plan.kpis["cost_rsd"] < base.kpis["cost_rsd"]
    assert plan.kpis["late_stops"] <= base.kpis["late_stops"]


def _tiny(orders, access=Access.ANY, cap_dp=10):
    depot = Depot("D", 44.80, 20.40)
    vans = [Vehicle(1, "kombi", "X", 2000, cap_dp, 30, 1000, 1000)]
    customers = {
        "A": Customer("A", "A", "market", "T", 44.81, 20.41, int(access), 360, 900),
        "B": Customer("B", "B", "market", "T", 44.82, 20.42, Access.ANY, 360, 900),
    }
    return Problem("2026-11-20", depot, vans, customers, orders)


def test_returnable_empties_force_delivery_first():
    # Visiting B first would have 10 - 2 + 8 = 16 tenths on board > 10: the solver must go A then B.
    orders = [Order("OA", "A", 8, 400, 0), Order("OB", "B", 2, 100, 8)]
    p = _tiny(orders)
    # make B the nearer stop, so distance alone would pick B first
    p.customers["A"].lat, p.customers["A"].lon = 44.86, 20.48
    plan = plan_day(p, 1.0)
    seq = [s.order_id for s in plan.routes[0].trips[0].stops]
    assert seq == ["OA", "OB"]
    assert plan.kpis["hard_violations"] == 0


def test_access_restriction_respected():
    p = _tiny([Order("OA", "A", 2, 100, 0)], access=Access.VAN_ONLY)
    p.vehicles.append(Vehicle(2, "kamion_12t", "Y", 6500, 150, 10, 500, 100))  # cheaper, but banned
    plan = plan_day(p, 1.0)
    assert plan.vehicle_of_order()["OA"] == 1


def test_simulator_reports_violations():
    p = _tiny([Order("OA", "A", 8, 400, 0), Order("OB", "B", 2, 100, 8)])
    bad = evaluate(p, {"routes": {1: [[360, ["OB", "OA"]]]}, "broken": {}}, "manual")
    assert any(v["type"] == "capacity_onboard" for v in bad.violations)
    p2 = _tiny([Order("OA", "A", 2, 100, 0)], access=Access.VAN_ONLY)
    p2.vehicles.append(Vehicle(2, "kamion_12t", "Y", 6500, 150, 10, 500, 100))
    bad2 = evaluate(p2, {"routes": {2: [[360, ["OA"]]]}, "broken": {}}, "manual")
    assert any(v["type"] == "access" for v in bad2.violations)
    p.customers["A"].tw_end = 361
    late = evaluate(p, {"routes": {1: [[360, ["OA"]]]}, "broken": {}}, "manual")
    assert any(v["type"] == "late" for v in late.violations)
