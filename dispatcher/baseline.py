"""Baseline: how a dispatcher plans without an optimizer.

Territories by direction from the depot (sweep), vehicles filled one after another until weight, pallets
or the shift is used up, stops ordered by nearest neighbour, everybody leaves at 06:00. Access rules and
capacity are respected (a dispatcher knows them), time windows are not checked stop by stop, so late
arrivals are measured instead. This is a reasonable heuristic, not a straw man, but it is a proxy: the
real comparison needs a company's actual plans.
"""

from __future__ import annotations

import math

from .domain import Order, Problem
from .plan import Plan, evaluate, network_for


def _angle(problem: Problem, o: Order) -> float:
    c = problem.customer_of(o)
    return math.atan2(c.lat - problem.depot.lat, (c.lon - problem.depot.lon) * 0.71)


def _nn_order(problem: Problem, orders: list[Order]) -> list[Order]:
    net = network_for(problem)
    left = list(orders)
    seq, pos = [], 0
    while left:
        nxt = min(left, key=lambda o: net.dist(pos, net.node(o.customer_id)))
        seq.append(nxt)
        left.remove(nxt)
        pos = net.node(nxt.customer_id)
    return seq


def _route_end(problem: Problem, orders: list[Order], depart: int) -> int:
    net = network_for(problem)
    t, pos = depart, 0
    for o in _nn_order(problem, orders):
        node = net.node(o.customer_id)
        t += net.time(pos, node)
        t = max(t, problem.window(o)[0]) + o.service_min()
        pos = node
    return t + net.time(pos, 0)


def plan_baseline(problem: Problem) -> Plan:
    vehicles = sorted(problem.available_vehicles(), key=lambda v: (-v.capacity_kg, v.id))
    small_first = sorted(vehicles, key=lambda v: (v.vclass, v.id))
    assigned: dict[int, list[Order]] = {v.id: [] for v in vehicles}

    def fits(v, cur: list[Order], o: Order) -> bool:
        kg = sum(x.deliver_kg for x in cur) + o.deliver_kg
        dp = sum(x.deliver_dp for x in cur) + o.deliver_dp
        pk = sum(x.pickup_dp for x in cur) + o.pickup_dp
        if kg > v.capacity_kg or dp > v.capacity_dp or pk > v.capacity_dp or not problem.allowed(o, v):
            return False
        depart = max(v.shift_start, problem.depot.opens)
        end = _route_end(problem, cur + [o], depart)
        return end <= v.shift_end and end - depart <= v.max_duration

    # restricted customers (central zone) go to the smallest vehicles first, by sweep
    restricted = sorted([o for o in problem.orders if problem.customer_of(o).access < 3],
                        key=lambda o: (problem.customer_of(o).access, _angle(problem, o)))
    other = sorted([o for o in problem.orders if problem.customer_of(o).access == 3],
                   key=lambda o: _angle(problem, o))
    unplaced: list[Order] = []
    for group, order_vs in ((restricted, small_first), (other, vehicles)):
        vi = 0
        for o in group:
            placed = False
            for k in range(vi, len(order_vs)):
                v = order_vs[k]
                if fits(v, assigned[v.id], o):
                    assigned[v.id].append(o)
                    vi = k  # keep filling this vehicle: territories stay contiguous
                    placed = True
                    break
            if not placed:
                # try any vehicle with room before giving up
                for v in order_vs:
                    if fits(v, assigned[v.id], o):
                        assigned[v.id].append(o)
                        placed = True
                        break
            if not placed:
                unplaced.append(o)
    routes = {}
    for v in vehicles:
        if assigned[v.id]:
            depart = max(v.shift_start, problem.depot.opens)
            routes[v.id] = [[depart, [o.id for o in _nn_order(problem, assigned[v.id])]]]
    reasons = {o.id: "nema mesta ni u jednom vozilu" for o in unplaced}
    return evaluate(problem, {"routes": routes, "broken": {}}, "baseline", reasons, allow_late=True)
