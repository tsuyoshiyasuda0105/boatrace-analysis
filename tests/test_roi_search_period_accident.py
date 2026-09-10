"""審査期の事故率・事故点 (本日判定用) の下限。

審査期スナップショットは 2026-07-26 から作られ始めた。それより前の行は
「スナップショットが無いので 0」で埋まっていて、事故率 0.5% 以下で絞っても
1 件も減らなかった (2026-09-10 実測: 140,246 → 140,246)。この条件を使ったら、
実データのある日から数える。
"""
from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from src.features.asof_builder import create_output_schema
from src.search.roi_search import (
    HISTORY_CUTOFF,
    PERIOD_ACCIDENT_CUTOFF,
    search_roi,
)

BET = {"type": "sanrentan", "first": 1, "second": 2, "third": 3}


def _row(race_id: str, race_date: str, **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "race_id": race_id,
        "race_date": race_date,
        "asof_date": race_date,
        "built_at": "2026-09-10T00:00:00+00:00",
        "schema_version": 11,
        "jcd": 12,
        "race_no": 1,
        "b1_racer_id": 4320,
        "b1_accident_rate": 0.0,
        "b1_accident_points": 0.0,
        "b1_accident_rate_365d": 0.2,
        "result_sanrentan": "1-2-3",
        "payout_sanrentan": 1000,
        "result_sanrentan_json": '["1-2-3"]',
        "payout_sanrentan_json": '{"1-2-3":1000}',
    }
    row.update(overrides)
    return row


def _make_db(path: Path, rows: list[dict[str, object]]) -> Path:
    with sqlite3.connect(path) as conn:
        create_output_schema(conn)
        for row in rows:
            columns = list(row)
            placeholders = ",".join("?" for _ in columns)
            conn.execute(
                "INSERT INTO asof_race_features "
                f"({','.join(columns)}) VALUES ({placeholders})",
                [row[column] for column in columns],
            )
    return path


def _day_before(iso: str) -> str:
    from datetime import date, timedelta

    return (date.fromisoformat(iso) - timedelta(days=1)).isoformat()


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return _make_db(
        tmp_path / "period.db",
        [
            _row("y2024", "2024-06-01"),
            _row("before", _day_before(PERIOD_ACCIDENT_CUTOFF)),
            _row("boundary", PERIOD_ACCIDENT_CUTOFF),
            _row("after", "2026-08-15", b1_accident_rate=0.3, b1_accident_points=10.0),
        ],
    )


def test_the_cutoff_is_where_the_snapshots_begin() -> None:
    assert PERIOD_ACCIDENT_CUTOFF == "2026-07-26"


@pytest.mark.parametrize(
    "condition",
    [
        {"accident_rate": {"max": 0.5}},
        {"accident_points": {"max": 20}},
    ],
)
def test_using_a_period_accident_condition_starts_at_the_snapshots(
    db: Path, condition: dict
) -> None:
    result = search_roi(db, {"bet": BET, "boats": {"1": condition}}, fast=True)
    assert result["n"] == 2
    assert result["effective_date_range"][0] == PERIOD_ACCIDENT_CUTOFF


def test_the_old_symptom_a_filter_that_filtered_nothing_is_gone(db: Path) -> None:
    """2024〜2025 年を「事故率 0.5% 以下」で絞ると、以前は全件が通っていた。

    その期間には本物の値が無い。0 埋めの行を「無事故」として数えず、
    何のデータが何日からあるかを日本語で伝える。
    """
    with pytest.raises(ValueError) as caught:
        search_roi(
            db,
            {
                "bet": BET,
                "date_from": "2024-01-01",
                "date_to": "2025-12-31",
                "boats": {"1": {"accident_rate": {"max": 0.5}}},
            },
            fast=True,
        )
    message = str(caught.value)
    assert PERIOD_ACCIDENT_CUTOFF in message
    assert "事故率（審査期・本日判定用）" in message


def test_the_explanation_reaches_the_screen_as_written(db: Path) -> None:
    """画面は「検索条件は」で始まる文だけをそのまま出す。まとめて
    「入力内容に誤りがあります」にされると、利用者は日付を疑ってしまう。"""
    from src.web.kachisuji_bp import _user_validation_message

    with pytest.raises(ValueError) as caught:
        search_roi(
            db,
            {"bet": BET, "date_to": "2025-12-31",
             "boats": {"1": {"accident_points": {"max": 10}}}},
            fast=True,
        )
    shown = _user_validation_message(caught.value)
    assert shown == str(caught.value)
    assert "入力内容に誤りがあります" not in shown


def test_a_range_the_user_reversed_keeps_the_ordinary_error(db: Path) -> None:
    """下限のせいではなく利用者が逆に入れたときは、これまでどおりの扱い。"""
    with pytest.raises(ValueError, match="date_from must not be after date_to"):
        search_roi(
            db,
            {"bet": BET, "date_from": "2026-09-01", "date_to": "2026-08-01",
             "boats": {"1": {"accident_rate": {"max": 0.5}}}},
            fast=True,
        )


@pytest.mark.parametrize(
    ("condition", "label"),
    [
        ({"boats": {"1": {"accident_rate_365d": {"max": 0.5}}}}, "決まり手・事故率（過去1年）"),
        ({"entry_change_risk": {"max": 10}}, "復元事故率・平均ST・進入変更リスク"),
    ],
)
def test_other_cutoffs_explain_themselves_too(db: Path, condition: dict, label: str) -> None:
    with pytest.raises(ValueError) as caught:
        search_roi(db, {"bet": BET, "date_to": "2010-12-31", **condition}, fast=True)
    assert label in str(caught.value)


def test_the_filter_still_filters_after_the_cutoff(db: Path) -> None:
    result = search_roi(
        db, {"bet": BET, "boats": {"1": {"accident_rate": {"min": 0.25}}}}, fast=True
    )
    assert result["n"] == 1


def test_a_later_start_date_is_kept(db: Path) -> None:
    result = search_roi(
        db,
        {"bet": BET, "date_from": "2026-08-01",
         "boats": {"1": {"accident_rate": {"max": 5}}}},
        fast=True,
    )
    assert result["n"] == 1
    assert result["effective_date_range"][0] == "2026-08-15"


def test_the_365_day_rate_keeps_its_own_earlier_cutoff(db: Path) -> None:
    """過去1年の事故率は 2016 年から本物がある。巻き込んで下限を上げない。"""
    result = search_roi(
        db, {"bet": BET, "boats": {"1": {"accident_rate_365d": {"max": 0.5}}}}, fast=True
    )
    assert result["n"] == 4
    assert result["effective_date_range"][0] >= HISTORY_CUTOFF
    assert result["effective_date_range"][0] < PERIOD_ACCIDENT_CUTOFF


def test_combining_both_uses_the_later_cutoff(db: Path) -> None:
    result = search_roi(
        db,
        {"bet": BET, "boats": {"1": {"accident_rate_365d": {"max": 0.5},
                                     "accident_points": {"max": 20}}}},
        fast=True,
    )
    assert result["effective_date_range"][0] == PERIOD_ACCIDENT_CUTOFF


def test_a_condition_on_another_boat_moves_the_cutoff_too(tmp_path: Path) -> None:
    database = _make_db(
        tmp_path / "boat2.db",
        [
            _row("before", "2025-01-01", b2_accident_rate=0.0),
            _row("after", "2026-08-01", b2_accident_rate=0.0),
        ],
    )
    result = search_roi(
        database, {"bet": BET, "boats": {"2": {"accident_rate": {"max": 0.5}}}}, fast=True
    )
    assert result["n"] == 1


def test_without_the_condition_older_races_stay_in(db: Path) -> None:
    assert search_roi(db, {"bet": BET}, fast=True)["n"] == 4


def test_an_empty_range_does_not_move_the_cutoff(db: Path) -> None:
    """min も max も無ければ条件なし。下限も引き上げない。"""
    result = search_roi(db, {"bet": BET, "boats": {"1": {"accident_rate": {}}}}, fast=True)
    assert result["n"] == 4


@pytest.mark.parametrize(
    "template",
    [
        Path("src/web/templates/kachisuji_search.html"),
        Path("src/kachisuji_web/templates/search.html"),
    ],
    ids=["本番", "双子"],
)
def test_the_screen_shows_the_same_start_date(template: Path) -> None:
    """画面の目印と、検索の下限をそろえる。"""
    source = template.read_text(encoding="utf-8")
    label = PERIOD_ACCIDENT_CUTOFF.replace("-", "/").replace("/0", "/")
    for field in ("事故率（審査期・本日判定用）", "事故点（審査期）"):
        block = source.split(field, 1)[1].split("compareSelect", 1)[0]
        assert f"📅 {label}〜" in block, (field, label)
