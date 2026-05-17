# fishin

Terminal solunar, tide, and weather forecast — one command, full picture, btop-dense layout.

`fishin` rolls a solunar app, a tide app (xtides-style), and `wttr.in` into a single
terminal panel. Solunar major/minor periods, sun/moon ephemera, NOAA tide
predictions, open-meteo weather, and a per-day "best fishing window" cross-correlation
all rendered in ~12 terminal lines per day.

## Install

```bash
pipx install fishin
```

First run downloads the JPL DE421 ephemeris (~17 MB) into the working directory.
NOAA + open-meteo responses are cached at `~/.cache/fishin/`.

## Quick start

```bash
fishin                          # full panel for today, default location
fishin --city "sarasota fl"     # geocode and resolve nearest tide station
fishin --save                   # write resolved location to ~/.config/fishin/config.toml
fishin 7                        # compact 7-day list view
fishin month                    # 30-day calendar grid with star ratings
fishin best 14                  # next 14 days ranked, top 5 highlighted
```

## Modes

| Command | Output |
|---|---|
| `fishin` | One full panel for today |
| `fishin N` | N-day list view, one row per day |
| `fishin month` | Calendar grid for the month of `--date` |
| `fishin best N` | Next N days sorted by score, top 5 highlighted |
| `fishin --days N` | Legacy: render N full panels back-to-back |

Skip network fetches with `--no-tides` / `--no-weather` (e.g. offline or for quick recompute).

## Configuration

`~/.config/fishin/config.toml`:

```toml
location = "Sarasota, FL"
lat = 27.3366
lon = -82.5313
tz = "America/New_York"
station = "8726083"
```

Resolution order: explicit flags > `--city` > config file > built-in Sarasota default.

## Data sources

- **Astronomy**: JPL DE421 ephemeris via [`skyfield`](https://rhodesmill.org/skyfield/)
- **Tides**: NOAA CO-OPS predictions (`api.tidesandcurrents.noaa.gov`)
- **Weather**: open-meteo (`api.open-meteo.com`, free, no key)
- **Geocoding**: OpenStreetMap Nominatim
- **Timezone**: `timezonefinder` (offline)
