"""Distances and travel times.

Default provider: great-circle distance times a road circuity factor, with a speed profile by trip
length (short city hops are slow, long legs use the highway). It needs no network and is good enough to
compare plans with each other. For real operations use the OSRM provider (road network from
OpenStreetMap): set DISTANCE_PROVIDER=osrm and OSRM_URL to your own OSRM server.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass

CIRCUITY = 1.32  # road km per straight-line km, typical for Serbian city + regional roads
# (segment length km, average speed km/h): first 4 km in city traffic, then arterial, then highway
SPEED_PROFILE = [(4.0, 22.0), (10.0, 36.0), (26.0, 58.0), (math.inf, 78.0)]


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def travel_minutes(road_m: float) -> float:
    km = road_m / 1000
    minutes = 0.0
    for seg, speed in SPEED_PROFILE:
        part = min(km, seg)
        minutes += part / speed * 60
        km -= part
        if km <= 0:
            break
    return minutes


@dataclass
class Matrix:
    """Road distance (m) and travel time (min) between points, index 0 is the depot."""

    dist: list[list[int]]
    time: list[list[int]]


def build_matrix(points: list[tuple[float, float]]) -> Matrix:
    if os.environ.get("DISTANCE_PROVIDER") == "osrm":
        return osrm_matrix(points, os.environ.get("OSRM_URL", "http://localhost:5000"))
    n = len(points)
    dist = [[0] * n for _ in range(n)]
    time = [[0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            d = haversine_m(*points[i], *points[j]) * CIRCUITY
            t = travel_minutes(d)
            dist[i][j] = dist[j][i] = int(round(d))
            time[i][j] = time[j][i] = max(1, int(round(t)))
    return Matrix(dist, time)


def osrm_matrix(points: list[tuple[float, float]], base_url: str, client=None) -> Matrix:
    """Road distances from an OSRM server (table service). `client` is injectable for tests."""
    import httpx

    coords = ";".join(f"{lon:.5f},{lat:.5f}" for lat, lon in points)
    http = client or httpx.Client(timeout=60)
    resp = http.get(f"{base_url.rstrip('/')}/table/v1/driving/{coords}", params={"annotations": "distance,duration"})
    resp.raise_for_status()
    body = resp.json()
    if body.get("code") != "Ok":
        raise RuntimeError(f"OSRM error: {body.get('code')}")
    dist = [[int(round(x or 0)) for x in row] for row in body["distances"]]
    time = [[max(0, int(round((x or 0) / 60))) for x in row] for row in body["durations"]]
    return Matrix(dist, time)
