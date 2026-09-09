"""進入変更 (前づけ) 率での絞り込み。

レース単位の「進入変更リスク」は 2〜6 号艇のうち前づけ率が最も高い選手の率。
1 号艇はインを取られる側なので、自分の率でレースを荒いと判定しない。

判定できない (前づけ率が全員 NULL) レースは、黙って落とさず
``excluded["condition_null"]`` に出す。母数が減った理由が画面から分かるように。
"""
from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from src.features.asof_builder import create_output_schema
from src.search.roi_search import search_roi

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
        # schema v4 以降は _json 側が正。両方入れておく。
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


def _boats(default: float | None = 1.0, **rates: float | None) -> dict[str, object]:
    """b2〜b6 の前づけ率。書かなかった艇は ``default``。

    実際のレースはほとんどの艇に値が入るので、既定を「大人しい 1%」にして
    おく。判定不能を試したいときだけ ``default=None`` を渡す。
    """
    return {
        f"b{boat}_entry_change_rate": rates.get(f"b{boat}", default)
        for boat in range(2, 7)
    }


def _one(tmp_path: Path, name: str, condition: dict, date: str = "2024-01-10", **cells):
    """1 レースだけの DB を作って条件に掛け、結果をそのまま返す。

    どのレースが残ったかを ``n`` で読みたいので、1 行ずつ確かめる。
    """
    database = _make_db(tmp_path / f"{name}.db", [_row(name, date, **cells)])
    return search_roi(database, {"bet": BET, **condition}, fast=True)


def _kept(tmp_path: Path, name: str, condition: dict, **cells) -> bool:
    return _one(tmp_path, name, condition, **cells)["n"] == 1


# --- レース単位のリスク --------------------------------------------------


@pytest.mark.parametrize(
    ("name", "rates", "limit", "expected"),
    [
        ("calm", dict(b2=1.0, b3=2.0, b4=3.0, b5=4.0, b6=5.0), 10, True),
        ("one_rough_boat", dict(b2=1.0, b3=2.0, b4=3.0, b5=4.0, b6=18.0), 10, False),
        ("one_rough_boat_ok", dict(b2=1.0, b3=2.0, b4=3.0, b5=4.0, b6=18.0), 20, True),
        ("very_rough", dict(b2=45.0, b3=2.0, b4=3.0, b5=4.0, b6=5.0), 20, False),
    ],
)
def test_the_roughest_outer_boat_decides_the_race(
    tmp_path: Path, name: str, rates: dict, limit: int, expected: bool
) -> None:
    """1 艇でも荒い選手がいれば、そのレースは荒いほうに数える。"""
    assert _kept(tmp_path, name, {"entry_change_risk": {"max": limit}}, **_boats(**rates)) is expected


def test_min_selects_the_rough_races(tmp_path: Path) -> None:
    assert _kept(tmp_path, "rough", {"entry_change_risk": {"min": 40}}, **_boats(b2=45.0))
    assert not _kept(tmp_path, "calm", {"entry_change_risk": {"min": 40}}, **_boats(b2=5.0))


def test_band_uses_both_ends(tmp_path: Path) -> None:
    band = {"entry_change_risk": {"min": 10, "max": 20}}
    assert _kept(tmp_path, "inside", band, **_boats(b6=18.0))
    assert not _kept(tmp_path, "below", band, **_boats(b6=5.0))
    assert not _kept(tmp_path, "above", band, **_boats(b6=45.0))


def test_boundary_is_inclusive_on_both_ends(tmp_path: Path) -> None:
    assert _kept(tmp_path, "at_max", {"entry_change_risk": {"max": 10}}, **_boats(b4=10.0))
    assert _kept(tmp_path, "at_min", {"entry_change_risk": {"min": 10}}, **_boats(b4=10.0))


def test_a_zero_rate_is_kept_not_treated_as_missing(tmp_path: Path) -> None:
    """0% は「一度も動いていない」という立派な値。NULL と混同しない。"""
    assert _kept(tmp_path, "never_a", {"entry_change_risk": {"max": 5}}, **_boats(b2=0.0))
    result = _one(tmp_path, "never_b", {"entry_change_risk": {"min": 5}}, **_boats(b2=0.0))
    assert result["n"] == 0
    assert result["excluded"]["condition_null"] == 0


# --- 判定できないレースの扱い --------------------------------------------


def test_a_race_with_no_known_rate_is_reported_not_silently_dropped(
    tmp_path: Path,
) -> None:
    result = _one(
        tmp_path, "unknown", {"entry_change_risk": {"max": 10}}, **_boats(default=None)
    )
    assert result["n"] == 0
    assert result["excluded"]["condition_null"] == 1


def test_older_schema_rows_are_reported_as_unjudged(tmp_path: Path) -> None:
    """v10 以前の行は列が空。荒いレースとして数えず、判定できずに寄せる。"""
    result = _one(
        tmp_path,
        "old_schema",
        {"entry_change_risk": {"max": 10}},
        schema_version=10,
        **_boats(default=None),
    )
    assert result["n"] == 0
    assert result["excluded"]["condition_null"] == 1


def test_one_unknown_boat_makes_the_whole_race_unjudged(tmp_path: Path) -> None:
    """5 人そろって初めてレースの荒さを言える。

    1 人でも判定できない選手がいれば「静かなレース」と断定しない。分かって
    いる選手だけで判定する案は、当日照合が列名しか扱えないためバックテスト
    と答えがずれる。ここは数を稼がず、二つの画面をそろえる。
    """
    limit = {"entry_change_risk": {"max": 10}}
    result = _one(tmp_path, "partial", limit, **_boats(b3=2.0, b5=None))
    assert result["n"] == 0
    assert result["excluded"]["condition_null"] == 1
    assert _kept(tmp_path, "all_known", limit, **_boats(b3=2.0, b5=4.0))


def test_the_inner_boat_is_not_counted_as_a_risk(tmp_path: Path) -> None:
    """1 号艇は前づけされる側。自分の率でレースを荒いと判定しない。"""
    assert _kept(
        tmp_path,
        "inner_moves",
        {"entry_change_risk": {"max": 10}},
        b1_entry_change_rate=90.0,
        **_boats(b2=1.0, b3=1.0, b4=1.0, b5=1.0, b6=1.0),
    )


# --- 艇ごとの率 -----------------------------------------------------------


def test_per_boat_rate_looks_at_that_boat_only(tmp_path: Path) -> None:
    only_b2 = {"boats": {"2": {"entry_change_rate": {"max": 10}}}}
    assert _kept(tmp_path, "b2_calm", only_b2, **_boats(b2=3.0, b3=60.0))
    assert not _kept(tmp_path, "b2_rough", only_b2, **_boats(b2=60.0, b3=3.0))


def test_per_boat_min_selects_the_specialist(tmp_path: Path) -> None:
    only_b3 = {"boats": {"3": {"entry_change_rate": {"min": 40}}}}
    assert _kept(tmp_path, "specialist", only_b3, **_boats(b3=60.0))
    assert not _kept(tmp_path, "calm", only_b3, **_boats(b3=3.0))


def test_per_boat_and_race_level_combine_with_and(tmp_path: Path) -> None:
    both = {
        "entry_change_risk": {"max": 20},
        "boats": {"2": {"entry_change_rate": {"max": 5}}},
    }
    assert _kept(tmp_path, "both_ok", both, **_boats(b2=3.0, b6=18.0))
    assert not _kept(tmp_path, "boat_fails", both, **_boats(b2=9.0, b6=18.0))
    assert not _kept(tmp_path, "race_fails", both, **_boats(b2=3.0, b6=45.0))


# --- データのある期間 -----------------------------------------------------


@pytest.mark.parametrize(
    "condition",
    [
        {"entry_change_risk": {"max": 10}},
        {"boats": {"2": {"entry_change_rate": {"max": 10}}}},
    ],
)
def test_using_the_rate_pulls_the_start_date_up_to_2016_06(
    tmp_path: Path, condition: dict[str, object]
) -> None:
    """進入データは 2016/6 から。それ以前を混ぜて母数を水増ししない。"""
    database = _make_db(
        tmp_path / "cutoff.db",
        [
            _row("before", "2016-05-31", **_boats(b2=1.0)),
            _row("boundary", "2016-06-01", **_boats(b2=1.0)),
        ],
    )
    result = search_roi(database, {"bet": BET, **condition}, fast=True)
    assert result["n"] == 1
    assert result["effective_date_range"][0] == "2016-06-01"


def test_without_the_condition_older_races_stay_searchable(tmp_path: Path) -> None:
    database = _make_db(
        tmp_path / "no-cutoff.db",
        [_row("before", "2016-05-31", **_boats(b2=1.0))],
    )
    assert search_roi(database, {"bet": BET}, fast=True)["n"] == 1


# --- 入力の検査 -----------------------------------------------------------


def test_reversed_band_is_rejected(tmp_path: Path) -> None:
    database = _make_db(tmp_path / "bad.db", [_row("a", "2024-01-10", **_boats(b2=1.0))])
    with pytest.raises(ValueError, match="min must not exceed max"):
        search_roi(database, {"bet": BET, "entry_change_risk": {"min": 30, "max": 10}})


@pytest.mark.parametrize(
    "bad",
    [
        {"entry_change_risk": {"maximum": 10}},
        {"entry_change_risk": "10"},
        {"entry_change_risk": {"max": "とても低い"}},
        {"entry_change_risk": {"max": None, "min": None, "mid": 5}},
        {"boats": {"2": {"entry_change": {"max": 10}}}},
        {"entry_change": {"max": 10}},
    ],
)
def test_malformed_conditions_are_rejected(tmp_path: Path, bad: dict) -> None:
    database = _make_db(tmp_path / "bad.db", [_row("a", "2024-01-10", **_boats(b2=1.0))])
    with pytest.raises(ValueError):
        search_roi(database, {"bet": BET, **bad})


def test_an_empty_range_does_not_filter_anything(tmp_path: Path) -> None:
    """min も max も無ければ条件なし。日付の下限も引き上げない。"""
    database = _make_db(
        tmp_path / "empty.db", [_row("before", "2016-05-31", **_boats(b2=1.0))]
    )
    result = search_roi(database, {"bet": BET, "entry_change_risk": {}}, fast=True)
    assert result["n"] == 1


# --- 本日の合致レース (当日照合) ------------------------------------------
#
# バックテストだけ見ていて 2026-09-10 に取りこぼした。当日照合は条件が
# 触った「列名」を受け取って NULL を調べる作りで、SQL 式を渡すと落ちる。


def _forward_db(tmp_path: Path, rows: list[dict[str, object]]) -> Path:
    return _make_db(tmp_path / "forward.db", rows)


def test_the_race_level_filter_works_on_todays_races(tmp_path: Path) -> None:
    from src.search.strategies import match_races

    database = _forward_db(
        tmp_path,
        [
            _row("calm", "2026-09-10", jcd=1, race_no=1, **_boats(b2=2.0)),
            _row("rough", "2026-09-10", jcd=1, race_no=2, **_boats(b2=55.0)),
        ],
    )
    result = match_races(
        {"bet": BET, "entry_change_risk": {"max": 10}},
        "2026-09-10",
        database,
        tmp_path / "strategies.db",
    )
    assert [item["race_id"] for item in result["matched"]] == ["calm"]


def test_an_unjudged_race_does_not_show_up_as_a_match(tmp_path: Path) -> None:
    """前日までに決まる条件が欠測なら、合致とは呼ばない。"""
    from src.search.strategies import match_races

    database = _forward_db(
        tmp_path,
        [_row("unknown", "2026-09-10", jcd=1, race_no=1, **_boats(default=None))],
    )
    result = match_races(
        {"bet": BET, "entry_change_risk": {"max": 10}},
        "2026-09-10",
        database,
        tmp_path / "strategies.db",
    )
    assert result["matched"] == []
    assert result["pending"] == []


def test_the_per_boat_rate_works_on_todays_races(tmp_path: Path) -> None:
    from src.search.strategies import match_races

    database = _forward_db(
        tmp_path,
        [
            _row("calm", "2026-09-10", jcd=1, race_no=1, **_boats(b2=2.0)),
            _row("rough", "2026-09-10", jcd=1, race_no=2, **_boats(b2=55.0)),
        ],
    )
    result = match_races(
        {"bet": BET, "boats": {"2": {"entry_change_rate": {"max": 10}}}},
        "2026-09-10",
        database,
        tmp_path / "strategies.db",
    )
    assert [item["race_id"] for item in result["matched"]] == ["calm"]


def test_backtest_and_today_agree_on_the_same_race(tmp_path: Path) -> None:
    """同じ条件・同じレースで、二つの画面が違う答えを出さないこと。"""
    from src.search.strategies import match_races

    condition = {"entry_change_risk": {"max": 10}}
    cases = {
        "all_calm": _boats(b2=2.0),
        "one_rough": _boats(b2=55.0),
        "one_unknown": _boats(b5=None),
        "none_known": _boats(default=None),
    }
    for name, cells in cases.items():
        database = _make_db(
            tmp_path / f"agree_{name}.db",
            [_row(name, "2026-09-10", jcd=1, race_no=1, **cells)],
        )
        backtest = search_roi(
            database,
            {"bet": BET, "date_from": "2026-09-10", "date_to": "2026-09-10", **condition},
            fast=True,
        )
        forward = match_races(
            {"bet": BET, **condition}, "2026-09-10", database, tmp_path / "s.db"
        )
        assert (backtest["n"] == 1) is (len(forward["matched"]) == 1), name
