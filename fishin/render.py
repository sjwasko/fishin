"""Terminal rendering via rich.

Takes the dicts built by `fishin.astro` (optionally augmented with tides) and
prints them as compact panels. The caller supplies a `Console` (or one is
created on demand) so output is testable and pipeable.
"""

from __future__ import annotations

from datetime import datetime

from rich import box
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from .tides import sparkline
from .weather import code_info, hpa_to_inhg, wind_arrow


# Retro CRT amber palette — bright for Major + day score, muted for Minor.
# Hex values render true-color on modern terminals; rich downsamples to 256
# on older ones automatically.
MAJOR_AMBER = "#FFB000"
MINOR_AMBER = "#B8860B"

RATING_COLORS = {
    "Excellent": "bright_green",
    "Good": "cyan",
    "Average": "yellow",
    "Poor": "red",
}

STAR_TIERS = {
    "Excellent": "★★★★",
    "Good":      "★★★ ",
    "Average":   "★★  ",
    "Poor":      "★   ",
}


def fmt_t(dt: datetime | None) -> str:
    if dt is None:
        return "  --   "
    return dt.strftime("%I:%M%p").lstrip("0").lower().replace("am", "a").replace("pm", "p")


def fmt_period(start_end) -> str:
    s, e = start_end
    return f"{fmt_t(s)} – {fmt_t(e)}"


def _fmt_short_t(dt: datetime) -> str:
    return dt.strftime("%I%p").lower().replace("am", "a").replace("pm", "p")


def fmt_factor(f: dict) -> str:
    """Pretty-print a single window factor for the Best Times section."""
    t = f["type"]
    if t == "sunrise":
        return "Sunrise"
    if t == "sunset":
        return "Sunset"
    if t == "tide":
        kind = "Low" if f["kind"] == "L" else "High"
        return f"{kind} {fmt_t(f['time']).strip()}"
    if t == "weather":
        sub = f["subtype"]
        if sub == "calm":
            return "calm"
        if sub == "wind":
            return f"wind {f['value']:.0f}mph"
        if sub == "rain":
            return f"rain {f['value']}%"
    return "?"


_FACTOR_SHORT = {
    "sunrise": "↑Sun",
    "sunset":  "↓Sun",
}


def _factor_short(f: dict) -> str:
    t = f["type"]
    if t in _FACTOR_SHORT:
        return _FACTOR_SHORT[t]
    if t == "tide":
        return f["kind"]
    if t == "weather":
        sub = f["subtype"]
        return {"calm": "calm", "wind": "wnd", "rain": "rain"}.get(sub, sub)
    return "?"


def _fmt_tide_event(ev: dict) -> str:
    color = "bright_blue" if ev["kind"] == "H" else "cyan"
    return f"[bold {color}]{ev['kind']}[/] {fmt_t(ev['time']):<7} {ev['height']:>5.2f}ft"


def render_day(day: dict, location_name: str, console: Console | None = None) -> None:
    console = console or Console()

    score_pct = int(round(day["score"] * 100))
    date_str = day["date"].strftime("%a %b %d %Y")

    tbl = Table.grid(padding=(0, 2), expand=False)
    tbl.add_column(style="bold")
    tbl.add_column()
    tbl.add_column(style="bold")
    tbl.add_column()

    phase_cell = f"{day['phase_symbol']} {day['phase_name']}"

    trend_cell = ""
    if day.get("trend"):
        trend = day["trend"]
        # Lock sparkline scale to 0..1 so the same trend reads consistently
        # across days and across sessions.
        trend_cell = f"7d: [bright_green]{sparkline(trend, lo=0.0, hi=1.0)}[/]"

    tbl.add_row("☀ Sunrise", fmt_t(day["sunrise"]),
                "☽ Moonrise", f"{fmt_t(day['moonrise'])}  {phase_cell}")
    overhead = (fmt_t(day["transits"][0])
                if day["transits"] else "  --   ")
    noon_right = f"{overhead}  {trend_cell}" if trend_cell else overhead
    tbl.add_row("  Noon", fmt_t(day["solar_noon"]),
                "  Overhead", noon_right)
    tbl.add_row("  Sunset", fmt_t(day["sunset"]),
                "  Moonset", fmt_t(day["moonset"]))

    major_lines = [fmt_period(p) for p in day["majors"]]
    minor_lines = [fmt_period(p) for p in day["minors"]]
    max_rows = max(len(major_lines), len(minor_lines), 1)
    for i in range(max_rows):
        m = major_lines[i] if i < len(major_lines) else ""
        n = minor_lines[i] if i < len(minor_lines) else ""
        label_l = f"[{MAJOR_AMBER}]★ Major[/]" if i == 0 else ""
        label_r = f"[{MINOR_AMBER}]☆ Minor[/]" if i == 0 else ""
        m_cell = f"[bold {MAJOR_AMBER}]{m}[/]" if m else ""
        n_cell = f"[{MINOR_AMBER}]{n}[/]" if n else ""
        tbl.add_row(label_l, m_cell, label_r, n_cell)

    if day.get("tides"):
        tides = day["tides"]
        events = tides["events"]
        for i in range(0, len(events), 2):
            label = "◢ Tides" if i == 0 else ""
            left = _fmt_tide_event(events[i])
            right = _fmt_tide_event(events[i + 1]) if i + 1 < len(events) else ""
            combined = f"{left}   {right}" if right else left
            tbl.add_row(label, combined, "", "")
        curve = tides["curve"]
        if curve:
            tbl.add_row("", f"[bright_blue]{sparkline(curve)}[/]", "", "")

    if day.get("weather"):
        wx = day["weather"]
        noon = next((p[1] for p in wx["periods"]
                     if p[0] == "Noon" and p[1]), None)
        if noon:
            label, glyph = code_info(noon["code"], False)
            wind = f"{wind_arrow(noon['wind_deg'])}{noon['wind_mph']:.0f}mph"
            pressure = f"{hpa_to_inhg(noon['pressure_hpa']):.2f}\"Hg"
            precip = f"{noon['precip_prob']:>3d}% precip"
            humid = f"{noon['humidity']:>3d}% RH"
            headline = (f"{glyph} {noon['temp']:.0f}°F  {label}  "
                        f"{wind}  {pressure}  {humid}  {precip}")
            tbl.add_row("☁ Weather", headline, "", "")

        chunks = []
        for label, sample in wx["periods"]:
            if not sample:
                continue
            _, g = code_info(sample["code"], label == "Night")
            chunks.append(
                f"{_fmt_short_t(sample['time'])} {g} {sample['temp']:.0f}°"
            )
        if chunks:
            tbl.add_row("", "  ".join(chunks), "", "")

    notable = [w for w in (day.get("windows") or []) if w["factors"]]
    for i, w in enumerate(notable[:2]):
        period = f"{fmt_t(w['period'][0])} – {fmt_t(w['period'][1])}"
        factor_str = " · ".join([w["label"]] + [fmt_factor(f) for f in w["factors"]])
        label = "⏱ Best" if i == 0 else ""
        if i == 0:
            cell = f"[bold yellow]{period}[/]  {factor_str}"
        else:
            cell = f"[dim]{period}  {factor_str}[/]"
        tbl.add_row(label, cell, "", "")

    title = Text()
    title.append(location_name, style="dim")
    title.append("  ·  ", style="dim")
    title.append(date_str, style="bold white")
    title.append("  ·  ", style="dim")
    title.append(f"{score_pct}% {day['rating']}", style=f"bold {MAJOR_AMBER}")

    console.print(Panel(tbl, title=title, expand=False, border_style="dim"))


# --------------------------------------------------------------- multi-day

def _peak_period(periods: list, sunrise, sunset):
    """Pick the daytime occurrence of a major/minor period if any, else the
    first one. Used to surface the most actionable time in a compact row."""
    if not periods:
        return None
    if sunrise and sunset:
        for p in periods:
            if sunrise <= p[0] <= sunset:
                return p
    return periods[0]


def _tide_summary(day: dict) -> tuple[str, str]:
    """Return (sparkline_24, range_label) for the list/best views."""
    if not day.get("tides"):
        return "", ""
    events = day["tides"]["events"]
    curve = day["tides"]["curve"]
    spark = ""
    if curve:
        every_other = curve[::2]  # 48 → 24 points
        spark = f"[bright_blue]{sparkline(every_other, lo=None, hi=None)}[/]"
    hs = [e["height"] for e in events if e["kind"] == "H"]
    ls = [e["height"] for e in events if e["kind"] == "L"]
    if hs and ls:
        range_label = f"{max(hs):>4.1f}/{min(ls):>+4.1f}"
    else:
        range_label = ""
    return spark, range_label


def _wx_summary(day: dict) -> str:
    if not day.get("weather"):
        return ""
    sample = next((p[1] for p in day["weather"]["periods"]
                   if p[0] == "Noon" and p[1]), None)
    if not sample:
        return ""
    _, g = code_info(sample["code"], False)
    return f"{g} {sample['temp']:.0f}°  {sample['precip_prob']:>2d}%"


def _best_summary(day: dict) -> str:
    notable = [w for w in (day.get("windows") or []) if w["factors"]]
    if not notable:
        return ""
    w = notable[0]
    abbrev = [_factor_short(f) for f in w["factors"]]
    return f"{fmt_t(w['period'][0])} {w['label'][:3]}+{'+'.join(abbrev)}"


def _list_row(day: dict) -> tuple:
    score_pct = int(round(day["score"] * 100))
    score_cell = (f"[bold {MAJOR_AMBER}]{score_pct:>3d}% "
                  f"{STAR_TIERS[day['rating']]}[/]")
    sun = fmt_t(day["sunrise"])
    moon = f"{fmt_t(day['moonrise'])} {day['phase_symbol']}"
    major = fmt_t(_peak_period(day.get("majors", []),
                               day.get("sunrise"), day.get("sunset"))[0]) \
        if day.get("majors") else ""
    minor = fmt_t(_peak_period(day.get("minors", []),
                               day.get("sunrise"), day.get("sunset"))[0]) \
        if day.get("minors") else ""
    spark, trange = _tide_summary(day)
    tide_cell = f"{spark} {trange}".strip()
    return (
        day["date"].strftime("%a %b %d"),
        score_cell, sun, moon, major, minor,
        tide_cell, _wx_summary(day), _best_summary(day),
    )


def _list_table() -> Table:
    tbl = Table(box=box.SIMPLE, padding=(0, 1), show_header=True,
                header_style="dim", expand=False)
    tbl.add_column("Day")
    tbl.add_column("Score", justify="right")
    tbl.add_column("Sun", justify="right")
    tbl.add_column("Moon", justify="right")
    tbl.add_column("Major", justify="right")
    tbl.add_column("Minor", justify="right")
    tbl.add_column("Tide", justify="left")
    tbl.add_column("Wx", justify="left")
    tbl.add_column("Best", justify="left")
    return tbl


def render_list(days: list[dict], location: str,
                console: Console | None = None) -> None:
    console = console or Console()
    if not days:
        return

    tbl = _list_table()
    for day in days:
        tbl.add_row(*_list_row(day))

    title = Text()
    title.append(location, style="dim")
    title.append("  ·  ", style="dim")
    title.append(
        f"{days[0]['date'].strftime('%a %b %d')}"
        f" – {days[-1]['date'].strftime('%a %b %d %Y')}",
        style="bold white",
    )
    console.print(Panel(tbl, title=title, expand=False, border_style="dim"))


def render_best(days: list[dict], location: str, top_n: int = 5,
                console: Console | None = None) -> None:
    console = console or Console()
    if not days:
        return

    ranked = sorted(days, key=lambda d: -d["score"])

    tbl = Table(box=box.SIMPLE, padding=(0, 1), show_header=True,
                header_style="dim", expand=False)
    tbl.add_column("#", justify="right")
    tbl.add_column("Day")
    tbl.add_column("Score", justify="right")
    tbl.add_column("Sun", justify="right")
    tbl.add_column("Moon", justify="right")
    tbl.add_column("Major", justify="right")
    tbl.add_column("Minor", justify="right")
    tbl.add_column("Tide", justify="left")
    tbl.add_column("Wx", justify="left")
    tbl.add_column("Best", justify="left")

    for i, day in enumerate(ranked, start=1):
        cells = _list_row(day)
        rank_cell = (f"[bold yellow]{i}[/]"
                     if i <= top_n else f"[dim]{i}[/]")
        style = None if i <= top_n else "dim"
        tbl.add_row(rank_cell, *cells, style=style)

    title = Text()
    title.append(location, style="dim")
    title.append("  ·  ", style="dim")
    title.append(
        f"top {top_n} of {len(days)} days"
        f"  ·  {days[0]['date'].strftime('%b %d')} →"
        f" {days[-1]['date'].strftime('%b %d %Y')}",
        style="bold white",
    )
    console.print(Panel(tbl, title=title, expand=False, border_style="dim"))


def render_month(days: list[dict], location: str,
                 console: Console | None = None) -> None:
    console = console or Console()
    if not days:
        return

    headers = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]

    def col_for(d) -> int:
        return (d.weekday() + 1) % 7  # Sun=0 .. Sat=6

    def fmt_cell(day: dict) -> str:
        n = day["date"].day
        stars = STAR_TIERS[day["rating"]]
        score_pct = int(round(day["score"] * 100))
        return (f"[dim]{n:>2}[/]  [{MAJOR_AMBER}]{stars}[/]\n"
                f"     [{MAJOR_AMBER}]{score_pct:>3d}%[/]")

    rows: list[list[str]] = []
    current = [""] * 7
    for day in days:
        c = col_for(day["date"])
        if c == 0 and any(current):
            rows.append(current)
            current = [""] * 7
        current[c] = fmt_cell(day)
    if any(current):
        rows.append(current)

    tbl = Table(box=box.SIMPLE, padding=(0, 1), show_header=True,
                header_style="bold dim", expand=False)
    for h in headers:
        tbl.add_column(h, justify="left", min_width=8)
    for row in rows:
        tbl.add_row(*row)

    first = days[0]["date"]
    title = Text()
    title.append(location, style="dim")
    title.append("  ·  ", style="dim")
    title.append(first.strftime("%B %Y"), style="bold white")
    console.print(Panel(tbl, title=title, expand=False, border_style="dim"))
