"""季節 (春夏秋冬) の絞り込みと内訳の検証。

季節は専用カラムを持たず race_date から導出する。導出規則が SQL 側 (絞り込み) と
Python 側 (内訳の集計) の 2 箇所にあるため、両者がずれると「絞り込んだ結果」と
「内訳に出ている数字」が食い違う。ここではその一致を固定する。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.search.roi_search import SEASON_MONTHS, _season_of, search_roi
from tests.test_roi_search import _make_db, _row


BET = {"type": "sanrentan", "first": 1, "second": 2, "third": 3}


def _season_db(tmp_path: Path) -> Path:
    """各季節に的中 1 本ずつ、払戻を変えて置く。"""
    rows = []
    for index, (race_date, payout) in enumerate(
        [
            ("2024-04-10", 1000),  # 春
            ("2024-07-10", 2000),  # 夏
            ("2024-10-10", 3000),  # 秋
            ("2024-01-10", 4000),  # 冬
        ]
    ):
        rows.append(
            _row(
                f"season-{index}",
                race_date,
                schema_version=4,
                result_sanrentan_json=json.dumps(["1-2-3"]),
                payout_sanrentan_json=json.dumps({"1-2-3": payout}),
            )
        )
    return _make_db(tmp_path / "season.db", rows)


@pytest.mark.parametrize(
    ("race_date", "expected"),
    [
        ("2024-03-01", "春"), ("2024-05-31", "春"),
        ("2024-06-01", "夏"), ("2024-08-31", "夏"),
        ("2024-09-01", "秋"), ("2024-11-30", "秋"),
        ("2024-12-01", "冬"), ("2024-01-31", "冬"), ("2024-02-29", "冬"),
    ],
)
def test_season_boundaries_follow_the_meteorological_split(race_date, expected):
    assert _season_of(race_date) == expected


def test_every_month_belongs_to_exactly_one_season():
    months = [m for months in SEASON_MONTHS.values() for m in months]
    assert sorted(months) == list(range(1, 13)), "月の重複か抜けがある"


def test_seasonal_breakdown_is_always_returned(tmp_path: Path):
    result = search_roi(_season_db(tmp_path), {"bet": BET}, fast=True)

    assert [item["season"] for item in result["seasonal"]] == ["春", "夏", "秋", "冬"]
    rois = {item["season"]: item["roi"] for item in result["seasonal"]}
    assert rois == {"春": 1000.0, "夏": 2000.0, "秋": 3000.0, "冬": 4000.0}


def test_filtering_by_season_matches_its_own_breakdown_row(tmp_path: Path):
    """絞り込んだ結果と、全体の内訳に出ている行が一致すること。

    SQL 側と Python 側で季節の分け方がずれると、ここが割れる。
    """
    db = _season_db(tmp_path)
    whole = search_roi(db, {"bet": BET}, fast=True)
    for row in whole["seasonal"]:
        narrowed = search_roi(db, {"bet": BET, "season": [row["season"]]}, fast=True)
        assert (narrowed["n"], narrowed["hits"], narrowed["roi"]) == (
            row["n"], row["hits"], row["roi"]
        ), row["season"]


def test_seasonal_rows_add_back_up_to_the_whole(tmp_path: Path):
    whole = search_roi(_season_db(tmp_path), {"bet": BET}, fast=True)
    assert sum(item["n"] for item in whole["seasonal"]) == whole["n"]
    assert sum(item["hits"] for item in whole["seasonal"]) == whole["hits"]


def test_selecting_two_seasons_keeps_only_those(tmp_path: Path):
    result = search_roi(
        _season_db(tmp_path), {"bet": BET, "season": ["夏", "冬"]}, fast=True
    )
    assert result["n"] == 2
    assert [item["season"] for item in result["seasonal"]] == ["夏", "冬"]
    # (2000 + 4000) / 2 レース
    assert result["roi"] == pytest.approx(3000.0)


def test_selecting_all_four_seasons_equals_no_filter(tmp_path: Path):
    """4 つ全部は絞り込みにならない。除外件数まで無指定と同じであること。"""
    db = _season_db(tmp_path)
    whole = search_roi(db, {"bet": BET}, fast=True)
    every = search_roi(
        db, {"bet": BET, "season": ["春", "夏", "秋", "冬"]}, fast=True
    )
    for key in ("n", "hits", "roi", "excluded", "yearly"):
        assert every[key] == whole[key], key


def test_season_filter_does_not_inflate_the_excluded_count(tmp_path: Path):
    """race_date は NULL になり得ないので、判定不能として除外されないこと."""
    result = search_roi(_season_db(tmp_path), {"bet": BET, "season": ["夏"]}, fast=True)
    assert result["excluded"]["condition_null"] == 0


def test_small_season_samples_are_flagged(tmp_path: Path):
    """季節で 4 分割すると母数が減る。少ない行に警告が付くこと。"""
    result = search_roi(_season_db(tmp_path), {"bet": BET}, fast=True)
    assert all(item["warning"] == "n<30" for item in result["seasonal"])


@pytest.mark.parametrize(
    ("season", "message"),
    [
        (["春夏"], "春・夏・秋・冬"),
        (["Spring"], "春・夏・秋・冬"),
        (["夏", "夏"], "重複"),
        ([], "must be a non-empty array"),
    ],
)
def test_invalid_season_values_are_rejected(tmp_path: Path, season, message):
    with pytest.raises(ValueError) as excinfo:
        search_roi(_season_db(tmp_path), {"bet": BET, "season": season}, fast=True)
    assert message in str(excinfo.value)
