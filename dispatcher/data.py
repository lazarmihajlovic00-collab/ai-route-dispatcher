"""Synthetic but realistic data for a beverage distributor around Belgrade.

The company, customers and names are fictional. Town and neighbourhood coordinates are real
(approximate centroids), and customers are scattered around them. Demand, time windows, access
restrictions and costs follow how beverage distribution works in Serbia: HoReCa customers want
deliveries before they open, supermarkets receive at the ramp early in the morning, central Belgrade
does not allow heavy trucks, and empty crates and kegs go back to the depot.
"""

from __future__ import annotations

import random

from .domain import Access, Customer, Depot, Order, Problem, Vehicle

DEPOT = Depot(name="Distributivni centar Šimanovci", lat=44.8735, lon=20.0920)

# (area, lat, lon, spread_km, weight, access rule)
AREAS = [
    ("Stari grad", 44.8186, 20.4590, 0.9, 9, "center"),
    ("Vračar", 44.7985, 20.4770, 0.9, 7, "center"),
    ("Savski venac", 44.7955, 20.4520, 0.9, 4, "center"),
    ("Palilula", 44.8140, 20.4880, 1.2, 4, "mixed"),
    ("Novi Beograd", 44.8110, 20.4100, 1.8, 10, "any"),
    ("Zemun", 44.8430, 20.4010, 1.4, 7, "mixed"),
    ("Zvezdara", 44.7950, 20.5100, 1.3, 5, "any"),
    ("Voždovac", 44.7700, 20.4800, 1.4, 5, "any"),
    ("Čukarica", 44.7810, 20.4150, 1.3, 4, "any"),
    ("Rakovica", 44.7400, 20.4440, 1.1, 3, "any"),
    ("Karaburma", 44.8130, 20.5150, 0.9, 2, "any"),
    ("Borča", 44.8700, 20.4600, 1.2, 2, "any"),
    ("Surčin", 44.7930, 20.2800, 1.0, 2, "any"),
    ("Batajnica", 44.9000, 20.2800, 1.0, 2, "any"),
    ("Obrenovac", 44.6550, 20.2000, 1.2, 3, "any"),
    ("Pančevo", 44.8710, 20.6400, 1.6, 5, "any"),
    ("Smederevo", 44.6650, 20.9300, 1.5, 4, "any"),
    ("Grocka", 44.6720, 20.7180, 1.0, 2, "any"),
    ("Stara Pazova", 44.9850, 20.1600, 1.0, 2, "any"),
    ("Inđija", 45.0480, 20.0820, 1.0, 2, "any"),
    ("Ruma", 45.0080, 19.8220, 1.0, 2, "any"),
    ("Novi Sad", 45.2550, 19.8450, 2.0, 6, "any"),
    ("Lazarevac", 44.3800, 20.2570, 1.0, 2, "any"),
    ("Mladenovac", 44.4400, 20.6950, 1.0, 2, "any"),
]

# type: (share, pallets min/max, kg per pallet min/max, empties share of delivery, window, key-account chance)
TYPES = {
    "kafic": (0.30, 0.1, 0.4, 450, 650, (0.4, 0.8), (420, 660), 0.03),
    "restoran": (0.16, 0.15, 0.5, 450, 650, (0.3, 0.7), (480, 720), 0.05),
    "hotel": (0.05, 0.2, 0.8, 450, 650, (0.3, 0.6), (480, 780), 0.4),
    "market": (0.22, 0.2, 0.8, 500, 650, (0.2, 0.5), (420, 780), 0.05),
    "supermarket": (0.12, 0.6, 2.0, 500, 650, (0.2, 0.4), (360, 600), 0.6),
    "trafika": (0.10, 0.05, 0.2, 400, 600, (0.0, 0.2), (420, 900), 0.0),
    "veleprodaja": (0.05, 1.2, 2.8, 550, 650, (0.1, 0.3), (420, 840), 0.3),
}

TYPE_LABEL = {"kafic": "Kafić", "restoran": "Restoran", "hotel": "Hotel", "market": "Market",
              "supermarket": "Supermarket", "trafika": "Trafika", "veleprodaja": "Veleprodaja"}
NAMES = ["Lipa", "Dunav", "Zlatni klas", "Kod Mike", "Sunce", "Avala", "Kosmaj", "Stari most", "Bela lađa",
         "Jelena", "Dva jelena", "Plavi točak", "Morava", "Kalemegdan", "Sava", "Fruška", "Vidikovac",
         "Kod Steve", "Ljubica", "Tri šešira", "Kafana Šumadija", "Zora", "Jasen", "Breza", "Bor", "Kesten",
         "Topola", "Bulevar", "Promenada", "Kej", "Ada", "Košutnjak", "Majdan", "Lastavica", "Gnezdo",
         "Čarda", "Salaš", "Kod Lale", "Pod lipom", "Ribar", "Splav", "Galerija", "Centar", "Most",
         "Kvart", "Ugao", "Kod Joce", "Kod Bate", "Dorćolac", "Zemunac", "Vojvodina", "Srem", "Banat",
         "Pivnica", "Podrum", "Bašta", "Terasa", "Mlin", "Bunar", "Toranj"]

FLEET = [
    # id, kind, plate, kg, dp, rsd/km, crew rsd/h, fixed rsd/day
    (1, "kamion_12t", "BG-1201-DP", 6500, 150, 72.0, 2100.0, 9500.0),
    (2, "kamion_12t", "BG-1202-DP", 6500, 150, 72.0, 2100.0, 9500.0),
    (3, "kamion_7_5t", "BG-0751-DP", 3800, 100, 52.0, 2100.0, 7000.0),
    (4, "kamion_7_5t", "BG-0752-DP", 3800, 100, 52.0, 2100.0, 7000.0),
    (5, "kamion_7_5t", "BG-0753-DP", 3800, 100, 52.0, 2100.0, 7000.0),
    (6, "kamion_7_5t", "BG-0754-DP", 3800, 100, 52.0, 2100.0, 7000.0),
    (7, "kombi", "BG-0351-DP", 1300, 40, 30.0, 1150.0, 4000.0),
    (8, "kombi", "BG-0352-DP", 1300, 40, 30.0, 1150.0, 4000.0),
    (9, "kombi", "BG-0353-DP", 1300, 40, 30.0, 1150.0, 4000.0),
]


def _jitter(rng: random.Random, lat: float, lon: float, spread_km: float) -> tuple[float, float]:
    dlat = rng.gauss(0, spread_km / 2) / 111.0
    dlon = rng.gauss(0, spread_km / 2) / 79.0  # km per degree of longitude at ~44.8 N
    return round(lat + dlat, 5), round(lon + dlon, 5)


def make_customers(seed: int = 7, n: int = 220) -> dict[str, Customer]:
    rng = random.Random(seed)
    areas = [a for a in AREAS]
    weights = [a[4] for a in areas]
    types = list(TYPES)
    type_weights = [TYPES[t][0] for t in types]
    used_names: set[str] = set()
    customers: dict[str, Customer] = {}
    for i in range(1, n + 1):
        area = rng.choices(areas, weights)[0]
        name_area, lat, lon, spread, _, rule = area
        ctype = rng.choices(types, type_weights)[0]
        if rule == "center" and ctype in ("veleprodaja", "supermarket") and rng.random() < 0.7:
            ctype = "market"
        _, _, _, _, _, _, (ws, we), key_p = TYPES[ctype]
        clat, clon = _jitter(rng, lat, lon, spread)
        if rule == "center":
            access = Access.VAN_ONLY if rng.random() < 0.15 else Access.MAX_7_5T
        elif rule == "mixed":
            access = Access.MAX_7_5T if rng.random() < 0.4 else Access.ANY
        else:
            access = Access.ANY
        # Window: type default, sometimes shifted or narrowed by the customer.
        shift = rng.choice([-60, 0, 0, 0, 60])
        tw_start = max(360, ws + shift)
        tw_end = min(900, we + shift)
        if rng.random() < 0.25:  # narrow 2-hour window
            tw_end = min(tw_end, tw_start + 120)
        while True:
            base = f"{TYPE_LABEL[ctype]} {rng.choice(NAMES)}"
            name = base if base not in used_names else f"{base} {name_area}"
            if name not in used_names:
                break
            name = f"{base} {i}"
            break
        used_names.add(name)
        cid = f"C{i:03d}"
        customers[cid] = Customer(cid, name, ctype, name_area, clat, clon, int(access), tw_start, tw_end,
                                  rng.random() < key_p)
    return customers


def make_orders(customers: dict[str, Customer], seed: int, n_orders: int) -> list[Order]:
    rng = random.Random(seed)
    ids = sorted(customers)
    chosen = rng.sample(ids, min(n_orders, len(ids)))
    orders = []
    for k, cid in enumerate(sorted(chosen), start=1):
        c = customers[cid]
        _, pmin, pmax, kgmin, kgmax, (emin, emax), _, _ = TYPES[c.type]
        pallets = rng.uniform(pmin, pmax)
        dp = max(1, round(pallets * 10))
        if c.access == Access.VAN_ONLY:
            dp = min(dp, 12)
        kg = int(dp / 10 * rng.uniform(kgmin, kgmax))
        pickup = round(dp * rng.uniform(emin, emax))
        orders.append(Order(f"O{k:03d}", cid, dp, kg, pickup))
    return orders


def make_fleet() -> list[Vehicle]:
    return [Vehicle(i, kind, plate, kg, dp, km, crew, fixed) for i, kind, plate, kg, dp, km, crew, fixed in FLEET]


def make_problem(seed: int = 1, n_orders: int = 95, day: str = "2026-11-20") -> Problem:
    """One delivery day. The customer base is fixed (seed 7); `seed` picks who ordered what today."""
    customers = make_customers()
    orders = make_orders(customers, seed, n_orders)
    # Keep every customer, not only today's: urgent orders may come from customers who did not order.
    return Problem(day=day, depot=DEPOT, vehicles=make_fleet(), customers=customers, orders=orders)
