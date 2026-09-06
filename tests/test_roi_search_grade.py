"""グレード (SG/G1/G2/G3/一般) の絞り込みと内訳の検証。

季節と違い grade は約 1 割が NULL。絞り込んだときは「判定不能」として除外件数に
出し、内訳では「不明」行として合計が N に一致することを固定する。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.search.roi_search import GRADE_LABELS, search_roi
from tests.test_roi_search import _make_db, _row


BET = {"type": "sanrentan", "first": 1, "second": 2, "third": 3}


def _grade_db(tmp_path: Path) -> Path:
    """SG / G1 / G3 / 一般 / 欠損 を 1 レースずつ、払戻を変えて置く (G2 は無し)。"""
    rows = []
    for index, (grade, payout) in enumerate(
        [(1, 1000), (2, 2000), (4, 3000), (5, 4000), (None, 5000)]
    ):
        rows.append(
            _row(
                f"grade-{index}",
                f"2024-06-{10 + index:02d}",
                schema_version=4,
                grade=grade,
                result_sanrentan_json=json.dumps(["1-2-3"]),
                payout_sanrentan_json=json.dumps({"1-2-3": payout}),
            )
        )
    return _make_db(tmp_path / "grade.db", rows)


def test_grade_number_mapping_matches_subgroup_analysis():
    assert GRADE_LABELS == {1: "SG", 2: "G1", 3: "G2", 4: "G3", 5: "一般"}


def test_breakdown_lists_present_grades_in_order_with_unknown_last(tmp_path: Path):
    result = search_roi(_grade_db(tmp_path), {"bet": BET}, fast=True)

    labels = [item["label"] for item in result["grade_breakdown"]]
    assert labels == ["SG", "G1", "G3", "一般", "不明"], "G2 は無いので出ない"
    rois = {item["label"]: item["roi"] for item in result["grade_breakdown"]}
    assert rois == {"SG": 1000.0, "G1": 2000.0, "G3": 3000.0, "一般": 4000.0, "不明": 5000.0}
    unknown = next(item for item in result["grade_breakdown"] if item["label"] == "不明")
    assert unknown["grade"] is None


def test_breakdown_rows_add_back_up_to_the_whole_including_unknown(tmp_path: Path):
    whole = search_roi(_grade_db(tmp_path), {"bet": BET}, fast=True)
    assert whole["n"] == 5
    assert sum(item["n"] for item in whole["grade_breakdown"]) == whole["n"]
    assert sum(item["hits"] for item in whole["grade_breakdown"]) == whole["hits"]


def test_filtering_by_grade_excludes_unknown_as_condition_null(tmp_path: Path):
    """絞り込むと欠損レースは「判定不能」で除外され、除外件数に出ること。"""
    result = search_roi(_grade_db(tmp_path), {"bet": BET, "grade": [1]}, fast=True)

    assert result["n"] == 1
    assert result["roi"] == pytest.approx(1000.0)
    assert result["excluded"]["condition_null"] == 1, "欠損 1 件が判定不能として除外される"
    assert [item["label"] for item in result["grade_breakdown"]] == ["SG"]


def test_filtering_by_grade_matches_its_own_breakdown_row(tmp_path: Path):
    db = _grade_db(tmp_path)
    whole = search_roi(db, {"bet": BET}, fast=True)
    for row in whole["grade_breakdown"]:
        if row["grade"] is None:
            continue
        narrowed = search_roi(db, {"bet": BET, "grade": [row["grade"]]}, fast=True)
        assert (narrowed["n"], narrowed["hits"], narrowed["roi"]) == (
            row["n"], row["hits"], row["roi"]
        ), row["label"]


def test_selecting_two_grades_keeps_only_those(tmp_path: Path):
    result = search_roi(_grade_db(tmp_path), {"bet": BET, "grade": [1, 5]}, fast=True)
    assert result["n"] == 2
    assert result["roi"] == pytest.approx(2500.0)
    assert [item["label"] for item in result["grade_breakdown"]] == ["SG", "一般"]
    assert result["excluded"]["condition_null"] == 1


def test_selecting_all_five_grades_equals_no_filter(tmp_path: Path):
    """5 つ全部は絞り込みにならない。欠損レースも残り、除外件数も無指定と同じ。"""
    db = _grade_db(tmp_path)
    whole = search_roi(db, {"bet": BET}, fast=True)
    every = search_roi(db, {"bet": BET, "grade": [1, 2, 3, 4, 5]}, fast=True)
    for key in ("n", "hits", "roi", "excluded", "grade_breakdown"):
        assert every[key] == whole[key], key


def test_string_numbers_are_accepted(tmp_path: Path):
    """フォームから来る "1" のような文字列でも整数として扱うこと。"""
    result = search_roi(_grade_db(tmp_path), {"bet": BET, "grade": ["1", "2"]}, fast=True)
    assert result["n"] == 2


def test_small_grade_samples_are_flagged(tmp_path: Path):
    result = search_roi(_grade_db(tmp_path), {"bet": BET}, fast=True)
    assert all(item["warning"] == "n<30" for item in result["grade_breakdown"])


@pytest.mark.parametrize(
    ("grade", "message"),
    [
        ([0], "SG・G1・G2・G3・一般"),
        ([6], "SG・G1・G2・G3・一般"),
        (["SG"], "SG・G1・G2・G3・一般"),
        ([1, 1], "重複"),
        ([], "must be a non-empty array"),
    ],
)
def test_invalid_grade_values_are_rejected(tmp_path: Path, grade, message):
    with pytest.raises(ValueError) as excinfo:
        search_roi(_grade_db(tmp_path), {"bet": BET, "grade": grade}, fast=True)
    assert message in str(excinfo.value)


def test_grade_and_season_filters_combine(tmp_path: Path):
    """グレードと季節は AND で効くこと (全て 6 月 = 夏なので、冬を足すと 0 件)。"""
    db = _grade_db(tmp_path)
    summer = search_roi(db, {"bet": BET, "grade": [1], "season": ["夏"]}, fast=True)
    winter = search_roi(db, {"bet": BET, "grade": [1], "season": ["冬"]}, fast=True)
    assert summer["n"] == 1
    assert winter["n"] == 0
