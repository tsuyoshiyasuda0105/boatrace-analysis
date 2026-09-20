"""2連複・3連複 (着順を問わない券種) の買い目と回収率の検証。

特徴量側 (asof_builder) は当たり目を艇番の小さい順「1-2-3」で持つ。買い目も
同じ書き方にそろえないと、3-1-2 と指定したときに当たりを取りこぼす。
"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from src.search.roi_search import _parse_bet, search_roi
from tests.test_roi_search import _make_db, _row


def _unordered_row(race_id: str, race_date: str, **overrides: object) -> dict[str, object]:
    row = _row(
        race_id,
        race_date,
        schema_version=12,
        result_sanrenpuku="1-2-3",
        payout_sanrenpuku=310,
        result_sanrenpuku_json='["1-2-3"]',
        payout_sanrenpuku_json='{"1-2-3":310}',
        result_nirenpuku="1-2",
        payout_nirenpuku=220,
        result_nirenpuku_json='["1-2"]',
        payout_nirenpuku_json='{"1-2":220}',
    )
    row.update(overrides)
    return row


@pytest.mark.parametrize(
    ("bet", "expected"),
    [
        ({"type": "sanrenpuku", "first": 3, "second": 1, "third": 2}, ("1-2-3",)),
        ({"type": "sanrenpuku", "first": 1, "second": 2, "third": 3}, ("1-2-3",)),
        ({"type": "nirenpuku", "first": 2, "second": 1}, ("1-2",)),
        (
            {"type": "sanrenpuku", "tickets": [
                {"first": 5, "second": 1, "third": 3}, {"first": 2, "second": 4, "third": 6}]},
            ("1-3-5", "2-4-6"),
        ),
    ],
)
def test_unordered_tickets_are_written_in_ascending_boat_order(bet, expected) -> None:
    assert _parse_bet(bet).expected == expected


def test_the_same_boats_in_another_order_are_one_duplicated_ticket() -> None:
    bet = {"type": "sanrenpuku", "tickets": [
        {"first": 1, "second": 2, "third": 3}, {"first": 3, "second": 2, "third": 1}]}
    with pytest.raises(ValueError, match="^買い目は重複しています: 3連複 1-2-3"):
        _parse_bet(bet)


def test_unordered_ticket_needs_distinct_boats() -> None:
    with pytest.raises(ValueError, match="^買い目は1〜6号艇から、異なる艇番を選んでください"):
        _parse_bet({"type": "nirenpuku", "first": 2, "second": 2})


def test_unordered_ticket_rejects_a_third_leg_for_nirenpuku() -> None:
    with pytest.raises(ValueError, match="unused bet key"):
        _parse_bet({"type": "nirenpuku", "first": 1, "second": 2, "third": 3})


def test_sanrenpuku_roi_hits_regardless_of_the_order_entered(tmp_path: Path) -> None:
    db = _make_db(
        tmp_path / "trio.db",
        [
            _unordered_row("hit", "2026-01-01"),
            _unordered_row(
                "miss",
                "2026-01-02",
                result_sanrenpuku="1-4-5",
                payout_sanrenpuku=2200,
                result_sanrenpuku_json='["1-4-5"]',
                payout_sanrenpuku_json='{"1-4-5":2200}',
            ),
        ],
    )

    forward = search_roi(db, {"bet": {"type": "sanrenpuku", "first": 1, "second": 2, "third": 3}}, fast=True)
    reversed_order = search_roi(db, {"bet": {"type": "sanrenpuku", "first": 3, "second": 2, "third": 1}}, fast=True)

    assert forward["n"] == 2
    assert forward["hits"] == 1
    assert forward["roi"] == pytest.approx(155.0)  # (310 + 0) / 2
    assert reversed_order["roi"] == forward["roi"]
    assert reversed_order["hits"] == forward["hits"]


def test_nirenpuku_dead_heat_pays_every_matching_ticket(tmp_path: Path) -> None:
    db = _make_db(
        tmp_path / "quinella-heat.db",
        [
            _unordered_row(
                "heat",
                "2026-01-01",
                result_nirenpuku_json='["1-2","1-3"]',
                payout_nirenpuku_json='{"1-2":220,"1-3":480}',
            )
        ],
    )
    bet = {"type": "nirenpuku", "tickets": [{"first": 2, "second": 1}, {"first": 3, "second": 1}]}

    result = search_roi(db, {"bet": bet}, fast=True)

    assert result["n"] == 1
    assert result["hits"] == 1
    assert result["roi"] == pytest.approx((220 + 480) / 2)


def test_rows_written_before_v12_are_result_missing_not_losses(tmp_path: Path) -> None:
    """版11の行は3連複・2連複の列が空 (未計算)。外れとして数えると回収率が下がる。"""
    old = _row("v11", "2026-01-01", schema_version=11)
    db = _make_db(tmp_path / "mixed.db", [old, _unordered_row("v12", "2026-01-02")])

    result = search_roi(db, {"bet": {"type": "sanrenpuku", "first": 1, "second": 2, "third": 3}}, fast=True)

    assert result["n"] == 1
    assert result["excluded"]["result_missing"] == 1
    assert result["roi"] == pytest.approx(310.0)


def test_existing_bet_types_ignore_the_new_columns(tmp_path: Path) -> None:
    """版12の行でも、単勝・2連単・3連単の計算は新しい列に左右されない。"""
    row = _unordered_row(
        "v12",
        "2026-01-01",
        result_sanrentan_json='["1-2-3"]',
        payout_sanrentan_json='{"1-2-3":1230}',
    )
    db = _make_db(tmp_path / "v12.db", [row])

    result = search_roi(db, {"bet": {"type": "sanrentan", "first": 1, "second": 2, "third": 3}}, fast=True)

    assert result["n"] == 1
    assert result["roi"] == pytest.approx(1230.0)


def test_database_without_the_new_columns_gets_a_friendly_message(tmp_path: Path) -> None:
    """本番へ列を足す前にこの券種を選んだ場合。SQL エラーで落とさず案内する。"""
    path = tmp_path / "old-schema.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE asof_race_features (race_id TEXT, race_date TEXT, schema_version INTEGER, "
            "result_sanrentan TEXT, payout_sanrentan INTEGER)"
        )
        connection.execute(
            "INSERT INTO asof_race_features VALUES ('x', '2026-01-01', 11, '1-2-3', 1230)"
        )

    with pytest.raises(ValueError, match="^買い目は2連複・3連複のデータを準備中です"):
        search_roi(path, {"bet": {"type": "sanrenpuku", "first": 1, "second": 2, "third": 3}}, fast=True)


# ---- 買い目ごとに券種を選ぶ (2026-09-20 リッキーさん要望) -------------------


def test_each_ticket_can_carry_its_own_bet_type() -> None:
    bet = {"type": "sanrentan", "tickets": [
        {"first": 1, "second": 2, "third": 3},
        {"type": "sanrenpuku", "first": 3, "second": 2, "third": 1},
        {"type": "tansho", "first": 1},
    ]}

    parsed = _parse_bet(bet)

    assert [(t.kind, t.key) for t in parsed.tickets] == [
        ("sanrentan", "1-2-3"), ("sanrenpuku", "1-2-3"), ("tansho", 1)]
    assert parsed.kinds == ("sanrentan", "sanrenpuku", "tansho")
    assert parsed.mixed is True


def test_the_same_numbers_in_two_bet_types_are_two_different_tickets(tmp_path: Path) -> None:
    """3連単 1-2-3 と 3連複 1-2-3 は別の点。重複にはしない。"""
    db = _make_db(tmp_path / "mixed.db", [
        _unordered_row("hit-both", "2026-01-01",
                       result_sanrentan_json='["1-2-3"]', payout_sanrentan_json='{"1-2-3":1230}'),
        _unordered_row("trio-only", "2026-01-02",
                       result_sanrentan="2-1-3", payout_sanrentan=980,
                       result_sanrentan_json='["2-1-3"]', payout_sanrentan_json='{"2-1-3":980}'),
    ])
    bet = {"type": "sanrentan", "tickets": [
        {"first": 1, "second": 2, "third": 3},
        {"type": "sanrenpuku", "first": 1, "second": 2, "third": 3},
    ]}

    result = search_roi(db, {"bet": bet}, fast=True)

    assert result["n"] == 2
    assert result["ticket_count"] == 2
    assert result["bet_types"] == ["sanrentan", "sanrenpuku"]
    # 1 レース目は両方当たり (1230 + 310)、2 レース目は 3連複だけ当たり (310)
    assert result["hits"] == 2
    assert result["roi"] == pytest.approx((1230 + 310 + 310) / (2 * 2))
    assert [(item["bet_label"], item["hits"], item["roi"]) for item in result["ticket_breakdown"]] == [
        ("3連単 1-2-3", 1, pytest.approx(615.0)),
        ("3連複 1-2-3", 2, pytest.approx(310.0)),
    ]


def test_a_race_missing_one_of_the_chosen_bet_types_is_excluded(tmp_path: Path) -> None:
    """片方の券種だけで数えると、点数で割る回収率が実際より良く出てしまう。"""
    db = _make_db(tmp_path / "half.db", [
        _unordered_row("both", "2026-01-01",
                       result_sanrentan_json='["1-2-3"]', payout_sanrentan_json='{"1-2-3":1230}'),
        _unordered_row("no-trio", "2026-01-02",
                       result_sanrenpuku=None, payout_sanrenpuku=None,
                       result_sanrenpuku_json=None, payout_sanrenpuku_json=None),
    ])
    bet = {"type": "sanrentan", "tickets": [
        {"first": 1, "second": 2, "third": 3},
        {"type": "sanrenpuku", "first": 1, "second": 2, "third": 3},
    ]}

    result = search_roi(db, {"bet": bet}, fast=True)

    assert result["n"] == 1
    assert result["excluded"]["result_missing"] == 1


def test_an_unknown_bet_type_on_one_ticket_is_rejected() -> None:
    with pytest.raises(ValueError, match=r"bet\.tickets\[1\]\.type must be"):
        _parse_bet({"type": "sanrentan", "tickets": [
            {"first": 1, "second": 2, "third": 3},
            {"type": "nirensou", "first": 1, "second": 2},
        ]})


def test_mixed_tickets_keep_their_own_leg_count() -> None:
    """2連複の点に3着を書いたら、その点だけのエラーとして返す。"""
    with pytest.raises(ValueError, match="unused bet key"):
        _parse_bet({"type": "sanrentan", "tickets": [
            {"first": 1, "second": 2, "third": 3},
            {"type": "nirenpuku", "first": 1, "second": 2, "third": 3},
        ]})
