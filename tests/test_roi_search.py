from __future__ import annotations

from datetime import date, timedelta
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from src.features.asof_builder import create_output_schema
from src.search.roi_search import search_roi


def _row(race_id: str, race_date: str, **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "race_id": race_id,
        "race_date": race_date,
        "asof_date": race_date,
        "built_at": "2026-08-15T00:00:00+00:00",
        "schema_version": 2,
        "jcd": 12,
        "race_no": 1,
        "weather": "晴",
        "wind_speed": 0.8,
        "tide_phase": "満潮前後",
        "female_present": 0,
        "class_mix": "A1単騎",
        "day_index": "初日",
        "daypart": "ナイター",
        "b1_class": "A1",
        "b1_racer_id": 4320,
        "b1_age": 30,
        "b1_avg_st": 0.12,
        "b1_national_rate": 6.5,
        "b1_local_rate": 6.1,
        "b1_national_rate2": 45.0,
        "b1_local_rate2": 41.0,
        "b1_motor_rate2": 42.0,
        "b1_ex_time": 6.70,
        "b1_ex_rank": 1,
        "b1_ex_dev": -0.15,
        "b1_ex_st": 0.08,
        "b1_kimarite_rate_nige": 70.0,
        "b1_accident_rate": 0.6,
        "b2_age": 35,
        "b2_avg_st": 0.14,
        "b2_national_rate": 5.5,
        "b2_local_rate": 5.1,
        "b2_national_rate2": 35.0,
        "b2_local_rate2": 31.0,
        "b2_motor_rate2": 37.0,
        "b2_ex_time": 6.80,
        "b2_ex_st": 0.10,
        "result_tansho": 1,
        "payout_tansho": 180,
        "result_nirentan": "1-2",
        "payout_nirentan": 650,
        "result_sanrentan": "1-2-3",
        "payout_sanrentan": 1230,
    }
    row.update(overrides)
    return row


def _make_db(path: Path, rows: list[dict[str, object]]) -> Path:
    with sqlite3.connect(path) as conn:
        create_output_schema(conn)
        for row in rows:
            columns = list(row)
            conn.execute(
                f"INSERT INTO asof_race_features ({','.join(columns)}) "
                f"VALUES ({','.join('?' for _ in columns)})",
                [row[column] for column in columns],
            )
    return path


@pytest.fixture
def fixture_db(tmp_path: Path) -> Path:
    return _make_db(
        tmp_path / "synthetic.db",
        [
            _row("r1", "2023-05-10"),
            _row(
                "r2",
                "2023-06-10",
                weather="曇",
                b1_class="A2",
                b1_racer_id=5000,
                b1_age=40,
                b1_avg_st=0.18,
                b1_national_rate=5.0,
                b1_local_rate=4.5,
                b1_national_rate2=30.0,
                b1_local_rate2=20.0,
                b1_motor_rate2=30.0,
                b1_ex_rank=4,
                b1_ex_dev=None,
                b1_ex_st=0.14,
                b1_kimarite_rate_nige=30.0,
                b1_accident_rate=0.2,
                result_tansho=2,
                payout_tansho=250,
                result_nirentan="2-1",
                payout_nirentan=900,
                result_sanrentan="2-1-3",
                payout_sanrentan=2000,
            ),
            _row(
                "r3",
                "2024-01-10",
                result_tansho=1,
                payout_tansho=220,
                result_nirentan="1-2",
                payout_nirentan=700,
                result_sanrentan="1-2-3",
                payout_sanrentan=1500,
                b1_ex_dev=-0.12,
            ),
            _row(
                "r4",
                "2024-02-10",
                result_tansho=None,
                payout_tansho=None,
                result_nirentan=None,
                payout_nirentan=None,
                result_sanrentan=None,
                payout_sanrentan=None,
                b1_ex_dev=-0.2,
            ),
            _row("old", "2022-12-31"),
        ],
    )


@pytest.mark.parametrize(
    ("bet", "expected_hits", "expected_roi"),
    [
        ({"type": "tansho", "first": 1}, 2, 133.3),
        ({"type": "nirentan", "first": 1, "second": 2}, 2, 450.0),
        ({"type": "sanrentan", "first": 1, "second": 2, "third": 3}, 2, 910.0),
    ],
)
def test_each_bet_type_matches_and_calculates_roi(
    fixture_db: Path, bet: dict[str, object], expected_hits: int, expected_roi: float
) -> None:
    result = search_roi(fixture_db, {"bet": bet, "date_from": "2023-05-01"}, fast=True)

    assert result["n"] == 3
    assert result["hits"] == expected_hits
    assert result["roi"] == expected_roi
    assert result["excluded"] == {"result_missing": 1, "condition_null": 0}


@pytest.mark.parametrize(
    ("bet", "result_json", "payout_json", "expected_roi"),
    [
        ({"type": "tansho", "first": 2}, ["1", "2"], {"1": 130, "2": 380}, 380.0),
        (
            {"type": "nirentan", "first": 2, "second": 1},
            ["1-2", "2-1"],
            {"1-2": 190, "2-1": 520},
            520.0,
        ),
        (
            {"type": "sanrentan", "first": 2, "second": 1, "third": 3},
            ["1-2-3", "2-1-3"],
            {"1-2-3": 780, "2-1-3": 2430},
            2430.0,
        ),
    ],
)
def test_schema_v4_dead_heat_uses_payout_for_the_matching_ticket(
    tmp_path: Path,
    bet: dict[str, object],
    result_json: list[str],
    payout_json: dict[str, int],
    expected_roi: float,
) -> None:
    kind = str(bet["type"])
    row = _row(
        "dead-heat",
        "2025-12-11",
        schema_version=4,
        **{
            f"result_{kind}_json": json.dumps(result_json),
            f"payout_{kind}_json": json.dumps(payout_json),
        },
    )
    db = _make_db(tmp_path / f"dead-heat-{kind}.db", [row])

    result = search_roi(db, {"bet": bet}, fast=True)

    assert result["n"] == 1
    assert result["hits"] == 1
    assert result["roi"] == expected_roi
    assert result["excluded"] == {"result_missing": 0, "condition_null": 0}


def test_schema_v4_invalid_winner_payload_is_result_missing(tmp_path: Path) -> None:
    db = _make_db(
        tmp_path / "invalid-v4.db",
        [
            _row(
                "invalid",
                "2025-01-01",
                schema_version=4,
                result_tansho_json='["1","2"]',
                payout_tansho_json='{"1":130}',
            )
        ],
    )

    result = search_roi(
        db, {"bet": {"type": "tansho", "first": 1}}, fast=True
    )

    assert result["n"] == 0
    assert result["excluded"] == {"result_missing": 1, "condition_null": 0}


def test_condition_null_is_excluded_only_when_condition_references_column(fixture_db: Path) -> None:
    bet = {"type": "tansho", "first": 1}

    filtered = search_roi(
        fixture_db,
        {"bet": bet, "date_from": "2023-05-01", "boats": {"1": {"ex_dev": {"faster_by": 0.1}}}},
        fast=True,
    )
    unfiltered = search_roi(fixture_db, {"bet": bet, "date_from": "2023-05-01"}, fast=True)

    assert filtered["n"] == 2
    assert filtered["excluded"] == {"result_missing": 1, "condition_null": 1}
    assert unfiltered["n"] == 3
    assert unfiltered["excluded"] == {"result_missing": 1, "condition_null": 0}


@pytest.mark.parametrize(
    "boat_condition",
    [
        {"class": ["A1", "B1"]},
        {"racer_id": 4320},
        {"age": {"min": 25, "max": 35}},
        {"avg_st": {"min": 0.10, "max": 0.15}},
        {"national_rate": {"min": 6.0}},
        {"local_rate": {"min": 6.0}},
        {"national_rate2": {"min": 40.0}},
        {"local_rate2": {"min": 40.0}},
        {"motor_rate2": {"min": 40.0, "max": 45.0}},
        {"ex_rank": {"min": 1, "max": 3}},
        {"ex_dev": {"faster_by": 0.1}},
        {"ex_st": {"max": 0.1}},
        {"kimarite": {"name": "nige", "rate_min": 60}},
        {"accident_rate": {"min": 0.5}},
    ],
)
def test_boat_operators(fixture_db: Path, boat_condition: dict[str, object]) -> None:
    result = search_roi(
        fixture_db,
        {
            "bet": {"type": "tansho", "first": 1},
            "date_from": "2023-05-01",
            "boats": {"1": boat_condition},
        },
        fast=True,
    )

    assert result["n"] == 2
    assert result["hits"] == 2


def test_slower_by_uses_positive_exhibition_deviation(tmp_path: Path) -> None:
    db = _make_db(tmp_path / "slow.db", [_row("slow", "2024-01-01", b1_ex_dev=0.11)])
    result = search_roi(
        db,
        {"bet": {"type": "tansho", "first": 1}, "boats": {"1": {"ex_dev": {"slower_by": 0.1}}}},
        fast=True,
    )
    assert result["n"] == 1


@pytest.mark.parametrize(
    ("comparison", "overrides"),
    [
        (
            {"metric": "motor_rate2", "boat": 1, "op": "ge", "other": 2, "margin": 5},
            {"b1_motor_rate2": 42.0, "b2_motor_rate2": 37.0},
        ),
        (
            {"metric": "avg_st", "boat": 1, "op": "le", "other": 2, "margin": 0.02},
            {"b1_avg_st": 0.12, "b2_avg_st": 0.14},
        ),
    ],
)
def test_compare_ge_le_margin_boundary_is_inclusive(
    tmp_path: Path, comparison: dict[str, object], overrides: dict[str, object]
) -> None:
    passing = _row("pass", "2025-01-01", schema_version=3, **overrides)
    db = _make_db(tmp_path / "compare-boundary.db", [passing])

    result = search_roi(db, {"compare": [comparison]}, fast=True)

    assert result["n"] == 1
    assert result["excluded"]["condition_null"] == 0


def test_compare_omitted_margin_is_equivalent_to_zero(tmp_path: Path) -> None:
    db = _make_db(tmp_path / "compare-zero.db", [_row("equal", "2026-01-01", b2_age=30)])
    base = {"metric": "age", "boat": 1, "op": "ge", "other": 2}

    omitted = search_roi(db, {"compare": [base]}, fast=True)
    explicit = search_roi(db, {"compare": [{**base, "margin": 0}]}, fast=True)

    assert omitted == explicit


def test_t5_odds_range_is_inclusive_and_missing_rows_are_condition_null(tmp_path: Path) -> None:
    db = _make_db(
        tmp_path / "odds.db",
        [
            _row("lower", "2026-05-02"),
            _row("upper", "2026-05-02"),
            _row("outside", "2026-05-02"),
            _row("missing", "2026-05-02"),
        ],
    )
    with sqlite3.connect(db) as connection:
        connection.execute(
            "CREATE TABLE odds_snapshot (race_id TEXT, combination TEXT, snapshot TEXT, odds REAL, "
            "PRIMARY KEY (race_id, combination, snapshot))"
        )
        connection.executemany(
            "INSERT INTO odds_snapshot VALUES (?, '1-2-3', 'T-5min', ?)",
            [("lower", 5.0), ("upper", 15.0), ("outside", 15.1)],
        )

    result = search_roi(
        db,
        {"odds": {"snapshot": "T-5min", "min": 5.0, "max": 15.0}},
        fast=True,
    )

    assert result["n"] == 2
    assert result["excluded"] == {"result_missing": 0, "condition_null": 1}


def test_odds_defaults_to_t5_and_no_condition_join_isolation(tmp_path: Path) -> None:
    db = _make_db(tmp_path / "odds-switch.db", [_row("race", "2026-05-02")])
    baseline = search_roi(db, {}, fast=True)
    with sqlite3.connect(db) as connection:
        connection.execute(
            "CREATE TABLE odds_snapshot (race_id TEXT, combination TEXT, snapshot TEXT, odds REAL, "
            "PRIMARY KEY (race_id, combination, snapshot))"
        )
        connection.executemany(
            "INSERT INTO odds_snapshot VALUES ('race', '1-2-3', ?, ?)",
            [("final", 20.0), ("T-5min", 10.0)],
        )

    assert search_roi(db, {}, fast=True) == baseline
    assert search_roi(db, {"odds": {"max": 15}}, fast=True)["n"] == 1
    assert search_roi(db, {"odds": {"snapshot": "T-5min", "max": 15}}, fast=True)["n"] == 1


@pytest.mark.parametrize("snapshot", ["final", "T-1min", ""])
def test_odds_rejects_snapshots_other_than_t5_with_japanese_guidance(
    fixture_db: Path, snapshot: str
) -> None:
    with pytest.raises(
        ValueError, match=r"オッズ条件は5分前オッズ\(T-5min\)のみ対応しています"
    ):
        search_roi(fixture_db, {"odds": {"snapshot": snapshot, "min": 5}}, fast=True)


@pytest.mark.parametrize("kind", ["tansho", "nirentan"])
def test_odds_condition_rejects_non_trifecta_bets(fixture_db: Path, kind: str) -> None:
    bet = {"type": kind, "first": 1}
    if kind == "nirentan":
        bet["second"] = 2
    with pytest.raises(ValueError, match="オッズ条件は現在3連単のみ対応しています"):
        search_roi(fixture_db, {"bet": bet, "odds": {"min": 5}}, fast=True)


@pytest.mark.parametrize(
    ("comparison", "overrides"),
    [
        (
            {"metric": "motor_rate2", "boat": 1, "op": "ge", "other": 2, "margin": 5},
            {"b1_motor_rate2": 41.99, "b2_motor_rate2": 37.0},
        ),
        (
            {"metric": "avg_st", "boat": 1, "op": "le", "other": 2, "margin": 0.02},
            {"b1_avg_st": 0.121, "b2_avg_st": 0.14},
        ),
    ],
)
def test_compare_ge_le_rejects_values_inside_margin(
    tmp_path: Path, comparison: dict[str, object], overrides: dict[str, object]
) -> None:
    failing = _row("fail", "2025-01-01", schema_version=3, **overrides)
    db = _make_db(tmp_path / "compare-fail.db", [failing])

    assert search_roi(db, {"compare": [comparison]}, fast=True)["n"] == 0


def test_compare_null_on_either_side_is_condition_null(tmp_path: Path) -> None:
    db = _make_db(
        tmp_path / "compare-null.db",
        [_row("null", "2025-01-01", schema_version=3, b2_motor_rate2=None)],
    )
    comparison = {"metric": "motor_rate2", "boat": 1, "op": "ge", "other": 2, "margin": 5}

    result = search_roi(db, {"compare": [comparison]}, fast=True)

    assert result["n"] == 0
    assert result["excluded"] == {"result_missing": 0, "condition_null": 1}


def test_multiple_compare_conditions_are_combined_with_and(tmp_path: Path) -> None:
    db = _make_db(
        tmp_path / "compare-and.db",
        [
            _row("both", "2025-01-01", schema_version=3),
            _row("one-only", "2025-01-01", schema_version=3, b1_age=40),
        ],
    )
    comparisons = [
        {"metric": "motor_rate2", "boat": 1, "op": "ge", "other": 2, "margin": 5},
        {"metric": "age", "boat": 1, "op": "le", "other": 2, "margin": 5},
    ]

    result = search_roi(db, {"compare": comparisons}, fast=True)

    assert result["n"] == 1


@pytest.mark.parametrize(
    ("comparison", "message"),
    [
        ({"metric": "drop_table", "boat": 1, "op": "ge", "other": 2, "margin": 0}, "metric"),
        ({"metric": "age", "boat": 7, "op": "ge", "other": 2, "margin": 0}, "boats"),
        ({"metric": "age", "boat": 2, "op": "ge", "other": 2, "margin": 0}, "異なる号艇同士"),
        ({"metric": "age", "boat": 1, "op": "ge", "other": 2, "margin": -1}, "non-negative"),
    ],
)
def test_compare_validates_whitelists_and_invariants(
    fixture_db: Path, comparison: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        search_roi(fixture_db, {"compare": [comparison]}, fast=True)


def test_duplicate_ticket_has_user_facing_japanese_error(fixture_db: Path) -> None:
    with pytest.raises(ValueError, match="着順ごとに異なる艇番"):
        search_roi(
            fixture_db,
            {"bet": {"type": "sanrentan", "first": 1, "second": 1, "third": 3}},
            fast=True,
        )


def test_race_number_range_is_inclusive(tmp_path: Path) -> None:
    db = _make_db(
        tmp_path / "race-number.db",
        [
            _row("r6", "2025-01-01", schema_version=3, race_no=6),
            _row("r7", "2025-01-01", schema_version=3, race_no=7),
            _row("r12", "2025-01-01", schema_version=3, race_no=12),
        ],
    )

    result = search_roi(db, {"race_no": {"min": 7, "max": 12}}, fast=True)

    assert result["n"] == 2


@pytest.mark.parametrize("key", ["age", "national_rate2", "local_rate2"])
def test_step5_boat_ranges_exclude_nulls(
    tmp_path: Path, key: str
) -> None:
    column = f"b1_{key}"
    db = _make_db(
        tmp_path / f"{key}.db",
        [
            _row("match", "2025-01-01", schema_version=3, **{column: 40}),
            _row("miss", "2025-01-01", schema_version=3, **{column: 20}),
            _row("null", "2025-01-01", schema_version=3, **{column: None}),
        ],
    )

    result = search_roi(db, {"boats": {"1": {key: {"min": 30, "max": 50}}}}, fast=True)

    assert result["n"] == 1
    assert result["excluded"]["condition_null"] == 1


def test_schema_v2_v3_and_v4_rows_coexist(tmp_path: Path) -> None:
    db = _make_db(
        tmp_path / "schema-coexist.db",
        [
            _row("v2", "2025-01-01", schema_version=2),
            _row("v3", "2025-01-02", schema_version=3),
            _row(
                "v4",
                "2025-01-03",
                schema_version=4,
                result_sanrentan_json='["1-2-3"]',
                payout_sanrentan_json='{"1-2-3":1230}',
            ),
        ],
    )

    assert search_roi(db, {}, fast=True)["n"] == 3


@pytest.mark.parametrize(
    ("count", "warning"),
    [
        (29, "n<30: 偶然の可能性が高い"),
        (30, "n<100: 上振れの可能性"),
        (99, "n<100: 上振れの可能性"),
        (100, None),
    ],
)
def test_small_n_warning_thresholds(tmp_path: Path, count: int, warning: str | None) -> None:
    rows = [_row(f"r{index}", "2024-01-01") for index in range(count)]
    db = _make_db(tmp_path / f"n{count}.db", rows)
    result = search_roi(db, {"bet": {"type": "tansho", "first": 1}}, fast=True)
    assert result["warnings"] == ([] if warning is None else [warning])


def test_bootstrap_ci_is_seeded_deterministic_and_contains_estimate(fixture_db: Path) -> None:
    conditions = {"bet": {"type": "tansho", "first": 1}, "date_from": "2023-05-01"}
    first = search_roi(fixture_db, conditions, seed=7)
    second = search_roi(fixture_db, conditions, seed=7)

    assert first == second
    assert first["roi_ci_low"] <= first["roi"] <= first["roi_ci_high"]


def test_yearly_totals_equal_overall(fixture_db: Path) -> None:
    result = search_roi(
        fixture_db,
        {"bet": {"type": "tansho", "first": 1}, "date_from": "2023-05-01"},
        fast=True,
    )
    assert sum(year["n"] for year in result["yearly"]) == result["n"]
    assert sum(year["hits"] for year in result["yearly"]) == result["hits"]
    assert result["effective_date_range"] == ["2023-05-10", "2024-01-10"]


def test_monthly_totals_match_each_year_and_skip_empty_months(fixture_db: Path) -> None:
    result = search_roi(
        fixture_db,
        {"bet": {"type": "tansho", "first": 1}, "date_from": "2023-05-01"},
        fast=True,
    )

    assert result["monthly"] == [
        {"year": 2023, "month": 5, "n": 1, "hits": 1, "roi": 180.0},
        {"year": 2023, "month": 6, "n": 1, "hits": 0, "roi": 0.0},
        {"year": 2024, "month": 1, "n": 1, "hits": 1, "roi": 220.0},
    ]
    for yearly in result["yearly"]:
        assert sum(month["n"] for month in result["monthly"] if month["year"] == yearly["year"]) == yearly["n"]


def test_profit_curve_aggregates_by_date_in_order(tmp_path: Path) -> None:
    db = _make_db(
        tmp_path / "curve.db",
        [
            _row("r1", "2026-08-02", result_tansho=2, payout_tansho=0),
            _row("r2", "2026-08-01", result_tansho=2, payout_tansho=0),
            _row("r3", "2026-08-01", payout_tansho=250),
        ],
    )

    result = search_roi(
        db,
        {"bet": {"type": "tansho", "first": 1}},
        fast=True,
        include_profit_curve=True,
    )

    assert result["profit_curve"] == [
        {"date": "2026-08-01", "cumulative": 50},
        {"date": "2026-08-02", "cumulative": -50},
    ]


def test_profit_curve_downsampling_keeps_first_and_last(tmp_path: Path) -> None:
    start = date(2025, 1, 1)
    rows = [_row(f"r{index}", (start + timedelta(days=index)).isoformat()) for index in range(205)]
    db = _make_db(tmp_path / "curve-large.db", rows)

    curve = search_roi(db, {}, fast=True, include_profit_curve=True)["profit_curve"]

    assert len(curve) == 200
    assert curve[0] == {"date": "2025-01-01", "cumulative": 1130}
    assert curve[-1]["date"] == (start + timedelta(days=204)).isoformat()
    assert curve[-1]["cumulative"] == 231650


def test_history_condition_explicitly_enforces_cutoff(fixture_db: Path) -> None:
    result = search_roi(
        fixture_db,
        {
            "bet": {"type": "tansho", "first": 1},
            "date_from": "2020-01-01",
            "boats": {"1": {"kimarite": {"name": "nige", "rate_min": 60}}},
        },
        fast=True,
    )
    assert result["effective_date_range"][0] == "2023-05-10"
    assert sum(year["n"] for year in result["yearly"] if year["year"] < 2023) == 0


def test_empty_history_range_is_unspecified_and_does_not_apply_cutoff(fixture_db: Path) -> None:
    result = search_roi(
        fixture_db,
        {"bet": {"type": "tansho", "first": 1}, "boats": {"1": {"accident_rate": {}}}},
        fast=True,
    )
    assert result["effective_date_range"][0] == "2022-12-31"


def test_omitted_optional_bet_uses_documented_example_ticket(fixture_db: Path) -> None:
    result = search_roi(fixture_db, {"date_from": "2023-05-01"}, fast=True)
    assert result["n"] == 3
    assert result["hits"] == 2
    assert result["roi"] == 910.0


def test_empty_result_is_safe(fixture_db: Path) -> None:
    result = search_roi(fixture_db, {"venue": 24}, fast=True)
    assert result["n"] == 0
    assert result["hits"] == 0
    assert result["roi"] == 0.0
    assert result["effective_date_range"] == [None, None]


def test_database_connection_is_explicitly_read_only(
    fixture_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_connect = sqlite3.connect
    calls: list[tuple[object, object]] = []

    def checked_connect(database: object, *args: object, **kwargs: object) -> sqlite3.Connection:
        calls.append((database, kwargs.get("uri")))
        return original_connect(database, *args, **kwargs)

    monkeypatch.setattr("src.search.roi_search.sqlite3.connect", checked_connect)
    search_roi(fixture_db, {"bet": {"type": "tansho", "first": 1}}, fast=True)

    assert len(calls) == 1
    assert str(calls[0][0]).endswith("?mode=ro")
    assert calls[0][1] is True


@pytest.mark.parametrize(
    "conditions",
    [
        {"drop_table": True},
        {"boats": {"1": {"unknown": 1}}},
        {"boats": {"7": {"class": ["A1"]}}},
        {"bet": {"type": "sanrentan", "first": 1, "second": 2}},
        {"wind_speed": {"minimum": 1}},
    ],
)
def test_unknown_or_invalid_condition_is_rejected(fixture_db: Path, conditions: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        search_roi(fixture_db, conditions, fast=True)


def test_cli_reads_utf8_json_and_outputs_one_json_value(fixture_db: Path, tmp_path: Path) -> None:
    condition_file = tmp_path / "conditions.json"
    condition_file.write_text(
        json.dumps({"bet": {"type": "tansho", "first": 1}, "weather": ["晴"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/search_roi.py",
            "--db",
            str(fixture_db),
            "--conditions",
            str(condition_file),
            "--fast",
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert json.loads(completed.stdout)["n"] == 3
    assert completed.stderr == ""
