"""NOAA CO-OPS tide predictions.

Fetches high/low predictions for a station, then synthesizes a smooth curve
between extrema via cosine interpolation. The cosine model is correct at the
extrema (zero derivative) and visually indistinguishable from the harmonic
prediction for sparkline purposes — and a lot cheaper than fetching the 6-min
interval dataset.

Nearest-station resolution lands in a later phase; for now the default station
is hardcoded for the Sarasota Bay area.
"""

from __future__ import annotations

import json
import math
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo


# Sarasota Bay station — closest to the default Sarasota lat/lon.
# https://tidesandcurrents.noaa.gov/stationhome.html?id=8726384
DEFAULT_STATION = "8726384"

NOAA_URL = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"

SPARK_CHARS = "▁▂▃▄▅▆▇█"


def fetch_predictions_range(station: str, start_date: date, end_date: date,
                            tz: ZoneInfo, timeout: float = 10.0) -> list[dict]:
    """One NOAA request for [start_date, end_date] inclusive.

    Returns a flat sorted list of events across the whole range.
    """
    params = {
        "product": "predictions",
        "application": "fishin",
        "begin_date": start_date.strftime("%Y%m%d"),
        "end_date": end_date.strftime("%Y%m%d"),
        "datum": "MLLW",
        "station": station,
        "time_zone": "lst_ldt",
        "units": "english",
        "interval": "hilo",
        "format": "json",
    }
    url = f"{NOAA_URL}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        payload = json.load(resp)

    if "error" in payload:
        raise RuntimeError(payload["error"].get("message", "NOAA error"))

    events: list[dict] = []
    for p in payload.get("predictions", []):
        local = datetime.strptime(p["t"], "%Y-%m-%d %H:%M").replace(tzinfo=tz)
        events.append({
            "time": local,
            "height": float(p["v"]),
            "kind": p["type"],
        })
    events.sort(key=lambda e: e["time"])
    return events


def _cache_key(station: str, d: date) -> str:
    return f"tide:{station}:{d.isoformat()}"


def get_tides_many(station: str, dates: list[date], tz: ZoneInfo,
                   cache=None) -> dict[date, list[dict]]:
    """Return {date: events} for each requested date, using the shared cache.

    Cache misses are coalesced into one NOAA call spanning the missing range,
    then split per-day and stored so subsequent overlapping queries hit cache.
    """
    from .cache import get_cache, YEAR_SECONDS
    cache = cache or get_cache()

    results: dict[date, list[dict]] = {}
    missing: list[date] = []
    for d in dates:
        hit = cache.get(_cache_key(station, d), default=None)
        if hit is not None:
            results[d] = hit
        else:
            missing.append(d)

    if missing:
        start, end = min(missing), max(missing)
        events = fetch_predictions_range(station, start, end, tz)
        by_day: dict[date, list[dict]] = {d: [] for d in missing}
        for ev in events:
            d = ev["time"].date()
            if d in by_day:
                by_day[d].append(ev)
        for d, evs in by_day.items():
            cache.set(_cache_key(station, d), evs, expire=YEAR_SECONDS)
            results[d] = evs

    return results


def tide_curve(events: list[dict], target_date: date, tz: ZoneInfo,
               n_points: int = 48,
               context: list[dict] | None = None) -> list[float]:
    """Cosine-interpolate between extrema to produce a curve across the day.

    `context` can optionally extend the event list with the prior day's last
    extremum and the next day's first extremum so the start/end of the curve
    is anchored correctly rather than extrapolated flat. If not provided we
    just hold the boundary heights.
    """
    all_events = sorted((context or []) + events, key=lambda e: e["time"])
    day_start = datetime(target_date.year, target_date.month, target_date.day,
                         tzinfo=tz)
    day_end = day_start + timedelta(days=1)

    if not all_events:
        return [0.0] * n_points

    out: list[float] = []
    for i in range(n_points):
        t = day_start + (day_end - day_start) * (i / n_points)

        prev_ev = next_ev = None
        for e in all_events:
            if e["time"] <= t:
                prev_ev = e
            else:
                next_ev = e
                break

        if prev_ev and next_ev:
            span = (next_ev["time"] - prev_ev["time"]).total_seconds()
            if span <= 0:
                out.append(prev_ev["height"])
            else:
                x = (t - prev_ev["time"]).total_seconds() / span
                h0, h1 = prev_ev["height"], next_ev["height"]
                out.append(h0 + (h1 - h0) * (1 - math.cos(math.pi * x)) / 2)
        elif prev_ev:
            out.append(prev_ev["height"])
        elif next_ev:
            out.append(next_ev["height"])
        else:
            out.append(0.0)
    return out


def sparkline(values: list[float],
              lo: float | None = None, hi: float | None = None) -> str:
    """Render a sequence of floats as Unicode block sparkline characters.

    Pass `lo`/`hi` to lock the scale across multiple sparklines (e.g. a
    multi-day grid); otherwise the scale auto-fits to the values."""
    if not values:
        return ""
    lo = min(values) if lo is None else lo
    hi = max(values) if hi is None else hi
    if hi <= lo:
        return SPARK_CHARS[len(SPARK_CHARS) // 2] * len(values)
    span = hi - lo
    out = []
    for v in values:
        idx = int(round((v - lo) / span * (len(SPARK_CHARS) - 1)))
        idx = max(0, min(len(SPARK_CHARS) - 1, idx))
        out.append(SPARK_CHARS[idx])
    return "".join(out)
