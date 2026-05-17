"""Command-line entry point.

Resolves a location (flags → config file → built-in default → optional --city
geocode), then dispatches to one of four view modes:

    fishin            # single full panel for today
    fishin 7          # compact list view, one row per day
    fishin month      # calendar grid for the month of --date
    fishin best 14    # sorted ranking over the next N days

`--days N` (legacy) forces N full panels back-to-back.

Tide and weather fetches are coalesced into a single range request per mode
and cached on disk (see fishin.cache) — repeat runs are network-free, multi-day
modes pay one round-trip per service.
"""

from __future__ import annotations

import argparse
import calendar
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

from rich.console import Console

from .astro import build_day, evaluate_windows
from .render import render_day, render_list, render_month, render_best
from .tides import DEFAULT_STATION, get_tides_many, tide_curve
from .weather import get_weather_many
from .config import load_config, save_config, config_path


BACKSTOP = {
    "lat": 27.3633,
    "lon": -82.5197,
    "tz": "America/New_York",
    "station": DEFAULT_STATION,
    "location": "Sarasota, FL",
}

# An ocean tide station >100km from the user's location is decoupled from
# their actual fishing conditions — inland lakes don't experience ocean tides.
# `--city` resolution drops the station entirely in that case.
INLAND_THRESHOLD_KM = 100.0


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _resolve_city(query: str, console: Console) -> dict:
    from .geocode import geocode, timezone_for
    from .stations import nearest_station

    hit = geocode(query)
    lat, lon = hit["lat"], hit["lon"]
    tz = timezone_for(lat, lon)
    station = nearest_station(lat, lon)

    pretty = hit["display_name"].split(",")
    short = ", ".join(s.strip() for s in pretty[:2])

    result = {"lat": lat, "lon": lon, "tz": tz, "location": short}

    if station["distance_km"] <= INLAND_THRESHOLD_KM:
        result["station"] = station["id"]
        station_note = (f"station={station['id']} {station['name']} "
                        f"({station['distance_km']:.1f} km)")
    else:
        result["station"] = None
        station_note = (f"inland — nearest tide station "
                        f"{station['name']} is "
                        f"{station['distance_km']:.0f} km away, "
                        f"tides disabled")

    console.print(
        f"[dim]resolved {query!r} → {short} "
        f"({lat:.4f}, {lon:.4f}) tz={tz} {station_note}[/dim]"
    )
    return result


def _parse_mode(tokens: list[str], legacy_days: int) -> dict:
    if not tokens:
        return {"type": "panel", "days": legacy_days}
    first = tokens[0].lower()
    if first == "month":
        return {"type": "month"}
    if first == "best":
        n = int(tokens[1]) if len(tokens) > 1 else 14
        return {"type": "best", "days": n, "top": 5}
    try:
        n = int(first)
    except ValueError as exc:
        raise SystemExit(f"unknown mode {first!r}") from exc
    return {"type": "list", "days": n}


def _build_days(loc: dict, tz: ZoneInfo, targets: list[date],
                *, want_tides: bool, want_weather: bool,
                console: Console) -> list[dict]:
    """Bulk-fetch tides + weather for `targets`, attach to each day dict.

    Tide fetches include ±1 day of padding around the request so each day's
    cosine-interpolated curve can be anchored to its neighbours' extrema —
    no more flat cold-start at midnight.
    """
    tides_by_day: dict[date, list[dict]] = {}
    if want_tides and loc.get("station"):
        try:
            padded = (
                [targets[0] - timedelta(days=1)]
                + targets
                + [targets[-1] + timedelta(days=1)]
            )
            tides_by_day = get_tides_many(loc["station"], padded, tz)
        except Exception as exc:
            console.print(f"[dim]tides unavailable ({exc})[/dim]")

    weather_by_day: dict[date, dict] = {}
    if want_weather:
        try:
            weather_by_day = get_weather_many(loc["lat"], loc["lon"], targets, tz)
        except Exception as exc:
            console.print(f"[dim]weather unavailable ({exc})[/dim]")

    days: list[dict] = []
    for target in targets:
        day = build_day(target, loc["lat"], loc["lon"], loc["tz"])
        events = tides_by_day.get(target, [])
        if events:
            prior = target - timedelta(days=1)
            after = target + timedelta(days=1)
            context: list[dict] = []
            if tides_by_day.get(prior):
                context.append(tides_by_day[prior][-1])
            if tides_by_day.get(after):
                context.append(tides_by_day[after][0])
            day["tides"] = {
                "station": loc["station"],
                "events": events,
                "curve": tide_curve(events, target, tz, n_points=48,
                                    context=context),
            }
        if target in weather_by_day:
            day["weather"] = weather_by_day[target]
        day["windows"] = evaluate_windows(day)
        days.append(day)
    return days


_EPILOG = """\
Examples:
  fishin --city "your town st" --save   set your default location once
  fishin                                today, full panel, saved location
  fishin --city "key west fl"           one-shot lookup, no save
  fishin 7                              7-day compact list view
  fishin month                          calendar grid, star-rated days
  fishin best 14                        next 14 days ranked by score
  fishin --date 2026-06-15              any specific date
  fishin --no-tides --no-weather        astro-only, no network calls

Config:
  Resolved location lives in ~/.config/fishin/config.toml.
  Resolution order: explicit flags > --city > config > built-in default.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fishin",
        description="Terminal solunar / tide / weather forecast.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("mode", nargs="*",
                        help="N (list N days) | 'month' | 'best [N]'")
    parser.add_argument("--city",
                        help="Place name to geocode (e.g. 'sarasota fl'); "
                             "one-shot unless paired with --save")
    parser.add_argument("--lat", type=float, help="Latitude (decimal degrees)")
    parser.add_argument("--lon", type=float, help="Longitude (decimal degrees)")
    parser.add_argument("--tz", help="IANA timezone (e.g. 'America/Chicago')")
    parser.add_argument("--location", help="Display name for the panel title")
    parser.add_argument("--station", help="NOAA CO-OPS tide station id")
    parser.add_argument("--date", type=_parse_date, default=date.today(),
                        help="Target date YYYY-MM-DD (default: today)")
    parser.add_argument("--days", type=int, default=1,
                        help="Force N full panels back-to-back (legacy)")
    parser.add_argument("--no-tides", action="store_true",
                        help="Skip the NOAA tide fetch")
    parser.add_argument("--no-weather", action="store_true",
                        help="Skip the open-meteo weather fetch")
    parser.add_argument("--save", action="store_true",
                        help=f"Save the resolved location as your default "
                             f"({config_path()})")
    args = parser.parse_args(argv)

    console = Console()

    # If the config file is complete it's authoritative — don't blend in
    # BACKSTOP's station/coords or they'll bleed into the user's actual
    # location (e.g. Sarasota's tide station getting used for inland Austin).
    config = load_config()
    core = {"lat", "lon", "tz", "location"}
    if core.issubset(config.keys()):
        loc = config
    else:
        loc = dict(BACKSTOP)
        loc.update(config)

    if args.city:
        try:
            loc.update(_resolve_city(args.city, console))
        except Exception as exc:
            console.print(f"[red]could not resolve city {args.city!r}: {exc}[/red]")
            return 1

    for key in ("lat", "lon", "tz", "location", "station"):
        v = getattr(args, key, None)
        if v is not None:
            loc[key] = v

    if args.save:
        path = save_config(loc)
        console.print(f"[dim]saved → {path}[/dim]")

    tz = ZoneInfo(loc["tz"])
    mode = _parse_mode(args.mode, args.days)
    want_tides = not args.no_tides
    want_weather = not args.no_weather

    if mode["type"] == "panel":
        _run_panel(loc, tz, args.date, mode["days"],
                   want_tides=want_tides, want_weather=want_weather,
                   console=console)
    elif mode["type"] == "list":
        _run_list(loc, tz, args.date, mode["days"],
                  want_tides=want_tides, want_weather=want_weather,
                  console=console)
    elif mode["type"] == "month":
        _run_month(loc, args.date, console=console)
    elif mode["type"] == "best":
        _run_best(loc, tz, args.date, mode["days"], mode["top"],
                  want_tides=want_tides, want_weather=want_weather,
                  console=console)
    return 0


def _run_panel(loc, tz, start_date, days, *,
               want_tides, want_weather, console):
    targets = [start_date + timedelta(days=i) for i in range(days)]
    trend_dates = [start_date + timedelta(days=i) for i in range(days + 6)]
    trend_scores = [build_day(d, loc["lat"], loc["lon"], loc["tz"])["score"]
                    for d in trend_dates]

    built = _build_days(loc, tz, targets,
                        want_tides=want_tides, want_weather=want_weather,
                        console=console)
    for i, day in enumerate(built):
        day["trend"] = trend_scores[i:i + 7]
        render_day(day, loc["location"], console=console)


def _run_list(loc, tz, start_date, days, *,
              want_tides, want_weather, console):
    targets = [start_date + timedelta(days=i) for i in range(days)]
    built = _build_days(loc, tz, targets,
                        want_tides=want_tides, want_weather=want_weather,
                        console=console)
    render_list(built, loc["location"], console=console)


def _run_month(loc, ref_date, *, console):
    year, month = ref_date.year, ref_date.month
    num_days = calendar.monthrange(year, month)[1]
    days = [build_day(date(year, month, d),
                      loc["lat"], loc["lon"], loc["tz"])
            for d in range(1, num_days + 1)]
    render_month(days, loc["location"], console=console)


def _run_best(loc, tz, start_date, days, top, *,
              want_tides, want_weather, console):
    targets = [start_date + timedelta(days=i) for i in range(days)]
    built = _build_days(loc, tz, targets,
                        want_tides=want_tides, want_weather=want_weather,
                        console=console)
    render_best(built, loc["location"], top_n=top, console=console)


def cli_main() -> None:
    """Console-script entry point (installed by pyproject)."""
    raise SystemExit(main())


if __name__ == "__main__":
    cli_main()
