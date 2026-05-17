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
