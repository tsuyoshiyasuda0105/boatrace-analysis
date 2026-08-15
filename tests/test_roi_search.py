from __future__ import annotations

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
        "b1_avg_st": 0.12,
        "b1_national_rate": 6.5,
        "b1_local_rate": 6.1,
        "b1_motor_rate2": 42.0,
        "b1_ex_rank": 1,
        "b1_ex_dev": -0.15,
        "b1_ex_st": 0.08,
        "b1_kimarite_rate_nige": 70.0,
        "b1_accident_rate": 0.6,
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
                b1_avg_st=0.18,
                b1_national_rate=5.0,
                b1_local_rate=4.5,
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
        {"avg_st": {"min": 0.10, "max": 0.15}},
        {"national_rate": {"min": 6.0}},
        {"local_rate": {"min": 6.0}},
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
