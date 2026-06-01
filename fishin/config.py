"""Read/write `~/.config/fishin/config.toml`.

The file is intentionally flat — a handful of top-level keys, no sections. We
read with stdlib `tomllib` (3.11+) and write by hand to avoid pulling in
`tomli-w` for five lines of output.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path


_VALID_KEYS = {"location", "lat", "lon", "tz", "station"}

# TOML basic-string escapes per https://toml.io/en/v1.0.0#string. Backslash
# must come first or it'll re-escape the escapes we just added.
_TOML_ESCAPES = [
    ("\\", "\\\\"),
    ('"', '\\"'),
    ("\b", "\\b"),
    ("\t", "\\t"),
    ("\n", "\\n"),
    ("\f", "\\f"),
    ("\r", "\\r"),
]


def config_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "fishin" / "config.toml"


def load_config(path: Path | None = None) -> dict:
    path = path or config_path()
    if not path.exists():
        return {}
    with path.open("rb") as f:
        data = tomllib.load(f)
    return {k: v for k, v in data.items() if k in _VALID_KEYS}


def _toml_escape(s: str) -> str:
    """Escape a string for a TOML basic-string literal.

    Covers backslash, double-quote, and the common control characters
    (\\b \\t \\n \\f \\r). Other C0 controls are encoded as \\uXXXX so the
    resulting file is always parseable by tomllib — a stray newline in a
    geocoded display name would otherwise corrupt the config.
    """
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
