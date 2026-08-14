"""Fixture: Shimonoseki original exhibition, 2026-07-29 race 12.

Copied from data/raw/original_exhibition/2026-07-29/
19_12_shimonoseki_group_cyokuzen.html; the complete single-race page remains
without structural trimming.
"""

from pathlib import Path

from src.parsers.original_exhibition import parse_original_exhibition


FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "parsers"
    / "original_exhibition"
    / "20260729_19_12_shimonoseki.html"
)


def test_parse_original_exhibition_real_fixture_golden_values() -> None:
    rows = parse_original_exhibition(FIXTURE.read_text(encoding="utf-8"))

    assert len(rows) == 6
    assert [row["boat_number"] for row in rows] == [1, 2, 3, 4, 5, 6]
    assert all(
        {"boat_number", "raw_text", "lap_time", "turn_time", "straight_time"}
        <= row.keys()
        for row in rows
    )
    assert rows[0] == {
        "boat_number": 1,
        "raw_text": "西村　　　歩",
        "lap_time": 37.5,
        "turn_time": 5.33,
        "straight_time": 7.33,
    }
    assert (rows[-1]["lap_time"], rows[-1]["turn_time"], rows[-1]["straight_time"]) == (
        37.69,
        5.73,
        7.44,
    )


def test_parse_original_exhibition_empty_input_is_safe() -> None:
    assert parse_original_exhibition("") == []
