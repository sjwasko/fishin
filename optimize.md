# `fishin` — Security & Optimization Audit

**Audit scope:** entire `fishin/` Python package (11 modules, ~1,600 LOC) at
baseline commit `482bcc8` — the current `main` HEAD, which is three
README-only commits ahead of the `v0.2.2` release tag. No source code has
changed between `v0.2.2` and the baseline; the findings here apply equally
to both.
**Audit branch:** `audit/security-hardening` — fixes implemented as atomic
commits, version bumped to `0.3.0`.
**Baseline anchor:** local tag `pre-audit-baseline` at `482bcc8`. The
remote rejected the tag push (HTTP 403 — repo policy appears to permit
branch pushes but not tag pushes), so the canonical rollback target is
simply the `main` branch on remote, which this audit does not touch.
**Auditor profile:** Principal Software Engineer + Senior Application Security
Auditor.

---

## 1. Executive Summary

`fishin` is a small, well-factored single-user CLI. Modules are single-purpose,
the data-flow graph is a clean DAG, network I/O is already coalesced into
range fetches with disk caching, and the only writable state is under the
user's own `$XDG_CACHE_HOME` / `$XDG_CONFIG_HOME`. There is **no web surface,
no database, no authentication, and no untrusted-network input**, which
narrows the realistic threat model to (a) local on-disk tampering of the
cache or config, and (b) defensive-coding hygiene at the I/O boundaries.

**Security posture:** generally good, with **one notable issue**: the response
cache used `diskcache`'s default pickle-based serialization. A local actor
with write access to `~/.cache/fishin/responses/` could plant a malicious
pickle blob and gain arbitrary code execution on the next `fishin`
invocation. The threat is real but low-probability for a single-user CLI
(attacker would need write access to your home directory, at which point
they can do worse things). This audit replaces pickle with a JSON-based
disk that preserves nested `datetime` values via a tagged codec — turning
the worst-case from "RCE" into "stale data."

**Performance posture:** already strong. The two big network sources (NOAA
tides, open-meteo weather) coalesce N-day requests into single round-trips
and cache deterministic results for a year. The remaining "optimizations"
in this report are micro-level (one duplicate haversine call) and have no
measurable runtime effect — they're noted for hygiene, not throughput.

**Code health:** three public functions were unused (single-day shim
wrappers); removed. One module-internal redundant import (`timedelta as
_td` inside a function body) was carried inside one of those dead
functions and disappears with it. Manual TOML escaping was fragile;
hardened with full control-character handling. Everything else read clean.

**Headline numbers**

| Bucket | Findings | Fixed on `audit/security-hardening` | Documented only |
|---|---:|---:|---:|
| Security — High | 1 | 1 | 0 |
| Security — Low / Info | 5 | 1 | 4 |
| Dead code | 3 | 3 | 0 |
| Refactor / quality | 3 | 2 | 1 |
| **Total** | **12** | **7** | **5** |

---

## 2. Findings Table

| # | Severity | File | Issue | Status |
|---|---|---|---|---|
| S-1 | **High** | `fishin/cache.py` | `diskcache` default pickle serialization → cache-poisoning RCE | **Fixed** |
| S-2 | Low | `fishin/config.py` | Manual TOML writer escaped only `\` and `"`; control chars corrupted file | **Fixed** |
| S-3 | Low | `fishin/stations.py` | Cached `stations.json` has no integrity check (data-integrity only) | Documented |
| S-4 | Info | `fishin/cli.py` | `--lat/--lon` without `--station` keeps stale station from prior resolve | Documented |
| S-5 | Info | All HTTP fetchers | `urllib.request.urlopen` calls — audited, verified clean | Documented |
| S-6 | Info | `fishin/geocode.py` | Free-form `--city` query → URL-encoded, no injection vector | Documented |
| D-1 | — | `fishin/tides.py` | `fetch_predictions` — unused single-day shim | **Removed** |
| D-2 | — | `fishin/tides.py` | `get_tide_day` — unused, also carried a redundant local import | **Removed** |
| D-3 | — | `fishin/weather.py` | `fetch_weather` — unused single-day shim | **Removed** |
| R-1 | — | `fishin/stations.py` | `haversine_km` computed N+1 times instead of N | **Fixed** |
| R-2 | — | `fishin/__main__.py` | `raise SystemExit(main())` at module top, no `__name__` guard | **Fixed** |
| R-3 | — | `fishin/tides.py` | `tide_curve` is O(N·M); negligible at N=48, M~6 | Documented |

---

## 3. Component-by-Component Analysis

### 3.1 `fishin/cache.py`

#### S-1 · Security Vulnerability (High) · Cache deserialization → arbitrary code execution

**Location:** `fishin/cache.py:31` (baseline) — `diskcache.Cache(str(path))`.

**Problem.** `diskcache.Cache` uses its base `Disk` class by default, which
serializes non-trivial values via `pickle`. Pickle is **not a data format** —
it's a sequence of opcodes for a stack-based VM that can construct
arbitrary objects and call arbitrary callables, including `os.system`,
`subprocess.Popen`, etc. The classic exploit is a class with a malicious
`__reduce__` method.

The cache lives at `~/.cache/fishin/responses/`. A local actor who can write
to that directory (another user account on a multi-tenant box, a compromised
helper process, a malicious tarball extracted into `$HOME`, a sloppy backup
restore from an untrusted source) can drop a crafted pickle blob keyed
under `tide:8726384:2026-06-01` (or any cache key). The next `fishin` run
calls `cache.get(...)`, which pickle-loads the blob, and the attacker's
payload executes with the user's privileges.

For a single-user CLI on a personal machine the probability is low —
write-to-home implies the attacker already has a foothold. But pickle is
the wrong tool: cached values here are plain JSON-shaped data (lists of
dicts of strings, floats, datetimes), so there is no reason to expose an
RCE surface.

**Before** (`cache.py:27-31`):

```python
@lru_cache(maxsize=1)
def get_cache() -> diskcache.Cache:
    path = _cache_dir()
    path.mkdir(parents=True, exist_ok=True)
    return diskcache.Cache(str(path))
```

**After** (`cache.py`, current branch):

```python
def _encode_dt(obj):
    if isinstance(obj, datetime):
        return {"__dt__": obj.isoformat()}
    raise TypeError(f"not JSON serializable: {type(obj).__name__}")


def _decode_dt(obj: dict):
    if len(obj) == 1 and "__dt__" in obj:
        return datetime.fromisoformat(obj["__dt__"])
    return obj


class FishinDisk(diskcache.Disk):
    """JSON-serializing Disk that preserves datetime objects."""

    def store(self, value, read, key=UNKNOWN):
        if not read:
            value = json.dumps(value, default=_encode_dt).encode("utf-8")
        return super().store(value, read, key=key)

    def fetch(self, mode, filename, value, read):
        data = super().fetch(mode, filename, value, read)
        if not read and isinstance(data, (bytes, bytearray)):
            return json.loads(data.decode("utf-8"), object_hook=_decode_dt)
        return data


@lru_cache(maxsize=1)
def get_cache() -> diskcache.Cache:
    path = _cache_dir()
    path.mkdir(parents=True, exist_ok=True)
    return diskcache.Cache(str(path), disk=FishinDisk)
```

JSON is a data-only format — no opcodes, no callable invocations. The
nested-datetime codec uses a sentinel-tagged dict so values round-trip
identically (verified locally with quote/backslash/newline/control-char
sample inputs). The cache directory was also bumped from `responses/` to
`responses-v2/` so legacy pickle blobs from 0.2.x don't trip JSON
deserialization on first read after upgrade — the old directory simply
becomes dormant.

---

### 3.2 `fishin/config.py`

#### S-2 · Security/Robustness (Low) · Hand-rolled TOML writer drops control chars

**Location:** `fishin/config.py:32-46` (baseline) — `save_config`.

**Problem.** The serializer escapes only `\` and `"`. If `values["location"]`
contains a newline (`\n`), carriage return (`\r`), tab (`\t`), or other C0
control character, the output is **invalid TOML**. `tomllib.load` will raise
on the next `fishin` run, locking the user out of their saved configuration
until they hand-edit the file. There is also no clean injection vector
today because the only place we ingest a `location` is Nominatim's
`display_name`, which is sanitized upstream — but the writer shouldn't
depend on the well-behavedness of an external service.

**Before** (`config.py:32-46`):

```python
def save_config(values: dict, path: Path | None = None) -> Path:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for key in ("location", "lat", "lon", "tz", "station"):
        if key not in values or values[key] is None:
            continue
        v = values[key]
        if isinstance(v, (int, float)):
            lines.append(f"{key} = {v}")
        else:
            escaped = str(v).replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'{key} = "{escaped}"')
    path.write_text("\n".join(lines) + "\n")
    return path
```

**After** (`config.py`, current branch):

```python
_TOML_ESCAPES = [
    ("\\", "\\\\"),
    ('"', '\\"'),
    ("\b", "\\b"),
    ("\t", "\\t"),
    ("\n", "\\n"),
    ("\f", "\\f"),
    ("\r", "\\r"),
]


def _toml_escape(s: str) -> str:
    """Escape a string for a TOML basic-string literal."""
    out = s
    for raw, esc in _TOML_ESCAPES:
        out = out.replace(raw, esc)
    return "".join(
        ch if ord(ch) >= 0x20 or ch in "\t" else f"\\u{ord(ch):04X}"
        for ch in out
    )


def save_config(values: dict, path: Path | None = None) -> Path:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for key in ("location", "lat", "lon", "tz", "station"):
        if key not in values or values[key] is None:
            continue
        v = values[key]
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            lines.append(f"{key} = {v}")
        else:
            lines.append(f'{key} = "{_toml_escape(str(v))}"')
    path.write_text("\n".join(lines) + "\n")
    return path
```

Full TOML basic-string escape coverage (`\b \t \n \f \r` per spec) with a
`\uXXXX` fallback for the remaining C0 controls. The `isinstance(v, bool)`
exclusion is a side-fix — Python treats `bool` as a subclass of `int`, so
the previous branch would write `lat = True`, which TOML accepts but is
almost certainly not what anyone wanted. Verified with adversarial
round-trips (quotes, backslashes, newlines, control bytes).

---

### 3.3 `fishin/tides.py`

#### D-1, D-2 · Dead Code · Two unused single-day shims

**Location (baseline):** `fishin/tides.py:69-72` (`fetch_predictions`) and
`fishin/tides.py:113-130` (`get_tide_day`).

**Problem.** Both functions are public (no leading underscore) and both are
defined-but-never-called. `grep -rn` across `fishin/*.py` returns only the
definitions, no callers. `cli._build_days` constructs its own padded date
range and calls `get_tides_many` directly, so the `get_tide_day` wrapper
adds nothing. `fetch_predictions` is a one-line shim around the range
fetcher.

`get_tide_day` also re-imports `timedelta as _td` inside the function body
even though `timedelta` is already imported at module top — a small code
smell that gets removed with the function.

**Before**:

```python
def fetch_predictions(station: str, target_date: date, tz: ZoneInfo,
                      timeout: float = 10.0) -> list[dict]:
    """Single-day shim around `fetch_predictions_range`."""
    return fetch_predictions_range(station, target_date, target_date, tz, timeout)


def get_tide_day(station: str, target_date: date, tz: ZoneInfo,
                 cache=None) -> tuple[list[dict], list[dict]]:
    """Return (target_day_events, neighbor_context)."""
    from datetime import timedelta as _td
    prior = target_date - _td(days=1)
    after = target_date + _td(days=1)
    days = get_tides_many(station, [prior, target_date, after], tz, cache=cache)
    context: list[dict] = []
    if days.get(prior):
        context.append(days[prior][-1])
    if days.get(after):
        context.append(days[after][0])
    return days[target_date], context
```

**After:** both functions deleted (commit `cbc671d`). Since these were
public symbols, the package version is bumped to `0.3.0` to signal the
breaking change to any external importer (none in the current codebase,
but external integrations could exist).

#### R-3 · Inefficiency (Negligible) · `tide_curve` per-point linear scan

**Location:** `fishin/tides.py:151-176` (current).

**Problem.** For each of `n_points` (default 48), the inner loop walks the
full event list to find the bracketing `prev_ev`/`next_ev`. That's O(N·M).
With N=48 and M~6 it's ~288 comparisons total — negligible. An O(N+M)
pointer-walk (events are already sorted) would be cleaner, but the
performance delta is unmeasurable.

**Status:** documented only — not worth code churn. If the curve ever
needs to be drawn at minute resolution (1,440 points), revisit.

---

### 3.4 `fishin/weather.py`

#### D-3 · Dead Code · Unused single-day shim

**Location (baseline):** `fishin/weather.py:159-163`.

**Before**:

```python
def fetch_weather(lat: float, lon: float, target_date: date, tz: ZoneInfo,
                  timeout: float = 10.0) -> dict:
    """Single-day shim around the range fetcher."""
    raw = fetch_weather_range(lat, lon, target_date, target_date, tz, timeout)
    return _day_from_raw(raw, target_date, tz)
```

**After:** removed (commit `cbc671d`). `cli._build_days` calls
`get_weather_many` directly; nothing else uses this wrapper.

---

### 3.5 `fishin/stations.py`

#### R-1 · Inefficiency · `haversine_km` computed N+1 times

**Location:** `fishin/stations.py:68-76` (baseline) — `nearest_station`.

**Problem.** `min(stations, key=haversine_km(...))` calls the key N times to
find the winner; the function then recomputes the *same* haversine on the
winner to attach `distance_km`. So `N+1` distance calculations where `N`
suffices. The catalog has ~1,500 stations and this is called once per
geocode, so the runtime cost is invisible — the issue is hygiene, not
throughput.

**Before**:

```python
def nearest_station(lat: float, lon: float, refresh: bool = False) -> dict:
    stations = load_stations(refresh=refresh)
    if not stations:
        raise LookupError("no NOAA stations available")
    best = min(stations, key=lambda s: haversine_km(lat, lon, s["lat"], s["lng"]))
    best = dict(best)
    best["distance_km"] = haversine_km(lat, lon, best["lat"], best["lng"])
    return best
```

**After** (`stations.py`, current branch):

```python
def nearest_station(lat: float, lon: float, refresh: bool = False) -> dict:
    stations = load_stations(refresh=refresh)
    if not stations:
        raise LookupError("no NOAA stations available")
    distance, _, best = min(
        (haversine_km(lat, lon, s["lat"], s["lng"]), i, s)
        for i, s in enumerate(stations)
    )
    return {**best, "distance_km": distance}
```

The `enumerate` index sits between the float and the dict as a tuple
tiebreaker — without it, two stations with identical haversine distance
(astronomically unlikely on floats but defensible) would make tuple
comparison fall through to comparing the dicts themselves, which raises
`TypeError` in Python 3.

#### S-3 · Security (Low) · Cached `stations.json` has no integrity check

**Location:** `fishin/stations.py:42-44` (current).

**Problem.** Same local-tamper class as the diskcache finding, but the
on-disk catalog is plain JSON. `json.load` is safe (data-only), so the
worst-case is wrong nearest-station selection (which has cosmetic
downstream effects on tide-station selection) — not RCE. Mitigations
(HMAC, signature, ETag, size sanity check) are disproportionate for a
single-user CLI.

**Status:** documented only.

---

### 3.6 `fishin/geocode.py`

#### S-6 · Security (Info) · Verified clean — no injection in `--city` handling

**Location:** `fishin/geocode.py:40-75`.

**What was checked.** Free-form user input (`--city "..."`) is normalized
(lowercased + whitespace-collapsed), then URL-encoded via
`urllib.parse.urlencode` before being appended to the hardcoded HTTPS
Nominatim URL. There is no path of user input into a shell, SQL string,
HTML output, or filesystem path. The User-Agent string is built from
`__version__` (a hardcoded constant), so there is no header-injection
surface either. The 10s timeout prevents indefinite hangs.

**Status:** documented as audited-and-clean.

---

### 3.7 `fishin/cli.py`

#### S-4 · Logic (Info) · `--lat/--lon` without `--station` keeps stale station

**Location:** `fishin/cli.py:202-223` (baseline).

**Problem.** Resolution order is: config → optional `--city` resolve →
explicit flags overlay. If a user has a saved Sarasota config and runs
`fishin --lat 30.27 --lon -97.74` (Austin), the latitude and longitude
become Austin but the `station` field stays at `8726384` (Sarasota Bay).
The tide fetch then runs against a station ~1,000 km from the requested
coordinates — silently producing wrong data.

**Why not fix.** A clean fix requires re-resolving the nearest station
when lat/lon move significantly, which means a network call (or at least
loading the station catalog). That's a behavioral change and a scope
creep relative to a security/optimization audit. A defensive alternative
(drop the inherited station if lat/lon are overridden without an explicit
station) would silently disable tides in a confusing way.

**Status:** documented as a design note — recommend either (a) re-running
`nearest_station` on lat/lon override, or (b) printing a warning when
the inherited station is >100 km from the overridden coordinates.

---

### 3.8 `fishin/__main__.py`

#### R-2 · Quality · Missing `__name__` guard

**Location:** `fishin/__main__.py:3` (baseline).

**Problem.** `raise SystemExit(main())` at module top-level worked for the
intended `python -m fishin` invocation, but any code that simply imported
`fishin.__main__` for introspection would unconditionally invoke `main()`
and kill the interpreter. Cosmetic — but the standard idiom is cheap and
removes a footgun.

**Before**:

```python
from .cli import main

raise SystemExit(main())
```

**After** (`__main__.py`, current branch):

```python
from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
```

---

### 3.9 All HTTP fetchers — `urllib.request.urlopen` review

#### S-5 · Security (Info) · Audited clean

**Locations:** `tides.py:51`, `weather.py:105`, `geocode.py:62`,
`stations.py:46`.

**What was checked.**

- All four URLs are hardcoded HTTPS endpoints. No user input controls the
  scheme, host, or path — only query-string parameters via `urlencode`. No
  SSRF surface.
- All calls pass an explicit `timeout` (10–15s). No risk of indefinite
  hangs on slow/malicious upstream.
- Python's stdlib `urllib` uses the system trust store for TLS verification
  by default. No custom `SSLContext` overrides anywhere.
- No response body is `eval`/`exec`/`compile`ed. JSON is parsed via
  stdlib `json.load`, which is safe.

**Status:** documented as audited-and-clean.

---

## 4. What was implemented on the `audit/security-hardening` branch

Each fix is its own atomic commit so any individual change is a single
`git revert <sha>` away. The pre-audit state is the `main` branch on
remote (commit `482bcc8`), also tagged locally as `pre-audit-baseline`.

```
56607c2 Bump version 0.2.2 → 0.3.0 for security-hardening release
1858c9b Guard __main__ entry with __name__ check
8c9558f Avoid double haversine computation in nearest_station
4423542 Escape control chars in TOML config writer
cb0eb3e Harden response cache against pickle deserialization
cbc671d Remove unused single-day shim fetchers (dead code)
```

---

## 5. Smoke-test plan for the audit branch

In a clean environment with deps installed, paste **one line at a time** —
avoid combining commands with `&&` and avoid inline `# comments` on the
same line as a command. zsh on macOS doesn't treat `#` as a comment in
interactive mode, so an inline comment gets fed to the program as
arguments (e.g. `fishin # 2nd run` parses as `fishin '#' '2nd' 'run'` →
`unknown mode '#'`).

Install / upgrade — paste as a **single line**:
```
pipx install --force git+https://github.com/sjwasko/fishin@audit/security-hardening
```

Verify the right version is installed:
```
pipx list
```
Should show `fishin 0.3.0`.

Clear the cache so the next two runs exercise WRITE then READ explicitly:
```
rm -rf ~/.cache/fishin/responses-v2/
```

Cache WRITE — populates the cache from network:
```
fishin
```

Cache READ — same invocation, must render an identical panel without
errors. This is the highest-regression-risk step (validates the
`FishinDisk` JSON+datetime codec round-trip):
```
fishin
```

All view modes:
```
fishin 7
fishin month
fishin best 14
fishin --date 2026-06-15
```

Geocode + config write (validates the `_toml_escape` fix):
```
fishin --city "key west fl" --save
cat ~/.config/fishin/config.toml
```
The config file must be valid TOML with the `location`/`lat`/`lon`/`tz`/
`station` keys present. A subsequent plain `fishin` should pick it up.

Offline mode — exercises code paths unaffected by the cache change:
```
fishin --no-tides --no-weather
```

Legacy cache dormancy — the old `~/.cache/fishin/responses/` directory
(if present from 0.2.x) should be ignored. Confirm new cache files land
under `responses-v2/` only:
```
ls ~/.cache/fishin/
```

---

## 6. Rollback paths

- **One fix misbehaves:** `git revert <sha>` on the audit branch, repush,
  retest. Each fix is independent.
- **Whole branch bad:** ignore the branch entirely. The `main` line is
  untouched. `pipx install git+https://github.com/sjwasko/fishin@main`
  (or `@v0.2.2`) returns to the prior good state.
- **Already merged and regretting it:** `git revert <merge-sha> -m 1` on
  `main`, or hard-reset `main` back to commit `482bcc8` if nothing else
  has landed since the merge.
