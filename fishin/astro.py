"""Astronomy and day-building.

Pure computation — no terminal output, no argparse. Given a date, lat/lon, and
timezone, returns a dict describing the day's solunar events, periods, and
rating. The skyfield ephemeris is loaded lazily and cached at module scope so
multi-day runs don't repay the cost.
"""

from __future__ import annotations

import math
import os
from datetime import datetime, timedelta, date
from pathlib import Path
from zoneinfo import ZoneInfo

from skyfield.api import Loader, wgs84
from skyfield import almanac


MAJOR_HOURS = 3.0   # 1.5hr each side of moon transit / antitransit
MINOR_HOURS = 2.0   # 1hr each side of moonrise / moonset

_ts = None
_eph = None


def _ephemeris_dir() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "fishin" / "ephemeris"


def _load_skyfield():
    """Lazy-load skyfield's timescale and the DE421 ephemeris.

    The ~17MB ephemeris is parked under `~/.cache/fishin/ephemeris/` so it
    doesn't litter whatever directory the user happens to run `fishin` from,
    and so it survives across invocations.
    """
    global _ts, _eph
    if _ts is None:
        path = _ephemeris_dir()
        path.mkdir(parents=True, exist_ok=True)
        loader = Loader(str(path))
        _ts = loader.timescale()
        _eph = loader("de421.bsp")
    return _ts, _eph


# ---------------------------------------------------------------- moon phase

def phase_name(deg: float) -> tuple[str, str]:
    """Return (name, symbol). Named phases (New, Quarters, Full) get tight ±5°
    windows; crescent / gibbous fill the gaps."""
    a = deg % 360
    if a < 5 or a >= 355:
        return "New Moon", "🌑"
    if a < 85:
        return "Waxing Crescent", "🌒"
    if a < 95:
        return "First Quarter", "🌓"
    if a < 175:
        return "Waxing Gibbous", "🌔"
    if a < 185:
        return "Full Moon", "🌕"
    if a < 265:
        return "Waning Gibbous", "🌖"
    if a < 275:
        return "Last Quarter", "🌗"
    return "Waning Crescent", "🌘"


def phase_score(deg: float) -> float:
    # 1.0 at new and full, 0.0 at quarters
    return abs(math.cos(math.radians(deg)))


# ---------------------------------------------------------------- events

def find_events(target_date: date, lat: float, lon: float, tz: ZoneInfo) -> dict:
    ts, eph = _load_skyfield()
    location = wgs84.latlon(lat, lon)

    start_local = datetime(target_date.year, target_date.month, target_date.day,
                           tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    pad = timedelta(hours=4)

    t0 = ts.from_datetime(start_local - pad)
    t1 = ts.from_datetime(end_local + pad)

    def in_day(t):
        local = t.utc_datetime().astimezone(tz)
        return start_local <= local < end_local

    # Sunrise / sunset
    f_sun = almanac.sunrise_sunset(eph, location)
    sun_times, sun_events = almanac.find_discrete(t0, t1, f_sun)
    sunrise = next((t for t, e in zip(sun_times, sun_events) if e == 1 and in_day(t)), None)
    sunset = next((t for t, e in zip(sun_times, sun_events) if e == 0 and in_day(t)), None)

    # Moonrise / moonset
    f_moon = almanac.risings_and_settings(eph, eph["moon"], location)
    moon_times, moon_events = almanac.find_discrete(t0, t1, f_moon)
    moonrise = next((t for t, e in zip(moon_times, moon_events) if e == 1 and in_day(t)), None)
    moonset = next((t for t, e in zip(moon_times, moon_events) if e == 0 and in_day(t)), None)

    # Sun meridian transit (solar noon)
    f_sun_mt = almanac.meridian_transits(eph, eph["sun"], location)
    smt, sme = almanac.find_discrete(t0, t1, f_sun_mt)
    solar_noon = next((t for t, e in zip(smt, sme) if e == 1 and in_day(t)), None)

    # Moon meridian transits: upper (overhead) and lower (antitransit, underfoot)
    f_moon_mt = almanac.meridian_transits(eph, eph["moon"], location)
    mmt, mme = almanac.find_discrete(t0, t1, f_moon_mt)
    transits = [t for t, e in zip(mmt, mme) if e == 1 and in_day(t)]
    antitransits = [t for t, e in zip(mmt, mme) if e == 0 and in_day(t)]

    # Moon phase at local noon
    noon = start_local + timedelta(hours=12)
    phase_deg = almanac.moon_phase(eph, ts.from_datetime(noon)).degrees

    def tolocal(t):
        if t is None:
            return None
        return t.utc_datetime().astimezone(tz)

    return {
        "date": target_date,
        "sunrise": tolocal(sunrise),
        "solar_noon": tolocal(solar_noon),
        "sunset": tolocal(sunset),
        "moonrise": tolocal(moonrise),
        "moonset": tolocal(moonset),
        "transits": [tolocal(t) for t in transits],
        "antitransits": [tolocal(t) for t in antitransits],
        "phase_deg": phase_deg,
    }


# ---------------------------------------------------------------- periods

def period_around(center: datetime, hours: float) -> tuple[datetime, datetime]:
    half = timedelta(hours=hours / 2)
    return (center - half, center + half)


def overlaps(a_start, a_end, b_start, b_end) -> bool:
    return a_start < b_end and b_start < a_end


def build_day(target_date: date, lat: float, lon: float, tz_name: str) -> dict:
    tz = ZoneInfo(tz_name)
    ev = find_events(target_date, lat, lon, tz)

    majors = []
    for t in ev["transits"]:
        majors.append(period_around(t, MAJOR_HOURS))
    for t in ev["antitransits"]:
        majors.append(period_around(t, MAJOR_HOURS))
    majors.sort()

    minors = []
    if ev["moonrise"]:
        minors.append(period_around(ev["moonrise"], MINOR_HOURS))
    if ev["moonset"]:
        minors.append(period_around(ev["moonset"], MINOR_HOURS))
    minors.sort()

    phase_pct = phase_score(ev["phase_deg"])
    coincidence = 0.0
    if ev["sunrise"] and ev["sunset"]:
        win = timedelta(hours=1)
        for p_start, p_end in majors + minors:
            if overlaps(p_start, p_end, ev["sunrise"] - win, ev["sunrise"] + win):
                coincidence += 0.15
            if overlaps(p_start, p_end, ev["sunset"] - win, ev["sunset"] + win):
                coincidence += 0.15
    coincidence = min(coincidence, 0.4)

    # Base 15% + phase up to 60% + coincidence up to 25%
    score = min(1.0, 0.15 + 0.60 * phase_pct + 0.625 * coincidence)

    if score >= 0.85:
        rating = "Excellent"
    elif score >= 0.65:
        rating = "Good"
    elif score >= 0.45:
        rating = "Average"
    else:
        rating = "Poor"

    pname, psymbol = phase_name(ev["phase_deg"])
    return {**ev, "majors": majors, "minors": minors, "score": score,
            "rating": rating, "phase_name": pname, "phase_symbol": psymbol}


# --------------------------------------------------------------- windows

# Window scoring: major/minor base + weighted overlap factors. Tuned so that
# a minor with two strong factors (e.g. sunrise + low tide) can outrank a lone
# major — which matches actual fishing wisdom.
_BASE_MAJOR = 4
_BASE_MINOR = 2
_W_SUN = 3       # sunrise or sunset within 1hr of the period
_W_TIDE = 2      # each H/L within 30min of the period
_W_CALM = 1
_W_WIND = -1
_W_RAIN = -2

_SUN_WIN = timedelta(hours=1)
_TIDE_WIN = timedelta(minutes=30)


def _weather_factor(day: dict, start: datetime, end: datetime) -> dict | None:
    """Pick a single weather note for the window from the closest sample."""
    wx = day.get("weather")
    if not wx:
        return None
    midpoint = start + (end - start) / 2
    samples = [p[1] for p in wx.get("periods", []) if p[1]]
    if not samples:
        return None
    closest = min(samples, key=lambda s: abs(s["time"] - midpoint))

    if closest["precip_prob"] >= 60:
        return {"type": "weather", "subtype": "rain",
                "value": closest["precip_prob"], "bonus": _W_RAIN}
    if closest["wind_mph"] >= 18:
        return {"type": "weather", "subtype": "wind",
                "value": closest["wind_mph"], "bonus": _W_WIND}
    if closest["wind_mph"] <= 6:
        return {"type": "weather", "subtype": "calm",
                "value": closest["wind_mph"], "bonus": _W_CALM}
    return None


def _score_window(label: str, period: tuple, day: dict, base: int) -> dict:
    s, e = period
    score = base
    factors: list[dict] = []

    if day.get("sunrise") and overlaps(s, e,
                                       day["sunrise"] - _SUN_WIN,
                                       day["sunrise"] + _SUN_WIN):
        factors.append({"type": "sunrise"})
        score += _W_SUN
    if day.get("sunset") and overlaps(s, e,
                                      day["sunset"] - _SUN_WIN,
                                      day["sunset"] + _SUN_WIN):
        factors.append({"type": "sunset"})
        score += _W_SUN

    tide_events = (day.get("tides") or {}).get("events") or []
    for ev in tide_events:
        if overlaps(s, e, ev["time"] - _TIDE_WIN, ev["time"] + _TIDE_WIN):
            factors.append({"type": "tide", "kind": ev["kind"],
                            "time": ev["time"], "height": ev["height"]})
            score += _W_TIDE

    wf = _weather_factor(day, s, e)
    if wf:
        factors.append(wf)
        score += wf["bonus"]

    return {"label": label, "period": period, "factors": factors, "score": score}


def evaluate_windows(day: dict) -> list[dict]:
    """Score and rank every major/minor period for the day.

    Returns a list of window dicts sorted by score descending (with earlier
    starts breaking ties). Each window's `factors` list captures every overlap
    (sunrise/sunset/tide/weather) that contributed to its score, so the
    renderer can spell them out for the angler.
    """
    windows: list[dict] = []
    for p in day.get("majors", []):
        windows.append(_score_window("Major", p, day, _BASE_MAJOR))
    for p in day.get("minors", []):
        windows.append(_score_window("Minor", p, day, _BASE_MINOR))
    windows.sort(key=lambda w: (-w["score"], w["period"][0]))
    return windows
