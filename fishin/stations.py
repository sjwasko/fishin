"""NOAA tide station catalog & nearest-station lookup.

Fetches the NOAA CO-OPS metadata catalog once, caches it to
`~/.cache/fishin/stations.json`, then resolves arbitrary lat/lon to the
nearest tide-prediction station via haversine distance.

The full catalog is ~1500 stations and a few hundred KB — easy to keep on disk.
"""

from __future__ import annotations

import json
import math
import os
import urllib.request
from pathlib import Path


NOAA_STATION_INDEX = (
    "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations.json"
    "?type=tidepredictions"
)


def _cache_path() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "fishin" / "stations.json"


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def load_stations(refresh: bool = False, timeout: float = 15.0) -> list[dict]:
    """Return the cached station list, fetching from NOAA if missing/stale."""
    path = _cache_path()
    if not refresh and path.exists():
        with path.open() as f:
            return json.load(f)

    with urllib.request.urlopen(NOAA_STATION_INDEX, timeout=timeout) as resp:
        data = json.load(resp)

    stations = []
    for s in data.get("stations", []):
        try:
            stations.append({
                "id": str(s["id"]),
                "name": s.get("name", ""),
                "state": s.get("state", ""),
                "lat": float(s["lat"]),
                "lng": float(s["lng"]),
            })
        except (KeyError, TypeError, ValueError):
            continue

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        json.dump(stations, f)
    return stations


def nearest_station(lat: float, lon: float, refresh: bool = False) -> dict:
    """Return the closest tide-prediction station with distance in km."""
    stations = load_stations(refresh=refresh)
    if not stations:
        raise LookupError("no NOAA stations available")
    best = min(stations, key=lambda s: haversine_km(lat, lon, s["lat"], s["lng"]))
    best = dict(best)
    best["distance_km"] = haversine_km(lat, lon, best["lat"], best["lng"])
    return best
