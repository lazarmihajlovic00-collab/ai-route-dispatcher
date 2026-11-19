"""Plans, and the simulator that is the single source of truth for times, loads, costs and violations.

Every planner (OR-Tools, the baseline heuristic, the re-planner) only outputs a *skeleton*: for each
vehicle, a list of trips, each trip a departure time and an ordered list of orders. `evaluate` then
drives that skeleton through the network and computes everything else. The validator in the tests uses
the same simulation, so a plan cannot report numbers that its own stops do not produce.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .domain import Problem, Vehicle, hhmm
from .geo import Matrix, build_matrix

FUEL_L_PER_100KM = {"kombi": 10.0, "kamion_7_5t": 18.0, "kamion_12t": 25.0}
CO2_KG_PER_L_DIESEL = 2.65


# --------------------------------------------------------------------------------------------- network
class Network:
    """Matrix over depot (index 0) and every customer, built once per customer base."""

    def __init__(self, problem: Problem):
        self.ids = sorted(problem.customers)
        self.index = {cid: i + 1 for i, cid in enumerate(self.ids)}
        points = [(problem.depot.lat, problem.depot.lon)] + [
            (problem.customers[c].lat, problem.customers[c].lon) for c in self.ids]
        self.matrix: Matrix = build_matrix(points)

    def node(self, customer_id: str | None) -> int:
        return 0 if customer_id is None else self.index[customer_id]

    def dist(self, a: int, b: int) -> int:
        return self.matrix.dist[a][b]

    def time(self, a: int, b: int) -> int:
        return self.matrix.time[a][b]


_NETWORKS: dict[tuple, Network] = {}


def network_for(problem: Problem) -> Network:
    key = (problem.depot.lat, problem.depot.lon,
           tuple((c.id, c.lat, c.lon) for c in sorted(problem.customers.values(), key=lambda c: c.id)))
    net = _NETWORKS.get(key)
    if net is None:
        net = _NETWORKS[key] = Network(problem)
    return net


# ----------------------------------------------------------------------------------------- data types
@dataclass
class Stop:
    order_id: str
    customer_id: str
    arrival: int
    start: int
    depart: int
    late_min: int
    wait_min: int
    onboard_dp_after: int
    km_from_prev: float


@dataclass
class Trip:
    depart: int
    end: int  # back at the depot (or the breakdown time for a broken vehicle)
    stops: list[Stop]
    km: float
    load_dp: int
    load_kg: int
    pickup_dp: int


@dataclass
class Route:
    vehicle_id: int
    trips: list[Trip] = field(default_factory=list)
    broken_at: int | None = None
    km: float = 0.0
    cost: float = 0.0
    start: int | None = None
    end: int | None = None


@dataclass
class Plan:
    method: str
    routes: list[Route]
    unserved: list[dict]  # {"order_id", "reason"}
    kpis: dict
    violations: list[dict]
    skeleton: dict  # vehicle_id -> [[depart, [order ids]], ...], plus broken info

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Plan":
        routes = []
        for r in d["routes"]:
            trips = [Trip(**{**t, "stops": [Stop(**s) for s in t["stops"]]}) for t in r["trips"]]
            routes.append(Route(**{**r, "trips": trips}))
        return cls(d["method"], routes, d["unserved"], d["kpis"], d["violations"], d["skeleton"])

    def route_of(self, vehicle_id: int) -> Route | None:
        for r in self.routes:
            if r.vehicle_id == vehicle_id:
                return r
        return None

    def vehicle_of_order(self) -> dict[str, int]:
        out = {}
        for r in self.routes:
            for t in r.trips:
                for s in t.stops:
                    out[s.order_id] = r.vehicle_id
        return out

    def stop_of(self, order_id: str) -> tuple[Route, Trip, Stop] | None:
        for r in self.routes:
            for t in r.trips:
                for s in t.stops:
                    if s.order_id == order_id:
                        return r, t, s
        return None


# ------------------------------------------------------------------------------------------ simulation
def _simulate_trip(problem: Problem, net: Network, vehicle: Vehicle, depart: int, order_ids: list[str],
                   broken_at: int | None, violations: list[dict]) -> Trip:
    orders = [problem.order(o) for o in order_ids]
    load_dp = sum(o.deliver_dp for o in orders)
    load_kg = sum(o.deliver_kg for o in orders)
    pickup_dp = sum(o.pickup_dp for o in orders)
    if load_kg > vehicle.capacity_kg:
        violations.append({"type": "capacity_kg", "vehicle": vehicle.id,
                           "detail": f"{load_kg} kg > {vehicle.capacity_kg} kg"})
    if load_dp > vehicle.capacity_dp:
        violations.append({"type": "capacity_pallets", "vehicle": vehicle.id,
                           "detail": f"{load_dp / 10:.1f} > {vehicle.capacity_dp / 10:.1f} paleta na polasku"})
    t, pos, onboard, km = depart, 0, load_dp, 0.0
    stops: list[Stop] = []
    for o in orders:
        node = net.node(o.customer_id)
        arrival = t + net.time(pos, node)
        if broken_at is not None and arrival > broken_at:
            break
        tw_s, tw_e = problem.window(o)
        start = max(arrival, tw_s)
        late = max(0, start - tw_e)
        dep = start + o.service_min()
        if broken_at is not None and dep > broken_at:
            break  # the stop was not finished when the vehicle broke down
        onboard = onboard - o.deliver_dp + o.pickup_dp
        if onboard > vehicle.capacity_dp:
            violations.append({"type": "capacity_onboard", "vehicle": vehicle.id, "order": o.id,
                               "detail": f"{onboard / 10:.1f} paleta u vozilu posle stanice"})
        if not problem.allowed(o, vehicle):
            violations.append({"type": "access", "vehicle": vehicle.id, "order": o.id,
                               "detail": "vozilo nije dozvoljeno na ovoj lokaciji"})
        leg_km = net.dist(pos, node) / 1000
        km += leg_km
        stops.append(Stop(o.id, o.customer_id, arrival, start, dep, late, start - arrival, onboard,
                          round(leg_km, 2)))
        t, pos = dep, node
    if broken_at is not None and len(stops) < len(orders):
        end = broken_at
    else:
        km += net.dist(pos, 0) / 1000
        end = t + net.time(pos, 0)
    return Trip(depart, end, stops, round(km, 2), load_dp, load_kg, pickup_dp)


def evaluate(problem: Problem, skeleton: dict, method: str, unserved_reasons: dict[str, str] | None = None,
             allow_late: bool = False) -> Plan:
    """Simulate a skeleton. skeleton = {"routes": {vid: [[depart, [oids]], ...]}, "broken": {vid: minute}}."""
    net = network_for(problem)
    violations: list[dict] = []
    routes: list[Route] = []
    seen: dict[str, int] = {}
    broken = {int(k): v for k, v in skeleton.get("broken", {}).items()}
    for vid_raw, trips in sorted(skeleton["routes"].items(), key=lambda kv: int(kv[0])):
        vid = int(vid_raw)
        if not trips:
            continue
        vehicle = problem.vehicle(vid)
        route = Route(vid, broken_at=broken.get(vid))
        prev_end = None
        for k, (depart, oids) in enumerate(trips):
            if not oids:
                continue
            if prev_end is not None and depart < prev_end + problem.depot.reload_min:
                violations.append({"type": "reload", "vehicle": vid,
                                   "detail": f"tura {k + 1} krece u {hhmm(depart)}, pre kraja pretovara"})
            if depart < max(vehicle.shift_start, problem.depot.opens):
                violations.append({"type": "shift", "vehicle": vid, "detail": f"polazak u {hhmm(depart)}"})
            is_last = k == len(trips) - 1
            trip = _simulate_trip(problem, net, vehicle, depart, oids,
                                  route.broken_at if is_last else None, violations)
            route.trips.append(trip)
            prev_end = trip.end
            for s in trip.stops:
                if s.order_id in seen:
                    violations.append({"type": "duplicate", "order": s.order_id, "detail": "porudzbina 2 puta"})
                seen[s.order_id] = vid
                if s.late_min > 0 and not allow_late:
                    violations.append({"type": "late", "vehicle": vid, "order": s.order_id,
                                       "detail": f"kasni {s.late_min} min"})
        if not route.trips:
            continue
        route.start = route.trips[0].depart
        route.end = route.trips[-1].end
        if route.end > vehicle.shift_end:
            violations.append({"type": "shift", "vehicle": vid, "detail": f"kraj smene {hhmm(route.end)}"})
        if route.end - route.start > vehicle.max_duration:
            violations.append({"type": "duration", "vehicle": vid,
                               "detail": f"{(route.end - route.start) / 60:.1f} h"})
        route.km = round(sum(t.km for t in route.trips), 2)
        route.cost = round(route.km * vehicle.rsd_per_km + (route.end - route.start) / 60 * vehicle.crew_rsd_per_hour
                           + vehicle.fixed_rsd_per_day, 0)
        routes.append(route)

    reasons = unserved_reasons or {}
    served = set(seen)
    unserved = []
    for o in problem.orders:
        if o.id not in served:
            unserved.append({"order_id": o.id, "customer_id": o.customer_id,
                             "reason": reasons.get(o.id, "nije rasporedjena")})
    plan = Plan(method, routes, unserved, {}, violations,
                {"routes": {str(k): v for k, v in skeleton["routes"].items()},
                 "broken": {str(k): v for k, v in broken.items()}})
    plan.kpis = kpis(problem, plan)
    return plan


def kpis(problem: Problem, plan: Plan) -> dict:
    stops = [s for r in plan.routes for t in r.trips for s in t.stops]
    late = [s for s in stops if s.late_min > 0]
    used = [r for r in plan.routes if any(t.stops for t in r.trips)]
    fuel = sum(r.km * FUEL_L_PER_100KM[problem.vehicle(r.vehicle_id).kind] / 100 for r in plan.routes)
    km_cost = sum(r.km * problem.vehicle(r.vehicle_id).rsd_per_km for r in plan.routes)
    crew_cost = sum((r.end - r.start) / 60 * problem.vehicle(r.vehicle_id).crew_rsd_per_hour
                    for r in plan.routes if r.start is not None)
    fixed = sum(problem.vehicle(r.vehicle_id).fixed_rsd_per_day for r in used)
    util = []
    for r in plan.routes:
        v = problem.vehicle(r.vehicle_id)
        for t in r.trips:
            if t.stops:
                util.append(max(t.load_dp / v.capacity_dp, t.load_kg / v.capacity_kg))
    hard = [v for v in plan.violations if v["type"] != "late"]
    return {
        "orders": len(problem.orders),
        "served": len(stops),
        "unserved": len(plan.unserved),
        "vehicles_used": len(used),
        "trips": sum(1 for r in plan.routes for t in r.trips if t.stops),
        "km": round(sum(r.km for r in plan.routes), 1),
        "crew_hours": round(sum((r.end - r.start) / 60 for r in plan.routes if r.start is not None), 1),
        "cost_km_rsd": round(km_cost),
        "cost_crew_rsd": round(crew_cost),
        "cost_fixed_rsd": round(fixed),
        "cost_rsd": round(km_cost + crew_cost + fixed),
        "late_stops": len(late),
        "late_minutes": sum(s.late_min for s in late),
        "on_time_pct": round(100 * (len(stops) - len(late)) / len(stops), 1) if stops else 0.0,
        "avg_load_pct": round(100 * sum(util) / len(util), 1) if util else 0.0,
        "fuel_l": round(fuel, 1),
        "co2_kg": round(fuel * CO2_KG_PER_L_DIESEL, 1),
        "hard_violations": len(hard),
    }


def position_at(problem: Problem, plan: Plan, vehicle_id: int, minute: int) -> dict:
    """Where a vehicle is at a given minute (linear interpolation between stops), for the map."""
    net = network_for(problem)
    r = plan.route_of(vehicle_id)
    depot = (problem.depot.lat, problem.depot.lon)
    if r is None or r.start is None or minute <= r.start:
        return {"lat": depot[0], "lon": depot[1], "state": "u bazi"}
    if r.broken_at is not None and minute >= r.broken_at:
        last = _last_point(problem, r, r.broken_at, net)
        return {"lat": last[0], "lon": last[1], "state": "u kvaru"}
    for trip in r.trips:
        if minute < trip.depart:
            return {"lat": depot[0], "lon": depot[1], "state": "pretovar u bazi"}
        prev, prev_t = depot, trip.depart
        for s in trip.stops:
            c = problem.customers[s.customer_id]
            here = (c.lat, c.lon)
            if minute < s.arrival:
                f = (minute - prev_t) / max(1, s.arrival - prev_t)
                return _lerp(prev, here, f, "u voznji")
            if minute < s.depart:
                return {"lat": here[0], "lon": here[1], "state": f"istovar: {c.name}"}
            prev, prev_t = here, s.depart
        if minute < trip.end:
            f = (minute - prev_t) / max(1, trip.end - prev_t)
            return _lerp(prev, depot, f, "povratak u bazu")
    return {"lat": depot[0], "lon": depot[1], "state": "zavrsio"}


def _lerp(a, b, f, state):
    f = min(1.0, max(0.0, f))
    return {"lat": a[0] + (b[0] - a[0]) * f, "lon": a[1] + (b[1] - a[1]) * f, "state": state}


def _last_point(problem: Problem, r: Route, minute: int, net: Network):
    trip = r.trips[-1]
    if trip.stops:
        c = problem.customers[trip.stops[-1].customer_id]
        return (c.lat, c.lon)
    return (problem.depot.lat, problem.depot.lon)

