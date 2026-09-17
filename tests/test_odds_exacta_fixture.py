"""Fixture: boatrace.jp exacta/quinella odds page, 2026-09-17 Heiwajima race 1.

Copied from a live fetch of odds2tf?rno=1&jcd=04&hd=20260917 (確定オッズ)。
二連複の表も同じページに載るので、二連単の 30 通りだけを拾えることを確かめる。
"""

from pathlib import Path

from src.parsers.odds import parse_exacta_odds


FIXTURE = (
    Path(__file__).parent / "fixtures" / "parsers" / "odds" / "odds2tf_20260917_04_01.html"
)


def test_parse_exacta_real_fixture_golden_values() -> None:
    odds = parse_exacta_odds(FIXTURE.read_text(encoding="utf-8"))

    assert len(odds) == 30
    assert set(odds) == {f"{a}-{b}" for a in range(1, 7) for b in range(1, 7) if a != b}
    # ヘッダ直下の行: 1着=1 → 2着=2 が 6.4、1着=2 → 2着=1 が 6.9 ... (ページの値そのまま)
    assert odds["1-2"] == 6.4
    assert odds["2-1"] == 6.9
    assert odds["4-1"] == 109.4
    assert odds["6-1"] == 314.5
    assert odds["1-3"] == 10.7
    assert odds["6-3"] == 838.7


def test_parse_exacta_does_not_pick_quinella_table() -> None:
    # 二連複の表は「1=2 3.1」のように 15 通りしか無い。二連単 (1-2 = 6.4) が優先される。
    odds = parse_exacta_odds(FIXTURE.read_text(encoding="utf-8"))
    assert odds["1-2"] != 3.1


def test_parse_exacta_empty_input_is_safe() -> None:
    assert parse_exacta_odds("") == {}
