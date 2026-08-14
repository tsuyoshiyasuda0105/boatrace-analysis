"""Fixture: boatrace.jp raceresult, 2026-08-14 Kiryu race 1.

Downloaded from the official raceresult endpoint on 2026-08-15 and retained as
the complete UTF-8 single-race page without trimming.
"""

from pathlib import Path

from src.parsers.result_html import parse_result_html


FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "parsers"
    / "result_html"
    / "raceresult_20260814_01_01.html"
)


def test_parse_result_html_real_fixture_golden_values() -> None:
    result = parse_result_html(FIXTURE.read_text(encoding="utf-8"))

    assert result is not None
    assert {"boats", "payouts", "race_kimarite", "weather"} <= result.keys()
    assert len(result["boats"]) == 6
    assert result["boats"][0] == {
        "racer_boat_number": 4,
        "racer_place_number": 1,
        "racer_race_time": "1'51\"6",
    }
    assert [boat["racer_boat_number"] for boat in result["boats"]] == [4, 5, 3, 6, 2, 1]
    assert result["race_kimarite"] == "まくり差し"
    assert result["payouts"]["trifecta"] == [
        {"combination": "4-5-3", "payout": 3080, "popularity": 5}
    ]
    assert result["payouts"]["win"] == [
        {"combination": "4", "payout": 500, "popularity": None}
    ]
    assert result["weather"] == {
        "weather_number": 1,
        "wind_speed": 4,
        "wind_direction_number": 10,
        "wave_height": 3,
        "temperature": 28.0,
        "water_temperature": 26.0,
    }


def test_parse_result_html_empty_input_is_safe() -> None:
    assert parse_result_html("") is None
