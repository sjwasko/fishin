"""Disk-backed response cache for tide & weather fetches.

`diskcache` gives us a per-entry-TTL shelf with zero ceremony. By default it
serializes values with **pickle**, which would let a local actor who can write
to `~/.cache/fishin/` craft a malicious blob and gain arbitrary code execution
on the next run. We avoid that by passing a JSON-based `Disk` subclass —
JSON is data-only, no eval surface.

Cached values nest `datetime` objects (tide events, weather samples). The
custom `FishinDisk` round-trips those via a `{"__dt__": iso}` tag so the rest
of the codebase keeps receiving real datetimes.

Tide predictions are deterministic and cached for a year; weather forecasts
get 1h for today/future and a long TTL for past dates (forecasts freeze).
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

import diskcache
from diskcache import UNKNOWN


YEAR_SECONDS = 365 * 24 * 3600
HOUR_SECONDS = 3600

# Directory bumped from `responses` → `responses-v2` so existing pickle-backed
# caches from 0.2.x don't trip JSON deserialization on first read after
# upgrade. The old directory is harmless; users can `rm -rf` it at leisure.
_CACHE_SUBDIR = "responses-v2"


def _cache_dir() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "fishin" / _CACHE_SUBDIR


def _encode_dt(obj):
    if isinstance(obj, datetime):
        return {"__dt__": obj.isoformat()}
    raise TypeError(f"not JSON serializable: {type(obj).__name__}")


def _decode_dt(obj: dict):
    if len(obj) == 1 and "__dt__" in obj:
        return datetime.fromisoformat(obj["__dt__"])
    return obj


class FishinDisk(diskcache.Disk):
    """JSON-serializing Disk that preserves datetime objects.

    Overrides only the value-side serialization (store/fetch). Keys remain
    handled by the parent class (they're plain strings in our usage).
    """

    def store(self, value, read, key=UNKNOWN):
        if not read:
            value = json.dumps(value, default=_encode_dt).encode("utf-8")
        return super().store(value, read, key=key)

    def fetch(self, mode, filename, value, read):
        data = super().fetch(mode, filename, value, read)
        if not read and isinstance(data, (bytes, bytearray)):
            return json.loads(data.decode("utf-8"), object_hook=_decode_dt)
        return data


@lru_cache(maxsize=1)
def get_cache() -> diskcache.Cache:
    path = _cache_dir()
    path.mkdir(parents=True, exist_ok=True)
    return diskcache.Cache(str(path), disk=FishinDisk)


def weather_ttl(target_date: date) -> int:
    """Past dates: forecast no longer updates, cache effectively forever.
    Today/future: hourly model run, so 1h TTL keeps things fresh."""
    return YEAR_SECONDS if target_date < date.today() else HOUR_SECONDS
