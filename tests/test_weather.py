"""Forecast-window clamping for the open-meteo fetcher. No network."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from fishin import weather

NY = ZoneInfo("America/New_York")
TOKYO = ZoneInfo("Asia/Tokyo")
UTC = ZoneInfo("UTC")


def test_window_is_today_minus_92_through_today_plus_15():
    now = datetime(2026, 10, 2, 9, 0, tzinfo=NY)
    assert weather.forecast_window(NY, now) == (date(2026, 7, 2), date(2026, 10, 17))


def test_window_takes_narrower_side_when_local_and_utc_dates_differ():
    # 21:00 in New York is already tomorrow in UTC.
    now = datetime(2026, 10, 2, 21, 0, tzinfo=NY)
    assert weather.forecast_window(NY, now) == (date(2026, 7, 3), date(2026, 10, 17))
    # 08:00 in Tokyo is still yesterday in UTC.
    now = datetime(2026, 10, 3, 8, 0, tzinfo=TOKYO)
    assert weather.forecast_window(TOKYO, now) == (date(2026, 7, 3), date(2026, 10, 17))


class FakeCache(dict):
    def get(self, key, default=None):
        return super().get(key, default)

    def set(self, key, value, expire=None):
        self[key] = value


def _fake_raw(start: date, end: date) -> dict:
    """Minimal open-meteo-shaped response covering [start, end]."""
    times, days = [], []
    d = start
    while d <= end:
        days.append(d.isoformat())
        times += [f"{d.isoformat()}T{h:02d}:00" for h in range(24)]
        d += timedelta(days=1)
    n = len(times)
    hourly = {"time": times, "weather_code": [0] * n}
    for k in ("temperature_2m", "apparent_temperature", "precipitation_probability",
              "surface_pressure", "relative_humidity_2m", "wind_speed_10m",
              "wind_direction_10m"):
        hourly[k] = [1] * n
    return {"hourly": hourly, "daily": {"time": days, "weather_code": [0] * len(days)}}


@pytest.fixture
def window(monkeypatch):
    first, last = date(2026, 7, 2), date(2026, 10, 17)
    monkeypatch.setattr(weather, "forecast_window", lambda tz, now=None: (first, last))
    requests = []

    def fake_urlopen(url, timeout):
        from urllib.parse import parse_qs, urlparse
        q = parse_qs(urlparse(url).query)
        start = date.fromisoformat(q["start_date"][0])
        end = date.fromisoformat(q["end_date"][0])
        requests.append((start, end))
        assert first <= start <= end <= last, "request left the forecast window"
        import io, json
        return io.BytesIO(json.dumps(_fake_raw(start, end)).encode())

    monkeypatch.setattr(weather.urllib.request, "urlopen", fake_urlopen)
    return first, last, requests


def _span(start: date, n: int) -> list[date]:
    return [start + timedelta(days=i) for i in range(n)]


def test_far_days_are_dropped_and_request_stays_in_window(window):
    first, last, requests = window
    dates = _span(date(2026, 10, 2), 31)
    cache = FakeCache()
    got = weather.get_weather_many(0.0, 0.0, dates, NY, cache=cache)

    assert sorted(got) == [d for d in dates if d <= last]
    assert requests == [(date(2026, 10, 2), last)]
    # Far days aren't cached as empty, so they fill in once they're in range.
    assert len(cache) == len(got)


def test_last_day_has_no_night_sample_but_earlier_days_do(window):
    first, last, _ = window
    got = weather.get_weather_many(0.0, 0.0, [last - timedelta(days=1), last], NY,
                                   cache=FakeCache())
    night = lambda d: dict(got[d]["periods"])["Night"]
    assert night(last - timedelta(days=1)) is not None
    assert night(last) is None
    assert dict(got[last]["periods"])["Noon"] is not None


def test_dates_entirely_outside_window_make_no_request(window):
    first, last, requests = window
    far = _span(last + timedelta(days=1), 5) + _span(first - timedelta(days=5), 5)
    assert weather.get_weather_many(0.0, 0.0, far, NY, cache=FakeCache()) == {}
    assert requests == []


def test_old_dates_are_clamped_at_window_start(window):
    first, last, requests = window
    dates = _span(first - timedelta(days=3), 6)
    got = weather.get_weather_many(0.0, 0.0, dates, NY, cache=FakeCache())
    assert sorted(got) == [d for d in dates if d >= first]
    assert requests[0][0] == first
