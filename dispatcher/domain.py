"""Domain model: depot, vehicles, customers, orders, and the routing problem for one day.

Units used everywhere:
- time: minutes after midnight (int), so 06:30 is 390
- distance: metres (int)
- load: pallets in tenths, called "dp" (int), so 1.5 pallets is 15 dp
- weight: kg (int)
- money: RSD (float in reports; the solver works in tenths of a dinar)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import IntEnum


class Access(IntEnum):
    """Largest vehicle class allowed at a customer."""

    VAN_ONLY = 1  # narrow streets, pedestrian zones (e.g. Skadarlija)
    MAX_7_5T = 2  # central Belgrade truck restriction
    ANY = 3


VEHICLE_CLASS = {"kombi": 1, "kamion_7_5t": 2, "kamion_12t": 3}
VEHICLE_LABEL = {"kombi": "kombi 3,5 t", "kamion_7_5t": "kamion 7,5 t", "kamion_12t": "kamion 12 t"}


@dataclass
class Depot:
    name: str
    lat: float
    lon: float
    opens: int = 330  # 05:30
    reload_min: int = 30  # time to reload a vehicle for a second trip


@dataclass
class Vehicle:
    id: int
    kind: str  # kombi | kamion_7_5t | kamion_12t
    plate: str
    capacity_kg: int
    capacity_dp: int
    rsd_per_km: float  # fuel, tyres, maintenance
    crew_rsd_per_hour: float  # driver (+ helper on trucks), gross cost
    fixed_rsd_per_day: float  # leasing, insurance, registration, when the vehicle is used
    shift_start: int = 360  # 06:00
    shift_end: int = 960  # 16:00
    max_duration: int = 600  # 10 h

    @property
    def vclass(self) -> int:
        return VEHICLE_CLASS[self.kind]

    @property
    def label(self) -> str:
        return f"Vozilo {self.id} ({VEHICLE_LABEL[self.kind]})"


@dataclass
class Customer:
    id: str
    name: str
    type: str  # kafic, restoran, hotel, market, supermarket, trafika, veleprodaja
    town: str
    lat: float
    lon: float
    access: int = Access.ANY
    tw_start: int = 420
    tw_end: int = 720
    key_account: bool = False


@dataclass
class Order:
    id: str
    customer_id: str
    deliver_dp: int
    deliver_kg: int
    pickup_dp: int = 0  # returnable empties: crates, kegs
    urgent: bool = False
    # Optional per-order time window override (a customer can ask for another slot today).
    tw_start: int | None = None
    tw_end: int | None = None

    def service_min(self) -> int:
        # 8 min to park and hand over documents, about 4 min per pallet unloaded, 2 per pallet of empties.
        return 8 + round(4 * self.deliver_dp / 10) + round(2 * self.pickup_dp / 10)


@dataclass
class Problem:
    """Everything the planner needs for one delivery day."""

    day: str
    depot: Depot
    vehicles: list[Vehicle]
    customers: dict[str, Customer]
    orders: list[Order]
    unavailable_vehicles: list[int] = field(default_factory=list)

    def customer_of(self, order: Order) -> Customer:
        return self.customers[order.customer_id]

    def window(self, order: Order) -> tuple[int, int]:
        c = self.customer_of(order)
        return (order.tw_start if order.tw_start is not None else c.tw_start,
                order.tw_end if order.tw_end is not None else c.tw_end)

    def vehicle(self, vid: int) -> Vehicle:
        for v in self.vehicles:
            if v.id == vid:
                return v
        raise KeyError(f"nepoznato vozilo {vid}")

    def order(self, oid: str) -> Order:
        for o in self.orders:
            if o.id == oid:
                return o
        raise KeyError(f"nepoznata porudzbina {oid}")

    def available_vehicles(self) -> list[Vehicle]:
        return [v for v in self.vehicles if v.id not in self.unavailable_vehicles]

    def allowed(self, order: Order, vehicle: Vehicle) -> bool:
        return vehicle.vclass <= self.customer_of(order).access

    # --- serialisation -------------------------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "day": self.day,
            "depot": asdict(self.depot),
            "vehicles": [asdict(v) for v in self.vehicles],
            "customers": [asdict(c) for c in self.customers.values()],
            "orders": [asdict(o) for o in self.orders],
            "unavailable_vehicles": list(self.unavailable_vehicles),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Problem":
        return cls(
            day=d["day"],
            depot=Depot(**d["depot"]),
            vehicles=[Vehicle(**v) for v in d["vehicles"]],
            customers={c["id"]: Customer(**c) for c in d["customers"]},
            orders=[Order(**o) for o in d["orders"]],
            unavailable_vehicles=list(d.get("unavailable_vehicles", [])),
        )


def hhmm(minutes: int | float) -> str:
    m = int(round(minutes))
    return f"{m // 60:02d}:{m % 60:02d}"


def parse_hhmm(text: str) -> int:
    text = text.strip().lower().replace("h", "").replace(".", ":")
    if ":" in text:
        h, m = text.split(":", 1)
        return int(h) * 60 + int(m or 0)
    return int(text) * 60
