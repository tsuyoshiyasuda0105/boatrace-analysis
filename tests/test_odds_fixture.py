"""Fixture: boatrace.jp trifecta odds, 2026-05-06 Kiryu race 1.

Copied from data/raw/_test/odds3t_20260506_01_01.html; the complete single-race
page is retained without structural trimming.
"""

from pathlib import Path

from src.parsers.odds import parse_trifecta_odds


FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "parsers"
    / "odds"
    / "odds3t_20260506_01_01.html"
)


def test_parse_odds_real_fixture_golden_values() -> None:
    odds = parse_trifecta_odds(FIXTURE.read_text(encoding="utf-8"))

    assert len(odds) == 120
    assert set(odds) == {
        f"{first}-{second}-{third}"
        for first in range(1, 7)
        for second in range(1, 7)
        for third in range(1, 7)
        if len({first, second, third}) == 3
    }
    assert odds["1-2-3"] == 11.0
    assert odds["1-2-6"] == 131.4
    assert odds["6-5-4"] == 2141.0


def test_parse_odds_empty_input_is_safe() -> None:
    assert parse_trifecta_odds("") == {}
