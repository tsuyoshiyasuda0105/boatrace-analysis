"""Round 3 correctness audit against the immutable Kachisuji snapshots.

These tests intentionally recompute expected values from explicit SQL instead
of using the product condition compiler.  Known defects are strict xfails so a
future repair becomes an XPASS that must be reviewed rather than disappearing.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date
import math
from pathlib import Path
import sqlite3

import pytest

from src.search.roi_search import search_roi
from src.search.strategies import match_races


ROOT = Path(__file__).resolve().parents[1]
SEARCH_DB = ROOT / "data" / "kachisuji_search.db"
SOURCE_DB = ROOT / "data" / "boatrace.db"
HISTORY_LABELS = {
    "nige": "\u9003\u3052",
    "sashi": "\u5dee\u3057",
    "makuri": "\u307e\u304f\u308a",
    "makurizashi": "\u307e\u304f\u308a\u5dee\u3057",
    "nuki": "\u629c\u304d",
    "megumare": "\u6075\u307e\u308c",
}
ACCIDENT_CODES = ("K0", "K1", "S0", "S1", "S2", "F", "L", "\u5931", "\u5931\u683c", "\u8ee2", "\u843d", "\u59a8")


def _read_only(path: Path, *, rows: bool = False) -> sqlite3.Connection:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    if rows:
        connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _base_conditions(kind: str) -> dict[str, object]:
    bet: dict[str, object] = {"type": kind, "first": 1}
    if kind != "tansho":
        bet["second"] = 2
    if kind == "sanrentan":
        bet["third"] = 3
    return {
        "venue": 12,
        "date_from": "2025-01-01",
        "date_to": "2025-03-31",
        "race_no": {"min": 7, "max": 12},
        "boats": {
            "1": {"motor_rate2": {"min": 35}},
            "2": {"national_rate": {"min": 4}},
        },
        "compare": [{"metric": "age", "boat": 1, "op": "le", "other": 2, "margin": 0}],
        "bet": bet,
    }


@pytest.mark.parametrize(
    ("kind", "ticket", "result_column", "payout_column", "expected"),
    [
        ("tansho", 1, "result_tansho", "payout_tansho", (38, 30, 100.8, 1, 0)),
        ("nirentan", "1-2", "result_nirentan", "payout_nirentan", (39, 14, 104.1, 0, 0)),
        ("sanrentan", "1-2-3", "result_sanrentan", "payout_sanrentan", (39, 3, 54.4, 0, 0)),
    ],
)
def test_roi_matches_independent_sql_for_all_bet_types(
    kind: str,
    ticket: int | str,
    result_column: str,
    payout_column: str,
    expected: tuple[int, int, float, int, int],
) -> None:
    sql = f"""
        SELECT {result_column} AS result_value, {payout_column} AS payout_value,
               b1_motor_rate2, b2_national_rate, b1_age, b2_age
          FROM asof_race_features
         WHERE schema_version IN (2, 3)
           AND jcd = 12
           AND race_date BETWEEN '2025-01-01' AND '2025-03-31'
           AND race_no BETWEEN 7 AND 12
           AND (b1_motor_rate2 >= 35 OR b1_motor_rate2 IS NULL)
           AND (b2_national_rate >= 4 OR b2_national_rate IS NULL)
           AND ((b1_age - b2_age) <= 0 OR b1_age IS NULL OR b2_age IS NULL)
    """
    with _read_only(SEARCH_DB) as connection:
        rows = connection.execute(sql).fetchall()
    condition_null = sum(any(value is None for value in row[2:]) for row in rows)
    usable = [row for row in rows if not any(value is None for value in row[2:])]
    result_missing = sum(row[0] is None or row[1] is None for row in usable)
    included = [row for row in usable if row[0] is not None and row[1] is not None]
    hits = sum(row[0] == ticket for row in included)
    roi = round(sum(row[1] if row[0] == ticket else 0 for row in included) / len(included), 1)

    manual = (len(included), hits, roi, result_missing, condition_null)
    assert manual == expected
    actual = search_roi(SEARCH_DB, _base_conditions(kind), fast=True)
    assert (actual["n"], actual["hits"], actual["roi"]) == manual[:3]
    assert actual["excluded"] == {
        "result_missing": manual[3],
        "condition_null": manual[4],
    }


@pytest.mark.parametrize(
    ("bet_type", "result_column", "payout_column", "expected_rows"),
    [
        ("win", "result_tansho", "payout_tansho", 308_619),
        ("exacta", "result_nirentan", "payout_nirentan", 308_815),
        ("trifecta", "result_sanrentan", "payout_sanrentan", 308_679),
    ],
)
def test_unique_source_payouts_preserve_combination_and_100_yen_amount(
    bet_type: str, result_column: str, payout_column: str, expected_rows: int
) -> None:
    result_expression = (
        "CAST(a.result_tansho AS TEXT)" if bet_type == "win" else f"a.{result_column}"
    )
    with _read_only(SOURCE_DB) as connection:
        connection.execute(
            "ATTACH DATABASE ? AS snapshot",
            (SEARCH_DB.resolve().as_uri() + "?mode=ro",),
        )
        checked, result_mismatches, payout_mismatches = connection.execute(
            f"""
            WITH one_payout AS (
                SELECT race_id, MIN(combination) AS combination, MIN(payout) AS payout
                  FROM race_payouts
                 WHERE bet_type = ?
                 GROUP BY race_id
                HAVING COUNT(*) = 1
            )
            SELECT COUNT(*),
                   SUM({result_expression} IS NOT one_payout.combination),
                   SUM(a.{payout_column} IS NOT one_payout.payout)
              FROM one_payout
              JOIN snapshot.asof_race_features a USING (race_id)
             WHERE a.race_date >= '2021-01-01'
               AND a.{result_column} IS NOT NULL
            """,
            (bet_type,),
        ).fetchone()
    assert checked == expected_rows
    assert result_mismatches == 0
    assert payout_mismatches == 0


@pytest.mark.xfail(strict=True, reason="BUG-R3-001: historical payout/result rows are stale")
@pytest.mark.parametrize(
    ("bet_type", "legs", "result_column", "payout_column", "observed_mismatches"),
    [
        ("win", 1, "result_tansho", "payout_tansho", 2_833),
        ("exacta", 2, "result_nirentan", "payout_nirentan", 4_091),
        ("trifecta", 3, "result_sanrentan", "payout_sanrentan", 4_396),
    ],
)
def test_snapshot_result_and_payout_match_final_finishing_order(
    bet_type: str,
    legs: int,
    result_column: str,
    payout_column: str,
    observed_mismatches: int,
) -> None:
    expected_expression = (
        "CAST(w1 AS TEXT)"
        if legs == 1
        else "w1 || '-' || w2"
        if legs == 2
        else "w1 || '-' || w2 || '-' || w3"
    )
    actual_expression = (
        "CAST(a.result_tansho AS TEXT)" if bet_type == "win" else f"a.{result_column}"
    )
    with _read_only(SOURCE_DB) as connection:
        connection.execute(
            "ATTACH DATABASE ? AS snapshot",
            (SEARCH_DB.resolve().as_uri() + "?mode=ro",),
        )
        checked, mismatches = connection.execute(
            f"""
            WITH finishes AS (
                SELECT race_id,
                       MAX(CASE WHEN finishing_position = 1 THEN boat_number END) AS w1,
                       MAX(CASE WHEN finishing_position = 2 THEN boat_number END) AS w2,
                       MAX(CASE WHEN finishing_position = 3 THEN boat_number END) AS w3,
                       SUM(finishing_position = 1) AS c1,
                       SUM(finishing_position = 2) AS c2,
                       SUM(finishing_position = 3) AS c3
                  FROM race_results GROUP BY race_id
            ), expected AS (
                SELECT *, {expected_expression} AS ticket
                  FROM finishes WHERE c1 = 1 AND c2 = 1 AND c3 = 1
            )
            SELECT COUNT(*),
                   SUM({actual_expression} IS NOT expected.ticket
                       OR a.{payout_column} IS NOT p.payout)
              FROM expected
              JOIN race_payouts p
                ON p.race_id = expected.race_id
               AND p.bet_type = ? AND p.combination = expected.ticket
              JOIN snapshot.asof_race_features a ON a.race_id = expected.race_id
             WHERE a.{result_column} IS NOT NULL AND a.{payout_column} IS NOT NULL
            """,
            (bet_type,),
        ).fetchone()
    assert checked > 545_000
    assert mismatches == observed_mismatches
    assert mismatches == 0


@pytest.mark.xfail(strict=True, reason="BUG-R3-002: dead-heat winning tickets collapse to one result")
@pytest.mark.parametrize(
    ("bet_type", "result_column", "observed_omissions"),
    [
        ("win", "result_tansho", 1),
        ("exacta", "result_nirentan", 4),
        ("trifecta", "result_sanrentan", 20),
    ],
)
def test_every_dead_heat_winning_ticket_is_represented(
    bet_type: str, result_column: str, observed_omissions: int
) -> None:
    actual_expression = (
        "CAST(a.result_tansho AS TEXT)" if bet_type == "win" else f"a.{result_column}"
    )
    with _read_only(SOURCE_DB) as connection:
        connection.execute(
            "ATTACH DATABASE ? AS snapshot",
            (SEARCH_DB.resolve().as_uri() + "?mode=ro",),
        )
        omitted = connection.execute(
            f"""
            SELECT COUNT(*)
              FROM race_payouts p
              JOIN races r USING (race_id)
              JOIN snapshot.asof_race_features a USING (race_id)
             WHERE p.bet_type = ? AND r.race_date >= '2021-01-01'
               AND p.combination IS NOT {actual_expression}
               AND EXISTS (
                   SELECT 1 FROM race_payouts p2
                    WHERE p2.race_id = p.race_id AND p2.bet_type = p.bet_type
                    GROUP BY p2.race_id HAVING COUNT(*) > 1
               )
            """,
            (bet_type,),
        ).fetchone()[0]
    assert omitted == observed_omissions
    assert omitted == 0


def test_previous_day_features_match_independent_raw_recomputation() -> None:
    with _read_only(SEARCH_DB, rows=True) as snapshot, _read_only(SOURCE_DB) as source:
        total_rows, chronology_errors = snapshot.execute(
            "SELECT COUNT(*), SUM(asof_date >= race_date) FROM asof_race_features"
        ).fetchone()
        assert total_rows == 557_425
        assert chronology_errors == 0
        samples = snapshot.execute(
            """
            WITH numbered AS (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY SUBSTR(race_date, 1, 4) ORDER BY race_id
                ) AS sample_no
                  FROM asof_race_features
                 WHERE race_date BETWEEN '2021-01-01' AND '2026-08-14'
            )
            SELECT * FROM numbered WHERE sample_no IN (1, 10000, 20000, 30000)
             ORDER BY race_date, race_id
            """
        ).fetchall()
        rate_sql = (
            "SELECT COUNT(*), "
            + ", ".join(
                "SUM(TRIM(COALESCE(rr.kimarite, '')) = ?)" for _ in HISTORY_LABELS
            )
            + ", SUM(TRIM(COALESCE(rr.remarks, '')) IN ("
            + ",".join("?" for _ in ACCIDENT_CODES)
            + ")) FROM races r JOIN race_entries e USING (race_id) "
            "JOIN race_results rr ON rr.race_id=e.race_id AND rr.boat_number=e.boat_number "
            "WHERE e.racer_number=? "
            "AND r.race_date BETWEEN DATE(?,'-364 days') AND ?"
        )
        rate_comparisons = 0
        age_comparisons = 0
        for row in samples:
            assert row["asof_date"] < row["race_date"]
            for boat in range(1, 7):
                racer_id = row[f"b{boat}_racer_id"]
                if racer_id is None:
                    continue
                raw = source.execute(
                    rate_sql,
                    (
                        *HISTORY_LABELS.values(),
                        *ACCIDENT_CODES,
                        racer_id,
                        row["asof_date"],
                        row["asof_date"],
                    ),
                ).fetchone()
                starts = raw[0]
                expected_rates = [None] * 7 if starts == 0 else [value * 100.0 / starts for value in raw[1:]]
                columns = [
                    *(f"b{boat}_kimarite_rate_{key}" for key in HISTORY_LABELS),
                    f"b{boat}_accident_rate",
                ]
                for column, expected in zip(columns, expected_rates):
                    actual = row[column]
                    assert (actual is None and expected is None) or (
                        actual is not None
                        and expected is not None
                        and math.isclose(actual, expected, rel_tol=0.0, abs_tol=1e-10)
                    )
                    rate_comparisons += 1

                birth = source.execute(
                    "SELECT birth_date FROM racers WHERE racer_number = ?", (racer_id,)
                ).fetchone()
                if birth is not None and birth[0] is not None:
                    born = date.fromisoformat(str(birth[0])[:10])
                    raced = date.fromisoformat(row["race_date"])
                    expected_age = raced.year - born.year - (
                        (raced.month, raced.day) < (born.month, born.day)
                    )
                    assert row[f"b{boat}_age"] == expected_age
                    age_comparisons += 1
    assert len(samples) == 24
    assert rate_comparisons == 924
    assert age_comparisons == 124


@pytest.mark.xfail(strict=True, reason="BUG-R3-003: stale historical result fields pollute raw rates")
def test_historical_result_fields_are_internally_consistent() -> None:
    placeholders = ",".join("?" for _ in ACCIDENT_CODES)
    with _read_only(SOURCE_DB) as connection:
        nonwinner_kimarite = connection.execute(
            """
            SELECT COUNT(*) FROM race_results rr JOIN races r USING (race_id)
             WHERE TRIM(COALESCE(rr.kimarite, '')) <> '' AND finishing_position <> 1
            """
        ).fetchone()[0]
        numeric_accidents = connection.execute(
            f"""
            SELECT COUNT(*) FROM race_results rr JOIN races r USING (race_id)
             WHERE TRIM(COALESCE(rr.remarks, '')) IN ({placeholders})
               AND finishing_position BETWEEN 1 AND 6
            """,
            ACCIDENT_CODES,
        ).fetchone()[0]
    assert nonwinner_kimarite == 6_435
    assert numeric_accidents == 1_069
    assert (nonwinner_kimarite, numeric_accidents) == (0, 0)


@pytest.mark.parametrize(("op", "margin"), [("ge", 0), ("le", 0), ("ge", 5), ("le", 5)])
def test_comparison_margin_boundaries_match_signed_difference_sql(op: str, margin: int) -> None:
    operator = ">=" if op == "ge" else "<="
    threshold = margin if op == "ge" else -margin
    conditions = {
        "date_from": "2025-01-01",
        "date_to": "2025-01-31",
        "bet": {"type": "tansho", "first": 1},
        "compare": [{"metric": "age", "boat": 1, "op": op, "other": 2, "margin": margin}],
    }
    with _read_only(SEARCH_DB) as connection:
        rows = connection.execute(
            f"""
            SELECT result_tansho, payout_tansho, b1_age, b2_age
              FROM asof_race_features
             WHERE schema_version IN (2,3)
               AND race_date BETWEEN '2025-01-01' AND '2025-01-31'
               AND ((b1_age - b2_age) {operator} ? OR b1_age IS NULL OR b2_age IS NULL)
            """,
            (threshold,),
        ).fetchall()
    condition_null = sum(row[2] is None or row[3] is None for row in rows)
    usable = [row for row in rows if row[2] is not None and row[3] is not None]
    result_missing = sum(row[0] is None or row[1] is None for row in usable)
    n = len(usable) - result_missing
    actual = search_roi(SEARCH_DB, conditions, fast=True)
    assert actual["n"] == n
    assert actual["excluded"] == {
        "result_missing": result_missing,
        "condition_null": condition_null,
    }


def test_negative_comparison_margin_is_rejected_and_null_side_is_excluded() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        search_roi(
            SEARCH_DB,
            {"compare": [{"metric": "age", "boat": 1, "op": "ge", "other": 2, "margin": -0.01}]},
            fast=True,
        )
    result = search_roi(
        SEARCH_DB,
        {
            "date_from": "2024-01-01",
            "date_to": "2024-01-01",
            "compare": [{"metric": "ex_time", "boat": 1, "op": "ge", "other": 2, "margin": 0}],
        },
        fast=True,
    )
    assert result["n"] == 0
    assert result["excluded"] == {"result_missing": 0, "condition_null": 156}


@pytest.mark.parametrize(
    ("target_date", "same_day"),
    [("2025-01-02", False), ("2025-01-02", True), ("2026-08-15", False), ("2026-08-15", True)],
)
def test_match_races_sets_equal_independent_step2_predicates(target_date: str, same_day: bool) -> None:
    conditions: dict[str, object] = {
        "venue": 12,
        "race_no": {"min": 7, "max": 12},
        "boats": {"1": {"motor_rate2": {"min": 35}}},
        "bet": {"type": "tansho", "first": 1},
    }
    if same_day:
        conditions["boats"] = {"1": {"motor_rate2": {"min": 35}, "ex_rank": {"max": 3}}}
        conditions["compare"] = [
            {"metric": "ex_time", "boat": 1, "op": "le", "other": 2, "margin": 0}
        ]
    else:
        conditions["compare"] = [
            {"metric": "age", "boat": 1, "op": "le", "other": 2, "margin": 0}
        ]

    with _read_only(SEARCH_DB, rows=True) as connection:
        if same_day:
            rows = connection.execute(
                """
                SELECT race_id,b1_motor_rate2,b1_ex_rank,b1_ex_time,b2_ex_time
                  FROM asof_race_features
                 WHERE schema_version IN (2,3) AND race_date=? AND jcd=12
                   AND race_no BETWEEN 7 AND 12
                   AND (b1_motor_rate2>=35 OR b1_motor_rate2 IS NULL)
                   AND (b1_ex_rank<=3 OR b1_ex_rank IS NULL)
                   AND ((b1_ex_time-b2_ex_time)<=0 OR b1_ex_time IS NULL OR b2_ex_time IS NULL)
                """,
                (target_date,),
            ).fetchall()
            expected_confirmed = {
                row["race_id"] for row in rows if all(row[key] is not None for key in row.keys()[1:])
            }
            expected_pending = {
                row["race_id"]
                for row in rows
                if row["b1_motor_rate2"] is not None
                and any(row[key] is None for key in ("b1_ex_rank", "b1_ex_time", "b2_ex_time"))
            }
        else:
            rows = connection.execute(
                """
                SELECT race_id,b1_motor_rate2,b1_age,b2_age
                  FROM asof_race_features
                 WHERE schema_version IN (2,3) AND race_date=? AND jcd=12
                   AND race_no BETWEEN 7 AND 12
                   AND (b1_motor_rate2>=35 OR b1_motor_rate2 IS NULL)
                   AND ((b1_age-b2_age)<=0 OR b1_age IS NULL OR b2_age IS NULL)
                """,
                (target_date,),
            ).fetchall()
            expected_confirmed = {
                row["race_id"] for row in rows if all(row[key] is not None for key in row.keys()[1:])
            }
            expected_pending = set()

    matched = match_races(conditions, target_date, SEARCH_DB)
    assert {item["race_id"] for item in matched["matched"]} == expected_confirmed
    assert {item["race_id"] for item in matched["pending"]} == expected_pending
    step2 = search_roi(
        SEARCH_DB,
        {**conditions, "date_from": target_date, "date_to": target_date},
        fast=True,
    )
    assert matched["counts"]["pending"] == step2["excluded"]["condition_null"]
    assert matched["counts"]["matched"] == step2["n"] + step2["excluded"]["result_missing"]


@pytest.mark.parametrize(
    ("target_date", "expected_n", "expected_missing"),
    [
        ("2025-01-01", 168, 0),
        ("2025-01-02", 192, 0),
        ("2025-01-03", 215, 1),
        ("2026-08-14", 149, 43),
        ("2026-08-15", 0, 216),
    ],
)
def test_date_bounds_are_inclusive_and_effective_range_uses_included_rows(
    target_date: str, expected_n: int, expected_missing: int
) -> None:
    result = search_roi(
        SEARCH_DB,
        {
            "date_from": target_date,
            "date_to": target_date,
            "bet": {"type": "tansho", "first": 1},
        },
        fast=True,
    )
    assert result["n"] == expected_n
    assert result["excluded"]["result_missing"] == expected_missing
    expected_range = [target_date, target_date] if expected_n else [None, None]
    assert result["effective_date_range"] == expected_range


def test_history_cutoff_controls_effective_date_range() -> None:
    result = search_roi(
        SEARCH_DB,
        {
            "date_from": "2020-01-01",
            "date_to": "2023-05-02",
            "boats": {"1": {"accident_rate": {"min": 0}}},
            "bet": {"type": "tansho", "first": 1},
        },
        fast=True,
    )
    assert result["effective_date_range"] == ["2023-05-01", "2023-05-02"]


def test_bootstrap_is_identical_sequentially_and_concurrently() -> None:
    conditions = {
        "venue": 12,
        "date_from": "2025-01-01",
        "date_to": "2025-03-31",
        "bet": {"type": "sanrentan", "first": 1, "second": 2, "third": 3},
    }

    def run() -> dict[str, object]:
        return search_roi(SEARCH_DB, conditions, seed=20260815, bootstrap_iterations=500)

    baseline = run()
    assert run() == baseline
    with ThreadPoolExecutor(max_workers=4) as executor:
        concurrent = list(executor.map(lambda _index: run(), range(8)))
    assert all(result == baseline for result in concurrent)
