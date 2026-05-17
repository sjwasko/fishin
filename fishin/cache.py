"""Disk-backed response cache for tide & weather fetches.

`diskcache` gives us a pickle-backed shelf with per-entry TTLs and zero ceremony.
Tide predictions are deterministic and cached for a year; weather forecasts get
1h for today/future and a long TTL for past dates (forecasts no longer update).
"""

from __future__ import annotations

import os
from datetime import date
from functools import lru_cache
from pathlib import Path

import diskcache


YEAR_SECONDS = 365 * 24 * 3600
HOUR_SECONDS = 3600


def _cache_dir() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "fishin" / "responses"


@lru_cache(maxsize=1)
def get_cache() -> diskcache.Cache:
    path = _cache_dir()
    path.mkdir(parents=True, exist_ok=True)
    return diskcache.Cache(str(path))


def weather_ttl(target_date: date) -> int:
    """Past dates: forecast no longer updates, cache effectively forever.
    Today/future: hourly model run, so 1h TTL keeps things fresh."""
    return YEAR_SECONDS if target_date < date.today() else HOUR_SECONDS
