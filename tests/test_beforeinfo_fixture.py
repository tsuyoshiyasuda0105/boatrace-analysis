"""Fixture: boatrace.jp beforeinfo, 2026-05-06 Kiryu race 1.

Copied from data/raw/_test/beforeinfo_20260506_01_01.html; the complete
single-race page is retained without structural trimming.
"""

from pathlib import Path

from src.parsers.beforeinfo import parse_beforeinfo


FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "parsers"
    / "beforeinfo"
    / "beforeinfo_20260506_01_01.html"
)


def test_parse_beforeinfo_real_fixture_golden_values() -> None:
    page = parse_beforeinfo(FIXTURE.read_text(encoding="utf-8"))

    assert {
        "boats",
        "stable_plate",
        "weather_number",
        "wind_speed",
        "wind_direction_number",
        "wave_height",
        "temperature",
        "water_temperature",
    } <= page.keys()
    assert len(page["boats"]) == 6
    assert [boat["boat_number"] for boat in page["boats"]] == [1, 2, 3, 4, 5, 6]
    assert page["boats"][0] == {
        "boat_number": 1,
        "parts": [],
        "exhibition_time": 6.81,
        "tilt_adjustment": 0.0,
        "weight_adjustment": None,
        "course_number": 1,
        "start_timing_exhibition": 0.18,
    }
    assert page["boats"][4]["start_timing_exhibition"] == -0.02
    assert (
        page["weather_number"],
        page["wind_speed"],
        page["wind_direction_number"],
        page["wave_height"],
        page["temperature"],
        page["water_temperature"],
    ) == (2, 1, 8, 1, 17.0, 13.0)


def test_parse_beforeinfo_empty_input_is_safe() -> None:
    page = parse_beforeinfo("")
    assert page["boats"] == []
    assert page["stable_plate"] == 0
    assert all(
        page[key] is None
        for key in (
            "weather_number",
            "wind_speed",
            "wind_direction_number",
            "wave_height",
            "temperature",
            "water_temperature",
        )
    )
