"""Round 3 correctness audit against the immutable Kachisuji snapshots.

These tests intentionally recompute expected values from explicit SQL instead
of using the product condition compiler.  The seven former BUG-R3-001/002/003
strict-xfail cases now exercise a temporary schema-v4 sample or synthetic raw
history and must pass normally.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date
import json
import math
from pathlib import Path
import sqlite3

import pytest

from src.features.asof_builder import _load_histories, build_features
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
ACCIDENT_CODES = (
    "K0",
    "K1",
    "S0",
    "S1",
    "S2",
    "F",
    "L",
    "\u5931",
    "\u5931\u683c",
    "\u8ee2",
    "\u843d",
    "\u59a8",
)


def _read_only(path: Path, *, rows: bool = False) -> sqlite3.Connection:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    if rows:
        connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _representative_dates(
    *, where: str = "1=1", leading: int, trailing: int
) -> tuple[str, ...]:
    """Select stable edge coverage from the dates that the current snapshot has."""

    with _read_only(SEARCH_DB) as connection:
        dates = [
            str(row[0])
            for row in connection.execute(
                f"""
                SELECT DISTINCT race_date
                  FROM asof_race_features
                 WHERE schema_version IN (2,3,4,5) AND {where}
                 ORDER BY race_date
                """
            )
        ]
    assert len(dates) >= leading + trailing
    return tuple(dict.fromkeys([*dates[:leading], *dates[-trailing:]]))


MATCH_DATES = _representative_dates(where="jcd=12", leading=0, trailing=2)
DATE_BOUND_DATES = _representative_dates(leading=3, trailing=2)


def _independent_ticket_return(
    schema_version: int,
    result: int | str | None,
    payout: int | None,
    result_json: str | None,
    payout_json: str | None,
    ticket: int | str,
) -> tuple[bool, float] | None:
    """Decode one raw snapshot row without calling the product compiler/engine."""

    if schema_version < 4:
        if result is None or payout is None:
            return None
        hit = result == ticket
        return hit, float(payout) if hit else 0.0

    try:
        winning_tickets = json.loads(result_json)  # type: ignore[arg-type]
        payout_by_ticket = json.loads(payout_json)  # type: ignore[arg-type]
        if (
            not isinstance(winning_tickets, list)
            or not winning_tickets
            or not isinstance(payout_by_ticket, dict)
            or any(not isinstance(value, str) for value in winning_tickets)
            or any(value not in payout_by_ticket for value in winning_tickets)
        ):
            return None
        normalized_payouts = {
            str(key): float(value) for key, value in payout_by_ticket.items()
        }
    except (TypeError, ValueError, json.JSONDecodeError):
        return None

    expected = str(ticket)
    hit = expected in winning_tickets
    return hit, normalized_payouts[expected] if hit else 0.0


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


@pytest.fixture(scope="module")
def round4_sample_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    output = tmp_path_factory.mktemp("kachisuji-round4") / "sample.db"
    with _read_only(SOURCE_DB) as source:
        build_features(
            source,
            output,
            "2016-06-13",
            "2016-06-13",
            built_at="round4-test",
        )
        build_features(
            source,
            output,
            "2025-12-11",
            "2025-12-11",
            built_at="round4-test",
        )
    return output


@pytest.mark.parametrize(
    ("kind", "ticket", "result_column", "payout_column"),
    [
        ("tansho", 1, "result_tansho", "payout_tansho"),
        ("nirentan", "1-2", "result_nirentan", "payout_nirentan"),
        ("sanrentan", "1-2-3", "result_sanrentan", "payout_sanrentan"),
    ],
)
def test_roi_matches_independent_sql_for_all_bet_types(
    kind: str,
    ticket: int | str,
    result_column: str,
    payout_column: str,
) -> None:
    sql = f"""
        SELECT schema_version,
               {result_column} AS result_value, {payout_column} AS payout_value,
               {result_column}_json AS result_values_json,
               {payout_column}_json AS payout_values_json,
               b1_motor_rate2, b2_national_rate, b1_age, b2_age
          FROM asof_race_features
         WHERE schema_version IN (2, 3, 4, 5)
           AND jcd = 12
           AND race_date BETWEEN '2025-01-01' AND '2025-03-31'
           AND race_no BETWEEN 7 AND 12
           AND (b1_motor_rate2 >= 35 OR b1_motor_rate2 IS NULL)
           AND (b2_national_rate >= 4 OR b2_national_rate IS NULL)
           AND ((b1_age - b2_age) <= 0 OR b1_age IS NULL OR b2_age IS NULL)
    """
    with _read_only(SEARCH_DB) as connection:
        rows = connection.execute(sql).fetchall()
    condition_null = sum(any(value is None for value in row[5:]) for row in rows)
    usable = [row for row in rows if not any(value is None for value in row[5:])]
    outcomes = [_independent_ticket_return(*row[:5], ticket) for row in usable]
    result_missing = sum(outcome is None for outcome in outcomes)
    included = [outcome for outcome in outcomes if outcome is not None]
    assert included
    hits = sum(outcome[0] for outcome in included)
    roi = round(sum(outcome[1] for outcome in included) / len(included), 1)

    manual = (len(included), hits, roi, result_missing, condition_null)
    actual = search_roi(SEARCH_DB, _base_conditions(kind), fast=True)
    assert (actual["n"], actual["hits"], actual["roi"]) == manual[:3]
    assert actual["excluded"] == {
        "result_missing": manual[3],
        "condition_null": manual[4],
    }


@pytest.mark.parametrize(
    ("bet_type", "result_column", "payout_column"),
    [
        ("win", "result_tansho", "payout_tansho"),
        ("exacta", "result_nirentan", "payout_nirentan"),
        ("trifecta", "result_sanrentan", "payout_sanrentan"),
    ],
)
def test_unique_source_payouts_preserve_combination_and_100_yen_amount(
    bet_type: str, result_column: str, payout_column: str
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
        expected_rows = connection.execute(
            f"""
            SELECT COUNT(*)
              FROM snapshot.asof_race_features a
             WHERE a.race_date >= '2021-01-01'
               AND a.{result_column} IS NOT NULL
               AND (SELECT COUNT(*) FROM race_payouts p
                     WHERE p.race_id=a.race_id AND p.bet_type=?) = 1
            """,
            (bet_type,),
        ).fetchone()[0]
    assert expected_rows > 0
    assert checked == expected_rows
    assert result_mismatches == 0
    assert payout_mismatches == 0


@pytest.mark.parametrize(
    ("result_column", "payout_column", "expected_result", "expected_payout"),
    [
        ("result_tansho", "payout_tansho", 1, 110),
        ("result_nirentan", "payout_nirentan", "1-4", 350),
        ("result_sanrentan", "payout_sanrentan", "1-4-5", 1550),
    ],
)
def test_snapshot_result_and_payout_match_final_finishing_order(
    round4_sample_db: Path,
    result_column: str,
    payout_column: str,
    expected_result: int | str,
    expected_payout: int,
) -> None:
    with _read_only(round4_sample_db) as connection:
        row = connection.execute(
            f"SELECT schema_version,{result_column},{payout_column} "
            "FROM asof_race_features WHERE race_id='20160613-13-01'"
        ).fetchone()
    assert row == (5, expected_result, expected_payout)


@pytest.mark.parametrize(
    (
        "bet_type",
        "result_column",
        "payout_column",
        "expected_tickets",
        "expected_payouts",
    ),
    [
        (
            "win",
            "result_tansho_json",
            "payout_tansho_json",
            ["1", "2"],
            {"1": 130, "2": 380},
        ),
        (
            "exacta",
            "result_nirentan_json",
            "payout_nirentan_json",
            ["1-2", "2-1"],
            {"1-2": 190, "2-1": 520},
        ),
        (
            "trifecta",
            "result_sanrentan_json",
            "payout_sanrentan_json",
            ["1-2-5", "2-1-5"],
            {"1-2-5": 780, "2-1-5": 2430},
        ),
    ],
)
def test_every_dead_heat_winning_ticket_is_represented(
    round4_sample_db: Path,
    bet_type: str,
    result_column: str,
    payout_column: str,
    expected_tickets: list[str],
    expected_payouts: dict[str, int],
) -> None:
    race_id = "20251211-17-01"
    with _read_only(round4_sample_db) as snapshot:
        result_json, payout_json = snapshot.execute(
            f"SELECT {result_column},{payout_column} FROM asof_race_features WHERE race_id=?",
            (race_id,),
        ).fetchone()
    with _read_only(SOURCE_DB) as source:
        official = dict(
            source.execute(
                "SELECT combination,payout FROM race_payouts "
                "WHERE race_id=? AND bet_type=? ORDER BY combination",
                (race_id, bet_type),
            ).fetchall()
        )
    assert official == expected_payouts
    assert json.loads(result_json) == expected_tickets
    assert json.loads(payout_json) == expected_payouts


def test_previous_day_features_match_independent_raw_recomputation() -> None:
    with _read_only(SEARCH_DB, rows=True) as snapshot, _read_only(SOURCE_DB) as source:
        total_rows, chronology_errors = snapshot.execute(
            "SELECT COUNT(*), SUM(asof_date >= race_date) FROM asof_race_features"
        ).fetchone()
        min_date, max_date = snapshot.execute(
            "SELECT MIN(race_date), MAX(race_date) FROM asof_race_features"
        ).fetchone()
        source_rows = source.execute(
            "SELECT COUNT(*) FROM races WHERE race_date BETWEEN ? AND ?",
            (min_date, max_date),
        ).fetchone()[0]
        assert total_rows == source_rows
        assert chronology_errors == 0
        samples = snapshot.execute(
            """
            WITH eligible_years AS (
                SELECT SUBSTR(race_date, 1, 4) AS race_year
                  FROM asof_race_features
                 GROUP BY race_year
                 ORDER BY race_year DESC
                 LIMIT 6
            ), numbered AS (
                SELECT a.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY SUBSTR(a.race_date, 1, 4) ORDER BY a.race_id
                       ) AS sample_no,
                       COUNT(*) OVER (
                           PARTITION BY SUBSTR(a.race_date, 1, 4)
                       ) AS year_rows
                  FROM asof_race_features a
                  JOIN eligible_years y
                    ON y.race_year=SUBSTR(a.race_date, 1, 4)
            )
            SELECT * FROM numbered
             WHERE sample_no IN (
                 1,
                 MAX(1, CAST(year_rows / 3 AS INTEGER)),
                 MAX(1, CAST(year_rows * 2 / 3 AS INTEGER)),
                 year_rows
             )
             ORDER BY race_date, race_id
            """
        ).fetchall()
        rate_sql = (
            "SELECT COUNT(*), "
            + ", ".join(
                "SUM(rr.finishing_position = 1 AND TRIM(COALESCE(rr.kimarite, '')) = ?)"
                for _ in HISTORY_LABELS
            )
            + ", SUM((rr.finishing_position IS NULL OR "
            "CAST(rr.finishing_position AS INTEGER) NOT BETWEEN 1 AND 6) "
            "AND TRIM(COALESCE(rr.remarks, '')) IN ("
            + ",".join("?" for _ in ACCIDENT_CODES)
            + ")) FROM races r JOIN race_entries e USING (race_id) "
            "JOIN race_results rr ON rr.race_id=e.race_id AND rr.boat_number=e.boat_number "
            "WHERE e.racer_number=? "
            "AND r.race_date BETWEEN DATE(?,'-364 days') AND ?"
        )
        rate_comparisons = 0
        age_comparisons = 0
        sampled_racers = 0
        racers_with_birth_dates = 0
        for row in samples:
            assert row["asof_date"] < row["race_date"]
            for boat in range(1, 7):
                racer_id = row[f"b{boat}_racer_id"]
                if racer_id is None:
                    continue
                sampled_racers += 1
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
                expected_rates = (
                    [None] * 7
                    if starts == 0
                    else [value * 100.0 / starts for value in raw[1:]]
                )
                columns = [
                    *(f"b{boat}_kimarite_rate_{key}" for key in HISTORY_LABELS),
                    f"b{boat}_accident_rate_365d",
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
                    racers_with_birth_dates += 1
                    born = date.fromisoformat(str(birth[0])[:10])
                    raced = date.fromisoformat(row["race_date"])
                    expected_age = (
                        raced.year
                        - born.year
                        - ((raced.month, raced.day) < (born.month, born.day))
                    )
                    assert row[f"b{boat}_age"] == expected_age
                    age_comparisons += 1
    samples_per_year: dict[str, int] = {}
    for row in samples:
        samples_per_year[row["race_date"][:4]] = (
            samples_per_year.get(row["race_date"][:4], 0) + 1
        )
    assert len(samples_per_year) == 6
    assert set(samples_per_year.values()) == {4}
    assert rate_comparisons == sampled_racers * 7
    assert age_comparisons == racers_with_birth_dates


def test_historical_result_fields_are_internally_consistent() -> None:
    source = sqlite3.connect(":memory:")
    source.executescript(
        """
        CREATE TABLE races (race_id TEXT PRIMARY KEY, race_date TEXT);
        CREATE TABLE race_entries (
          race_id TEXT, boat_number INTEGER, racer_number INTEGER
        );
        CREATE TABLE race_results (
          race_id TEXT, boat_number INTEGER, finishing_position INTEGER,
          kimarite TEXT, remarks TEXT
        );
        INSERT INTO races VALUES ('winner','2025-01-01'),('stale','2025-01-02'),
                                 ('accident','2025-01-03');
        INSERT INTO race_entries VALUES ('winner',1,1001),('stale',1,1001),
                                        ('accident',1,1001);
        INSERT INTO race_results VALUES ('winner',1,1,'逃げ',NULL),
                                        ('stale',1,2,'まくり','S0'),
                                        ('accident',1,NULL,NULL,'S0');
        """
    )

    history = _load_histories(source, "2025-01-01", "2025-01-03", [1001])[1001]
    rates = history.rates("2025-01-01", "2025-01-03")

    assert rates["nige"] == pytest.approx(100 / 3)
    assert rates["makuri"] == pytest.approx(0.0)
    assert rates["accident"] == pytest.approx(100 / 3)


@pytest.mark.parametrize(("op", "margin"), [("ge", 0), ("le", 0), ("ge", 5), ("le", 5)])
def test_comparison_margin_boundaries_match_signed_difference_sql(
    op: str, margin: int
) -> None:
    operator = ">=" if op == "ge" else "<="
    threshold = margin if op == "ge" else -margin
    conditions = {
        "date_from": "2025-01-01",
        "date_to": "2025-01-31",
        "bet": {"type": "tansho", "first": 1},
        "compare": [
            {"metric": "age", "boat": 1, "op": op, "other": 2, "margin": margin}
        ],
    }
    with _read_only(SEARCH_DB) as connection:
        rows = connection.execute(
            f"""
            SELECT schema_version,result_tansho,payout_tansho,
                   result_tansho_json,payout_tansho_json,b1_age,b2_age
              FROM asof_race_features
             WHERE schema_version IN (2,3,4,5)
               AND race_date BETWEEN '2025-01-01' AND '2025-01-31'
               AND ((b1_age - b2_age) {operator} ? OR b1_age IS NULL OR b2_age IS NULL)
            """,
            (threshold,),
        ).fetchall()
    condition_null = sum(row[5] is None or row[6] is None for row in rows)
    outcomes = [
        _independent_ticket_return(*row[:5], 1)
        for row in rows
        if row[5] is not None and row[6] is not None
    ]
    result_missing = sum(outcome is None for outcome in outcomes)
    n = len(outcomes) - result_missing
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
            {
                "compare": [
                    {
                        "metric": "age",
                        "boat": 1,
                        "op": "ge",
                        "other": 2,
                        "margin": -0.01,
                    }
                ]
            },
            fast=True,
        )
    with _read_only(SEARCH_DB) as connection:
        target_date = connection.execute(
            """
            SELECT race_date
              FROM asof_race_features
             WHERE schema_version IN (2,3,4,5)
             GROUP BY race_date
            HAVING SUM(b1_ex_time IS NULL OR b2_ex_time IS NULL) > 0
             ORDER BY race_date DESC
             LIMIT 1
            """
        ).fetchone()[0]
        raw_rows = connection.execute(
            """
            SELECT schema_version,result_tansho,payout_tansho,
                   result_tansho_json,payout_tansho_json,b1_ex_time,b2_ex_time
              FROM asof_race_features
             WHERE schema_version IN (2,3,4,5) AND race_date=?
               AND ((b1_ex_time-b2_ex_time)>=0 OR b1_ex_time IS NULL OR b2_ex_time IS NULL)
            """,
            (target_date,),
        ).fetchall()
    condition_null = sum(row[5] is None or row[6] is None for row in raw_rows)
    outcomes = [
        _independent_ticket_return(*row[:5], 1)
        for row in raw_rows
        if row[5] is not None and row[6] is not None
    ]
    expected_missing = sum(outcome is None for outcome in outcomes)
    expected_n = len(outcomes) - expected_missing
    result = search_roi(
        SEARCH_DB,
        {
            "date_from": target_date,
            "date_to": target_date,
            "compare": [
                {"metric": "ex_time", "boat": 1, "op": "ge", "other": 2, "margin": 0}
            ],
        },
        fast=True,
    )
    assert condition_null > 0
    assert result["n"] == expected_n
    assert result["excluded"] == {
        "result_missing": expected_missing,
        "condition_null": condition_null,
    }


@pytest.mark.parametrize(
    ("target_date", "same_day"),
    [
        (target_date, same_day)
        for target_date in MATCH_DATES
        for same_day in (False, True)
    ],
)
def test_match_races_sets_equal_independent_step2_predicates(
    target_date: str, same_day: bool
) -> None:
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
                 WHERE schema_version IN (2,3,4,5) AND race_date=? AND jcd=12
                   AND race_no BETWEEN 7 AND 12
                   AND (b1_motor_rate2>=35 OR b1_motor_rate2 IS NULL)
                   AND (b1_ex_rank<=3 OR b1_ex_rank IS NULL)
                   AND ((b1_ex_time-b2_ex_time)<=0 OR b1_ex_time IS NULL OR b2_ex_time IS NULL)
                """,
                (target_date,),
            ).fetchall()
            expected_confirmed = {
                row["race_id"]
                for row in rows
                if all(row[key] is not None for key in row.keys()[1:])
            }
            expected_pending = {
                row["race_id"]
                for row in rows
                if row["b1_motor_rate2"] is not None
                and any(
                    row[key] is None
                    for key in ("b1_ex_rank", "b1_ex_time", "b2_ex_time")
                )
            }
        else:
            rows = connection.execute(
                """
                SELECT race_id,b1_motor_rate2,b1_age,b2_age
                  FROM asof_race_features
                 WHERE schema_version IN (2,3,4,5) AND race_date=? AND jcd=12
                   AND race_no BETWEEN 7 AND 12
                   AND (b1_motor_rate2>=35 OR b1_motor_rate2 IS NULL)
                   AND ((b1_age-b2_age)<=0 OR b1_age IS NULL OR b2_age IS NULL)
                """,
                (target_date,),
            ).fetchall()
            expected_confirmed = {
                row["race_id"]
                for row in rows
                if all(row[key] is not None for key in row.keys()[1:])
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
    assert (
        matched["counts"]["matched"] == step2["n"] + step2["excluded"]["result_missing"]
    )


@pytest.mark.parametrize(
    "target_date",
    DATE_BOUND_DATES,
)
def test_date_bounds_are_inclusive_and_effective_range_uses_included_rows(
    target_date: str,
) -> None:
    with _read_only(SEARCH_DB) as connection:
        raw_rows = connection.execute(
            """
            SELECT schema_version,result_tansho,payout_tansho,
                   result_tansho_json,payout_tansho_json
              FROM asof_race_features
             WHERE schema_version IN (2,3,4,5) AND race_date=?
            """,
            (target_date,),
        ).fetchall()
    outcomes = [_independent_ticket_return(*row, 1) for row in raw_rows]
    expected_missing = sum(outcome is None for outcome in outcomes)
    expected_n = len(outcomes) - expected_missing
    assert raw_rows
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
            "boats": {"1": {"accident_rate_365d": {"min": 0}}},
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
        return search_roi(
            SEARCH_DB, conditions, seed=20260815, bootstrap_iterations=500
        )

    baseline = run()
    assert run() == baseline
    with ThreadPoolExecutor(max_workers=4) as executor:
        concurrent = list(executor.map(lambda _index: run(), range(8)))
    assert all(result == baseline for result in concurrent)
