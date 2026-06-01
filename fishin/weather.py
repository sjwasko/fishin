"""Open-meteo weather fetcher.

Free, no auth. We pull current conditions and an hourly grid for the target
date, then sample at 6a / 12p / 6p / 12a (next-day) to render the wttr.in-style
4-period strip.

Unit choices match a US/imperial reading: °F, mph, inches, inHg. Stay tuned for
a metric flag when international users care.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo


OPENMETEO_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather code → (label, day glyph, night glyph).
# Glyphs lean monochrome / text-style to stay close to wttr.in / btop tone.
WEATHER_CODES: dict[int, tuple[str, str, str]] = {
    0:  ("Clear",            "☀", "☾"),
    1:  ("Mostly Clear",     "☀", "☾"),
    2:  ("Partly Cloudy",    "⛅", "☁"),
    3:  ("Overcast",         "☁", "☁"),
    45: ("Fog",              "≡", "≡"),
    48: ("Rime Fog",         "≡", "≡"),
    51: ("Light Drizzle",    "☂", "☂"),
    53: ("Drizzle",          "☂", "☂"),
    55: ("Heavy Drizzle",    "☔", "☔"),
    61: ("Light Rain",       "☔", "☔"),
    63: ("Rain",             "☔", "☔"),
    65: ("Heavy Rain",       "☔", "☔"),
    71: ("Light Snow",       "❄", "❄"),
    73: ("Snow",             "❄", "❄"),
    75: ("Heavy Snow",       "❄", "❄"),
    77: ("Snow Grains",      "❄", "❄"),
    80: ("Rain Showers",     "☔", "☔"),
    81: ("Rain Showers",     "☔", "☔"),
    82: ("Heavy Showers",    "☔", "☔"),
    85: ("Snow Showers",     "❄", "❄"),
    86: ("Snow Showers",     "❄", "❄"),
    95: ("Thunderstorm",     "⛈", "⛈"),
    96: ("Thunder + Hail",   "⛈", "⛈"),
    99: ("Thunder + Hail",   "⛈", "⛈"),
}


def code_info(code: int, is_night: bool) -> tuple[str, str]:
    label, day_g, night_g = WEATHER_CODES.get(code, ("Unknown", "·", "·"))
    return label, (night_g if is_night else day_g)


def wind_arrow(deg: float) -> str:
    """Convert meteorological 'wind from' degrees to a unicode arrow showing
    where the wind is blowing toward (wttr.in convention)."""
    to_deg = (deg + 180) % 360
    idx = int(round(to_deg / 45)) % 8
    return "↑↗→↘↓↙←↖"[idx]


def hpa_to_inhg(hpa: float) -> float:
    return hpa * 0.02953


def fetch_weather_range(lat: float, lon: float,
                        start_date: date, end_date: date, tz: ZoneInfo,
                        timeout: float = 10.0) -> dict:
    """One open-meteo request for [start_date, end_date + 1] inclusive.

    The trailing +1 day ensures the last in-range day's midnight "Night"
    sample (hour 0 of next day) is present.
    """
    params = {
        "latitude": f"{lat}",
        "longitude": f"{lon}",
        "timezone": str(tz),
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "hourly": ",".join([
            "temperature_2m",
            "weather_code",
            "precipitation_probability",
            "surface_pressure",
            "relative_humidity_2m",
            "apparent_temperature",
            "wind_speed_10m",
            "wind_direction_10m",
        ]),
        "daily": ",".join([
            "weather_code",
            "temperature_2m_max",
            "temperature_2m_min",
            "precipitation_sum",
            "precipitation_probability_max",
        ]),
        "start_date": start_date.isoformat(),
        "end_date": (end_date + timedelta(days=1)).isoformat(),
    }
    url = f"{OPENMETEO_URL}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.load(resp)


def _day_from_raw(raw: dict, target_date: date, tz: ZoneInfo) -> dict:
    """Slice a per-day {periods, daily} shape out of a range response."""
    hourly = raw.get("hourly", {})
    times = hourly.get("time", [])

    def sample(d: date, h: int) -> dict | None:
        key = f"{d.isoformat()}T{h:02d}:00"
        try:
            i = times.index(key)
        except ValueError:
            return None
        return {
            "time": datetime(d.year, d.month, d.day, h, tzinfo=tz),
            "temp": hourly["temperature_2m"][i],
            "feels_like": hourly["apparent_temperature"][i],
            "code": int(hourly["weather_code"][i]),
            "precip_prob": int(hourly["precipitation_probability"][i] or 0),
            "pressure_hpa": hourly["surface_pressure"][i],
            "humidity": int(hourly["relative_humidity_2m"][i] or 0),
            "wind_mph": hourly["wind_speed_10m"][i],
            "wind_deg": hourly["wind_direction_10m"][i],
        }

    next_day = target_date + timedelta(days=1)
    periods = [
        ("Morn",  sample(target_date, 6)),
        ("Noon",  sample(target_date, 12)),
        ("Eve",   sample(target_date, 18)),
        ("Night", sample(next_day, 0)),
    ]

    # Slice daily arrays at the matching date index.
    daily_in: dict = raw.get("daily", {})
    daily_times = daily_in.get("time", [])
    daily: dict = {}
    if target_date.isoformat() in daily_times:
        i = daily_times.index(target_date.isoformat())
        for k, arr in daily_in.items():
            if k == "time":
                daily[k] = arr[i]
            elif isinstance(arr, list) and i < len(arr):
                daily[k] = arr[i]

    return {"periods": periods, "daily": daily}


def _cache_key(lat: float, lon: float, d: date) -> str:
    return f"wx:{lat:.4f}:{lon:.4f}:{d.isoformat()}"


def get_weather_many(lat: float, lon: float, dates: list[date],
                     tz: ZoneInfo, cache=None) -> dict[date, dict]:
    """Return {date: per_day_dict} for each requested date, using the cache.

    Cache misses are coalesced into one open-meteo call, then split per-day."""
    from .cache import get_cache, weather_ttl
    cache = cache or get_cache()

    results: dict[date, dict] = {}
    missing: list[date] = []
    for d in dates:
        hit = cache.get(_cache_key(lat, lon, d), default=None)
        if hit is not None:
            results[d] = hit
        else:
            missing.append(d)

    if missing:
        start, end = min(missing), max(missing)
        raw = fetch_weather_range(lat, lon, start, end, tz)
        for d in missing:
            day = _day_from_raw(raw, d, tz)
            cache.set(_cache_key(lat, lon, d), day, expire=weather_ttl(d))
            results[d] = day

    return results
