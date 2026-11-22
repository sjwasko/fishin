"""CLI argument guards. No network, no ephemeris: every case exits early."""

import pytest

from fishin import cli
from fishin.config import config_path, load_config, save_config

OFFLINE = ["--no-tides", "--no-weather"]


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


def test_bad_tz_returns_error_instead_of_traceback():
    assert cli.main([*OFFLINE, "--tz", "Not/Real"]) == 1


def test_bad_tz_is_not_saved():
    assert cli.main([*OFFLINE, "--tz", "Not/Real", "--save"]) == 1
    assert not config_path().exists()


def test_bad_tz_already_in_config_returns_error():
    save_config({"location": "X", "lat": 27.0, "lon": -82.0, "tz": "Not/Real"})
    assert cli.main(OFFLINE) == 1
    assert load_config()["tz"] == "Not/Real"  # left for the user to fix


def test_bad_mode_with_save_does_not_write_config():
    with pytest.raises(SystemExit):
        cli.main([*OFFLINE, "0", "--save"])
    assert not config_path().exists()


@pytest.mark.parametrize("argv", [
    ["0"], ["-3"], ["best", "0"], ["best", "-1"], ["best", "abc"],
    ["--days", "0"],
])
def test_bad_day_counts_exit_cleanly(argv):
    with pytest.raises(SystemExit) as exc:
        cli.main([*OFFLINE, *argv])
    assert isinstance(exc.value.code, str)  # message, not a traceback


@pytest.mark.parametrize("tokens, expected", [
    ([], {"type": "panel", "days": 1}),
    (["7"], {"type": "list", "days": 7}),
    (["best"], {"type": "best", "days": 14, "top": 5}),
    (["best", "3"], {"type": "best", "days": 3, "top": 5}),
    (["month"], {"type": "month"}),
])
def test_valid_modes_unchanged(tokens, expected):
    assert cli._parse_mode(tokens, 1) == expected


def test_unknown_mode_message_unchanged():
    with pytest.raises(SystemExit, match="unknown mode 'bogus'"):
        cli._parse_mode(["bogus"], 1)


@pytest.mark.parametrize("argv", [
    ["--lat", "200"], ["--lat", "-90.5"], ["--lon", "181"],
    ["--lon", "-180.01"], ["--lat", "nan"], ["--lon", "inf"],
])
def test_bad_coords_are_rejected_and_not_saved(argv):
    assert cli.main([*OFFLINE, *argv, "--save"]) == 1
    assert not config_path().exists()


@pytest.mark.parametrize("lat", [200.0, "abc", True])
def test_bad_lat_already_in_config_returns_error(lat):
    save_config({"location": "X", "lat": lat, "lon": -82.0,
                 "tz": "America/New_York"})
    assert cli.main(OFFLINE) == 1


@pytest.mark.parametrize("loc", [
    {"lat": 90.0, "lon": 180.0}, {"lat": -90.0, "lon": -180.0},
    {"lat": 0, "lon": 0}, {"lat": 27.3633, "lon": -82.5197},
])
def test_boundary_and_normal_coords_pass(loc):
    assert cli._bad_coord(loc) is None
