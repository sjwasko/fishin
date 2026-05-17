"""Place-name → lat/lon/timezone resolution.

Geocoding via OpenStreetMap Nominatim (free, no key, but rate-limited and
requires a descriptive User-Agent). Timezone via `timezonefinder`, which keeps
a snapshot of tz_world geometries on disk and resolves offline after install.

Nominatim's usage policy explicitly asks clients to cache results — a city's
coordinates don't change, so we keep responses for 30 days in the shared
diskcache.

  https://operations.osmfoundation.org/policies/nominatim/
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

from . import __version__


NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = f"fishin/{__version__} (https://github.com/sjwasko/fishin)"
GEOCODE_TTL_SECONDS = 30 * 24 * 3600

_tf = None


def _timezone_finder():
    global _tf
    if _tf is None:
        # Lazy-import — timezonefinder pulls in numpy + h3 and is the heaviest
        # dep we have. Defer until actually needed.
        from timezonefinder import TimezoneFinder
        _tf = TimezoneFinder()
    return _tf


def geocode(query: str, timeout: float = 10.0, cache=None) -> dict:
    """Resolve a free-form place name to {lat, lon, display_name}.

    Responses are cached on disk per Nominatim's usage policy; a repeat
    query is a local lookup with no network call. Pass `cache=False` to
    bypass caching entirely (mostly useful in tests).

    Raises LookupError if the query has no hit.
    """
    norm = " ".join(query.strip().lower().split())
    key = f"geocode:{norm}"

    if cache is not False:
        from .cache import get_cache
        cache = cache if cache is not None else get_cache()
        hit = cache.get(key, default=None)
        if hit is not None:
            return hit

    params = {"q": query, "format": "json", "limit": "1", "addressdetails": "0"}
    url = f"{NOMINATIM_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        results = json.load(resp)
    if not results:
        raise LookupError(f"no geocode hit for {query!r}")
    top = results[0]
    result = {
        "lat": float(top["lat"]),
        "lon": float(top["lon"]),
        "display_name": top["display_name"],
    }

    if cache is not False:
        cache.set(key, result, expire=GEOCODE_TTL_SECONDS)
    return result


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
