# Architecture

A high-level map of how `fishin` is put together. Read this if you want to
contribute, audit the data flow, or fork for a different sport (hunting, surf,
sailing — the bones are the same).

## Design priorities, in order

1. **Information density** — every cell of a panel should carry signal. No
   decorative padding, no "click to expand," no scrolling. One render, one
   glance.
2. **Terminal-native output** — ASCII / Unicode glyphs, ANSI color, box-drawing
   characters. The output pipes, scripts, and SSH-tunnels cleanly.
3. **Network-light** — coalesce API calls into range fetches, cache aggressively
   on disk, degrade gracefully when offline.
4. **No leaky abstractions** — modules speak in plain Python dicts. The
   astronomy code knows nothing about rich; the renderer knows nothing about
   skyfield. The cache module is a thin wrapper, not a framework.

## Module layout

```
fishin/
├── __init__.py     version string only
├── __main__.py     `python -m fishin` entry point
├── cli.py          argparse, mode dispatch, location resolution, run_* glue
├── astro.py        skyfield-backed solunar math + window scoring
├── tides.py        NOAA CO-OPS fetcher, cosine curve, sparkline
├── weather.py      open-meteo fetcher, WMO glyph table, wind arrows
├── geocode.py      Nominatim + timezonefinder
├── stations.py     NOAA tide station catalog + nearest-lookup
├── cache.py        diskcache wrapper, TTL helpers
├── config.py       ~/.config/fishin/config.toml read/write
└── render.py       rich panel + list + month + best views
```

Each module is single-purpose. The graph is intentionally a DAG:

```
cli ──┬──> astro ────────────────────────> render
      ├──> tides  ──> cache
      ├──> weather ─> cache
      ├──> geocode ─> cache
      ├──> stations
      └──> config
```

`render.py` is the only module that imports `rich`. Astronomy, tides, weather,
and scoring are all pure-data — they return dicts that the renderer formats.
This makes it easy to add a JSON output mode, a web frontend, or a test
harness without touching the math.

## Data flow

A single `fishin` invocation walks roughly:

1. **`cli.main()`** parses argv and resolves the location dict
   (`{lat, lon, tz, station, location}`) by layering:
   ```
   BACKSTOP (Sarasota) ← config file ← --city resolve ← explicit flags
   ```
   If the config file contains the four core keys, it replaces BACKSTOP
   entirely so a saved inland location doesn't bleed the default tide
   station through.

2. **Mode dispatch** maps positional args to a runner:
   - `fishin` → `_run_panel` (full single-day panel)
   - `fishin N` → `_run_list` (compact N-day rows)
   - `fishin month` → `_run_month` (calendar grid, astro only)
   - `fishin best N` → `_run_best` (ranked top-N)

3. **`_build_days()`** is the shared workhorse for every mode except `month`.
   It takes a list of target dates and:
   - Builds the astronomy dict (`astro.build_day`) per day
   - Bulk-fetches tides via `tides.get_tides_many` with ±1 day padding
   - Bulk-fetches weather via `weather.get_weather_many`
   - Scores fishing windows via `astro.evaluate_windows`

   Each fetcher is cache-aware: it checks the shared `diskcache` per-day, then
   coalesces misses into a single range request to the upstream API.

4. **`render.render_*()`** consumes the enriched day dicts and prints rich
   `Panel`s. The single-day, list, month, and best views each have their own
   renderer but share helpers (`_list_row`, `_tide_summary`, `_wx_summary`).

## Caching strategy

Everything network-bound goes through `fishin.cache` (a thin wrapper around
`diskcache`). The cache lives at `~/.cache/fishin/responses/`.

| Key prefix | TTL | Why |
|---|---|---|
| `tide:{station}:{YYYY-MM-DD}` | 1 year | Predictions are harmonic and deterministic |
| `wx:{lat:.4f}:{lon:.4f}:{YYYY-MM-DD}` | 1 hour future / 1 year past | Forecasts refresh hourly; past dates frozen |
| `geocode:{normalized query}` | 30 days | Cities don't move; OSM Nominatim TOS requires caching |

The `nearest_station` catalog is cached separately at
`~/.cache/fishin/stations.json` (one JSON file, never expires until manually
removed). The JPL DE421 ephemeris lives at `~/.cache/fishin/ephemeris/`.

To wipe everything: `rm -rf ~/.cache/fishin/`. The next run will re-download.

### Range coalescing

Naive per-day fetches don't scale — `fishin best 14` would fire 28 HTTP
requests. Instead `get_tides_many` and `get_weather_many` follow this pattern:

```python
def get_X_many(args, dates, cache):
    results, missing = check_cache(dates)
    if missing:
        raw = fetch_range(min(missing), max(missing))   # 1 request
        for d in missing:
            results[d] = extract_day(raw, d)
            cache.set(key_for(d), results[d])
    return results
```

A 14-day request hits each API exactly once. Subsequent runs are free.

## Scoring model

`astro.evaluate_windows(day)` ranks every major and minor period for the day.
Each window starts with a base score (Major +4, Minor +2) and accumulates
bonuses from overlapping factors:

| Factor | Window | Weight |
|---|---|---|
| Major period base | — | +4 |
| Minor period base | — | +2 |
| Sunrise overlap | ±1 hr | +3 |
| Sunset overlap | ±1 hr | +3 |
| Each H/L tide overlap | ±30 min | +2 |
| Calm wind | ≤6 mph | +1 |
| Strong wind | ≥18 mph | −1 |
| Rain | ≥60% | −2 |

Weather factors look at the hourly sample closest to the window midpoint
(one of `Morn` / `Noon` / `Eve` / `Night`).

Tunables live as module-level constants in `astro.py`: `_BASE_MAJOR`,
`_BASE_MINOR`, `_W_SUN`, `_W_TIDE`, `_W_CALM`, `_W_WIND`, `_W_RAIN`, plus
window expansions `_SUN_WIN` and `_TIDE_WIN`.

## Tide curve interpolation

NOAA returns high/low events only. To render the per-day sparkline we
cosine-interpolate between extrema:

```
h(t) = h0 + (h1 − h0) × (1 − cos(π × x)) / 2     where x ∈ [0, 1]
```

This is correct at the extrema (zero derivative) and visually
indistinguishable from the harmonic prediction for sparkline purposes — and
free of an extra 6-min interval API call.

The ±1 day tide padding in `_build_days` is what makes the start-of-day
curve render correctly: without prior-day context, the cosine has nothing
to anchor against and the curve flat-starts at the first event.

## Adding a new view mode

1. Add a positional handler in `cli._parse_mode`.
2. Add a `_run_<name>` function in `cli.py` that prepares day dicts (use
   `_build_days` if you need tides/weather, raw `build_day` if astro-only).
3. Add a `render_<name>` function in `render.py`. Take `console: Console |
   None = None` for testability.
4. Reuse the helpers — `_list_row`, `_tide_summary`, `_wx_summary`,
   `_best_summary`, `STAR_TIERS`, `MAJOR_AMBER` / `MINOR_AMBER` — to keep
   the visual language consistent.

## Adding a new data source

The shape every fetcher follows:

```python
def fetch_X_range(args, start, end, tz) -> dict:
    """One HTTP request for the whole span."""

def _day_from_raw(raw, target_date, tz) -> dict:
    """Slice a per-day shape out of a range response."""

def get_X_many(args, dates, tz, cache=None) -> dict[date, dict]:
    """Cache-aware bulk fetcher used by cli._build_days."""
```

Anything that mirrors this contract will plug into the existing
`_build_days` flow without renderer changes.

## Distribution

- **Build** via `python -m build` — emits a wheel + sdist into `dist/`.
- **Install** via `pipx install <wheel>` or `pipx install git+https://...`.
- **Entry point** is the `fishin` console script (`fishin.cli:cli_main`).
- **Cache + config** live under XDG-compliant paths so they survive
  reinstall and respect `$XDG_CACHE_HOME` / `$XDG_CONFIG_HOME`.
