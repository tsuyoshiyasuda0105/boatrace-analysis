"""Fixture: official K240105.TXT, 2024-01-05 Omura race 1.

The fixture was byte-sliced from the official mbrace K archive. Only the Omura
venue header and race 1 block are retained; every retained line remains cp932.
"""

from datetime import date
import hashlib
from pathlib import Path

from src.parsers.official_k import parse_k_text


FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "parsers"
    / "official_k"
    / "K240105_omura_01.TXT"
)


def test_parse_official_k_real_fixture_golden_values() -> None:
    raw = FIXTURE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == (
        "18714a334ce85841a8a7998903d16064a6a4a69fdd6194e1fea6ec7889226ec5"
    )

    races = parse_k_text(raw.decode("cp932"), date(2024, 1, 5))

    assert len(races) == 1
    race = races[0]
    assert {
        "race_id",
        "race_date",
        "stadium_number",
        "race_number",
        "wind_speed",
        "wave_height",
        "weather_text",
        "kimarite",
        "results",
        "payouts",
    } <= race.keys()
    assert (race["race_id"], race["stadium_number"], race["race_number"]) == (
        "20240105-24-01",
        24,
        1,
    )
    assert (race["wind_speed"], race["wave_height"], race["weather_text"]) == (
        2,
        1,
        "晴",
    )
    assert race["kimarite"] == "逃げ"
    assert len(race["results"]) == 6
    assert race["results"][0] == {
        "boat_number": 1,
        "racer_number": 3773,
        "racer_name": "津 留  浩一郎",
        "finishing_position": 1,
        "course_number": 1,
        "start_timing": 0.16,
        "race_time": "1.49.5",
        "remarks": None,
        "kimarite": "逃げ",
    }
    assert {
        (row["bet_type"], row["combination"], row["payout"])
        for row in race["payouts"]
    } >= {
        ("win", "1", 110),
        ("trifecta", "1-4-5", 7970),
        ("trio", "1-4-5", 2040),
    }


def test_parse_official_k_empty_input_is_safe() -> None:
    assert parse_k_text("", date(2024, 1, 5)) == []
