"""決まり手率の絞り込み: 上限指定と、1 艇での複数指定。

既存の保存手法は全て単数形式 {name, rate_min} なので、それがそのまま読めることを
まず固定する。そのうえで rate_max と配列を足した分を検証する。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.search.roi_search import search_roi
from tests.test_roi_search import _make_db, _row


BET = {"type": "sanrentan", "first": 1, "second": 2, "third": 3}


def _kimarite_db(tmp_path: Path) -> Path:
    """1 号艇の逃げ率 20 / 50 / 80、差し率はそれぞれ 10 / 5 / 1 のレースを置く。"""
    rows = []
    for index, (nige, sashi) in enumerate([(20.0, 10.0), (50.0, 5.0), (80.0, 1.0)]):
        rows.append(
            _row(
                f"kimarite-{index}",
                f"2024-06-{10 + index:02d}",
                schema_version=4,
                b1_kimarite_rate_nige=nige,
                b1_kimarite_rate_sashi=sashi,
                result_sanrentan_json=json.dumps(["1-2-3"]),
                payout_sanrentan_json=json.dumps({"1-2-3": 1000}),
            )
        )
    # 決まり手率が欠損した 1 本。絞り込むと判定不能で除外される。
    rows.append(
        _row(
            "kimarite-null",
            "2024-06-20",
            schema_version=4,
            b1_kimarite_rate_nige=None,
            b1_kimarite_rate_sashi=None,
            result_sanrentan_json=json.dumps(["1-2-3"]),
            payout_sanrentan_json=json.dumps({"1-2-3": 1000}),
        )
    )
    return _make_db(tmp_path / "kimarite.db", rows)


def _search(db: Path, kimarite) -> dict:
    return search_roi(
        db, {"bet": BET, "boats": {"1": {"kimarite": kimarite}}}, fast=True
    )


def test_legacy_single_object_shape_still_works(tmp_path: Path):
    """保存済み手法の形 {name, rate_min} がそのまま読めること。"""
    result = _search(_kimarite_db(tmp_path), {"name": "nige", "rate_min": 50})
    assert result["n"] == 2  # 50 と 80
    assert result["excluded"]["condition_null"] == 1


def test_rate_max_selects_the_low_end(tmp_path: Path):
    """上限だけの指定で「逃げ率が低い1号艇」を探せること。"""
    result = _search(_kimarite_db(tmp_path), {"name": "nige", "rate_max": 30})
    assert result["n"] == 1  # 20 のみ


def test_rate_min_and_rate_max_form_a_band(tmp_path: Path):
    result = _search(
        _kimarite_db(tmp_path), {"name": "nige", "rate_min": 40, "rate_max": 60}
    )
    assert result["n"] == 1  # 50 のみ


def test_two_kimarite_on_the_same_boat_are_combined_with_and(tmp_path: Path):
    """同じ艇で 2 種類を指定でき、AND で効くこと。"""
    result = _search(
        _kimarite_db(tmp_path),
        [
            {"name": "nige", "rate_min": 40},
            {"name": "sashi", "rate_max": 3},
        ],
    )
    assert result["n"] == 1  # 逃げ80 かつ 差し1


def test_array_of_one_matches_the_single_object(tmp_path: Path):
    db = _kimarite_db(tmp_path)
    single = _search(db, {"name": "nige", "rate_min": 50})
    array = _search(db, [{"name": "nige", "rate_min": 50}])
    for key in ("n", "hits", "roi", "excluded"):
        assert single[key] == array[key], key


def test_all_six_kimarite_can_be_given_at_once(tmp_path: Path):
    result = _search(
        _kimarite_db(tmp_path),
        [{"name": name, "rate_min": 0} for name in
         ("nige", "sashi", "makuri", "makurizashi", "nuki", "megumare")],
    )
    assert result["n"] >= 0  # 例外にならないこと


def test_missing_kimarite_rows_are_excluded_as_undeterminable(tmp_path: Path):
    result = _search(_kimarite_db(tmp_path), {"name": "nige", "rate_min": 0})
    assert result["n"] == 3
    assert result["excluded"]["condition_null"] == 1


@pytest.mark.parametrize(
    ("kimarite", "message"),
    [
        ({"name": "unknown", "rate_min": 50}, "決まり手は1号艇で正しい種類を選んでください"),
        ({"rate_min": 50}, "決まり手は1号艇で正しい種類を選んでください"),
        ({"name": "nige"}, "決まり手は1号艇に「以上」か「以下」の率を入れてください"),
        ({"name": "nige", "rate_min": 60, "rate_max": 40}, "下限が上限を超えています"),
        ([{"name": "nige", "rate_min": 10}, {"name": "nige", "rate_max": 90}], "1回だけ"),
        ([{"name": "nige", "rate_min": 0}] * 7, "最大6件"),
        ([], "決まり手は1号艇に1件以上指定してください"),
        (["nige"], "決まり手は1号艇の指定の形式が不正です"),
    ],
)
def test_invalid_kimarite_is_rejected(tmp_path: Path, kimarite, message):
    """理由が読める日本語で返ること。

    画面側は「決まり手は」で始まる文言だけをそのまま出すので、英語の内部表現が
    混ざると「入力内容に誤りがあります」に潰れて原因が分からなくなる。
    """
    with pytest.raises(ValueError) as excinfo:
        _search(_kimarite_db(tmp_path), kimarite)
    assert message in str(excinfo.value)
    assert str(excinfo.value).startswith("決まり手は")


def test_kimarite_combines_with_season_and_grade(tmp_path: Path):
    """決まり手率が季節・グレードと AND で効くこと (全て 6 月 = 夏)。"""
    db = _kimarite_db(tmp_path)
    summer = search_roi(
        db,
        {"bet": BET, "season": ["夏"],
         "boats": {"1": {"kimarite": [{"name": "nige", "rate_min": 40}]}}},
        fast=True,
    )
    winter = search_roi(
        db,
        {"bet": BET, "season": ["冬"],
         "boats": {"1": {"kimarite": [{"name": "nige", "rate_min": 40}]}}},
        fast=True,
    )
    assert summer["n"] == 2
    assert winter["n"] == 0
