"""Place-name → lat/lon/timezone resolution.

Geocoding via OpenStreetMap Nominatim (free, no key, but rate-limited and
requires a descriptive User-Agent). Timezone via `timezonefinder`, which keeps
a snapshot of tz_world geometries on disk and resolves offline after install.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

from . import __version__


NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = f"fishin/{__version__} (terminal solunar/tide/weather)"

_tf = None


def _timezone_finder():
    global _tf
    if _tf is None:
        # Lazy-import — timezonefinder pulls in numpy + h3 and is the heaviest
        # dep we have. Defer until actually needed.
        from timezonefinder import TimezoneFinder
        _tf = TimezoneFinder()
    return _tf


def geocode(query: str, timeout: float = 10.0) -> dict:
    """Resolve a free-form place name to {lat, lon, display_name}.

    Raises LookupError if the query has no hit.
    """
    params = {"q": query, "format": "json", "limit": "1", "addressdetails": "0"}
    url = f"{NOMINATIM_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        results = json.load(resp)
    if not results:
        raise LookupError(f"no geocode hit for {query!r}")
    top = results[0]
    return {
        "lat": float(top["lat"]),
        "lon": float(top["lon"]),
        "display_name": top["display_name"],
    }


def timezone_for(lat: float, lon: float) -> str:
    """Return an IANA timezone name for the given coordinates."""
    tf = _timezone_finder()
    tz = tf.timezone_at(lat=lat, lng=lon)
    if tz is None:
        # Fall back to closest known zone (slower; handles offshore points)
        tz = tf.closest_timezone_at(lat=lat, lng=lon)
    if tz is None:
        raise LookupError(f"no timezone for ({lat}, {lon})")
    return tz
