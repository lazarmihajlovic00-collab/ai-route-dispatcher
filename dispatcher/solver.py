"""Vehicle routing with Google OR-Tools.

Model (one "slot" = one trip a vehicle can make from the depot and back):
- heterogeneous fleet, cost per km and crew cost per hour differ by vehicle type, fixed cost per used vehicle
- capacity in kg (deliveries) and in pallet places, with returnable empties picked up along the way
- customer time windows (hard; in re-planning optionally soft with a cost per minute late)
- access restrictions: central Belgrade only up to 7.5 t, some streets van only
- shift: earliest start, latest return and maximum duration per vehicle
- orders can be left out only at a very high penalty, so the solver reports what does not fit instead of
  failing the whole plan
"""

from __future__ import annotations

from dataclasses import dataclass

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from .domain import Order, Problem, Vehicle
from .plan import network_for

SCALE = 10  # solver costs are in tenths of a dinar
DROP_PENALTY = 5_000_000 * SCALE  # 5M RSD: serving an order is always cheaper than dropping it
LATE_RSD_PER_MIN = 150  # soft windows in re-planning: price of one minute late (customer goodwill)
MOVE_PENALTY_RSD = 400  # re-planning: re-picking in the warehouse, new paperwork, telling the drivers


@dataclass
class Slot:
    vehicle: Vehicle
    earliest: int
    latest_end: int
    max_duration: int
    trip_no: int = 1


@dataclass
class SolveResult:
    trips: list[tuple[Slot, int, list[str]]]  # slot, departure minute, order ids in sequence
    dropped: list[str]
    objective: float
    status: str
    wall_s: float


def default_slots(problem: Problem) -> list[Slot]:
    return [Slot(v, max(v.shift_start, problem.depot.opens), v.shift_end, v.max_duration)
            for v in problem.available_vehicles()]


def solve(problem: Problem, orders: list[Order] | None = None, slots: list[Slot] | None = None,
          time_limit_s: float = 10.0, max_late_min: int = 0,
          initial: dict[int, list[str]] | None = None,
          previous_vehicle: dict[str, int] | None = None) -> SolveResult:
    """Solve the VRP.

    `initial` maps slot index -> order ids and is used as a warm start. `previous_vehicle` maps order id ->
    vehicle id in the current plan; moving an order to another vehicle then costs MOVE_PENALTY_RSD, so a
    re-plan changes only what it has to.
    """
    import time as _time

    t0 = _time.perf_counter()
    orders = list(problem.orders if orders is None else orders)
    slots = default_slots(problem) if slots is None else slots
    if not orders or not slots:
        return SolveResult([], [o.id for o in orders], 0.0, "EMPTY", 0.0)
    net = network_for(problem)
    nodes = [0] + [net.node(o.customer_id) for o in orders]  # local index -> network index
    n = len(nodes)
    manager = pywrapcp.RoutingIndexManager(n, len(slots), 0)
    routing = pywrapcp.RoutingModel(manager)

    service = [0] + [o.service_min() for o in orders]

    # --- arc costs per vehicle type -------------------------------------------------------------
    cost_cbs: dict[tuple, int] = {}
    prev = previous_vehicle or {}
    prev_local = [None] + [prev.get(o.id) for o in orders]
    for k, s in enumerate(slots):
        rate = s.vehicle.rsd_per_km
        key = (rate, s.vehicle.id if prev else None)
        if key not in cost_cbs:
            def cb(i, j, rate=rate, vid=s.vehicle.id):
                a, b = manager.IndexToNode(i), manager.IndexToNode(j)
                c = int(net.dist(nodes[a], nodes[b]) * rate * SCALE / 1000)
                if prev_local[b] is not None and prev_local[b] != vid:
                    c += MOVE_PENALTY_RSD * SCALE
                return c
            cost_cbs[key] = routing.RegisterTransitCallback(cb)
        routing.SetArcCostEvaluatorOfVehicle(cost_cbs[key], k)
        if s.trip_no == 1:
            routing.SetFixedCostOfVehicle(int(s.vehicle.fixed_rsd_per_day * SCALE), k)

    # --- time -------------------------------------------------------------------------------------
    def time_cb(i, j):
        a, b = manager.IndexToNode(i), manager.IndexToNode(j)
        return service[a] + net.time(nodes[a], nodes[b])

    time_idx = routing.RegisterTransitCallback(time_cb)
    routing.AddDimension(time_idx, 240, 24 * 60, False, "time")
    time_dim = routing.GetDimensionOrDie("time")
    for k, s in enumerate(slots):
        time_dim.CumulVar(routing.Start(k)).SetRange(s.earliest, s.latest_end)
        time_dim.CumulVar(routing.End(k)).SetRange(s.earliest, s.latest_end)
        time_dim.SetSpanUpperBoundForVehicle(s.max_duration, k)
        time_dim.SetSpanCostCoefficientForVehicle(int(s.vehicle.crew_rsd_per_hour * SCALE / 60), k)
        routing.AddVariableMinimizedByFinalizer(time_dim.CumulVar(routing.Start(k)))
        routing.AddVariableMinimizedByFinalizer(time_dim.CumulVar(routing.End(k)))
    for li, o in enumerate(orders, start=1):
        idx = manager.NodeToIndex(li)
        tw_s, tw_e = problem.window(o)
        if max_late_min > 0:
            time_dim.CumulVar(idx).SetRange(tw_s, tw_e + max_late_min)
            time_dim.SetCumulVarSoftUpperBound(idx, tw_e, LATE_RSD_PER_MIN * SCALE)
        else:
            time_dim.CumulVar(idx).SetRange(tw_s, tw_e)

    # --- capacity: kg delivered, pallets on board with pickups ----------------------------------
    kg = [0] + [o.deliver_kg for o in orders]
    kg_idx = routing.RegisterUnaryTransitCallback(lambda i: kg[manager.IndexToNode(i)])
    routing.AddDimensionWithVehicleCapacity(kg_idx, 0, [s.vehicle.capacity_kg for s in slots], True, "kg")

    delta = [0] + [o.pickup_dp - o.deliver_dp for o in orders]
    pick = [0] + [o.pickup_dp for o in orders]
    caps = [s.vehicle.capacity_dp for s in slots]
    onboard_idx = routing.RegisterUnaryTransitCallback(lambda i: delta[manager.IndexToNode(i)])
    # Start cumul is free: it is what the truck loads at the depot. On board must stay in [0, cap].
    routing.AddDimensionWithVehicleCapacity(onboard_idx, 0, caps, False, "onboard")
    pick_idx = routing.RegisterUnaryTransitCallback(lambda i: pick[manager.IndexToNode(i)])
    routing.AddDimensionWithVehicleCapacity(pick_idx, 0, caps, True, "pickups")
    onboard = routing.GetDimensionOrDie("onboard")
    pickups = routing.GetDimensionOrDie("pickups")
    solver = routing.solver()
    for k in range(len(slots)):
        # load at the end = load at start - all deliveries + all pickups, and it must be at least all
        # pickups, so the start load covers every delivery on this trip.
        solver.Add(onboard.CumulVar(routing.End(k)) >= pickups.CumulVar(routing.End(k)))

    # --- access restrictions and optional orders ------------------------------------------------
    for li, o in enumerate(orders, start=1):
        idx = manager.NodeToIndex(li)
        banned = [k for k, s in enumerate(slots) if not problem.allowed(o, s.vehicle)]
        if banned:
            routing.VehicleVar(idx).RemoveValues(banned)
        weight = 3 if o.urgent else (2 if problem.customer_of(o).key_account else 1)
        routing.AddDisjunction([idx], DROP_PENALTY * weight)

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.FromMilliseconds(int(time_limit_s * 1000))

    solution = None
    if initial:
        local = {o.id: li for li, o in enumerate(orders, start=1)}
        routes = [[local[oid] for oid in initial.get(k, []) if oid in local] for k in range(len(slots))]
        start = routing.ReadAssignmentFromRoutes(routes, True)
        if start is not None:
            solution = routing.SolveFromAssignmentWithParameters(start, params)
    if solution is None:
        solution = routing.SolveWithParameters(params)
    wall = _time.perf_counter() - t0
    if solution is None:
        return SolveResult([], [o.id for o in orders], 0.0, "NO_SOLUTION", wall)

    trips = []
    served = set()
    for k, s in enumerate(slots):
        idx = routing.Start(k)
        seq = []
        depart = solution.Value(time_dim.CumulVar(idx))
        idx = solution.Value(routing.NextVar(idx))
        while not routing.IsEnd(idx):
            o = orders[manager.IndexToNode(idx) - 1]
            seq.append(o.id)
            served.add(o.id)
            idx = solution.Value(routing.NextVar(idx))
        if seq:
            # Leave as late as possible while keeping the first stop's arrival, to cut waiting.
            first = orders[[o.id for o in orders].index(seq[0])]
            arrive_first = solution.Value(time_dim.CumulVar(manager.NodeToIndex(
                [o.id for o in orders].index(seq[0]) + 1)))
            travel = net.time(0, net.node(first.customer_id))
            depart = max(depart, min(arrive_first - travel, s.latest_end))
            depart = max(depart, s.earliest)
            trips.append((s, depart, seq))
    dropped = [o.id for o in orders if o.id not in served]
    return SolveResult(trips, dropped, solution.ObjectiveValue() / SCALE, "OK", wall)


def skeleton_from(result: SolveResult) -> dict:
    routes: dict[int, list] = {}
    for slot, depart, seq in sorted(result.trips, key=lambda t: (t[0].vehicle.id, t[1])):
        routes.setdefault(slot.vehicle.id, []).append([depart, seq])
    return {"routes": routes, "broken": {}}


def add_second_trips(problem: Problem, skeleton: dict, dropped: list[str], time_limit_s: float = 3.0,
                     not_before: int = 0, max_late_min: int = 0) -> tuple[dict, list[str]]:
    """Orders that did not fit in one trip per vehicle get second trips: a vehicle comes back, reloads and
    goes out again. Returns the extended skeleton and the orders that still do not fit."""
    from .plan import evaluate

    if not dropped:
        return skeleton, []
    interim = evaluate(problem, skeleton, "interim", allow_late=True)
    ends = {r.vehicle_id: (r.start, r.end) for r in interim.routes}
    broken = {int(k) for k in skeleton.get("broken", {})}
    slots = []
    for v in problem.available_vehicles():
        if v.id in broken:
            continue
        start0, end0 = ends.get(v.id, (None, None))
        ready = max(not_before, (end0 if end0 is not None else max(v.shift_start, problem.depot.opens)))
        ready += problem.depot.reload_min if end0 is not None else 0
        max_dur = v.max_duration - (ready - start0) if start0 is not None else v.max_duration
        if ready < v.shift_end and max_dur > 30:
            slots.append(Slot(v, ready, v.shift_end, max_dur, trip_no=2 if end0 is not None else 1))
    orders = [problem.order(o) for o in dropped]
    res = solve(problem, orders=orders, slots=slots, time_limit_s=time_limit_s, max_late_min=max_late_min)
    routes = {int(k): list(v) for k, v in skeleton["routes"].items()}
    for slot, depart, seq in res.trips:
        routes.setdefault(slot.vehicle.id, []).append([depart, seq])
    return {"routes": routes, "broken": skeleton.get("broken", {})}, res.dropped


def plan_day(problem: Problem, time_limit_s: float = 10.0):
    """Full plan for the day from scratch: one trip per vehicle, then second trips for what is left."""
    from .plan import evaluate

    res = solve(problem, time_limit_s=time_limit_s)
    skeleton, still = add_second_trips(problem, skeleton_from(res), res.dropped)
    reasons = {oid: "ne staje u kapacitete / vremenske prozore" for oid in still}
    plan = evaluate(problem, skeleton, "or-tools", reasons)
    plan.kpis["solve_s"] = round(res.wall_s, 2)
    return plan
