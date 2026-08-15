from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from src.features.asof_builder import create_output_schema
from src.kachisuji_web.app import create_app
from src.search.strategies import (
    deactivate_strategy,
    get_strategy,
    list_strategies,
    match_races,
    save_strategy,
)


BET = {"type": "sanrentan", "first": 1, "second": 2, "third": 3}


def _row(race_id: str, race_no: int, **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "race_id": race_id,
        "race_date": "2026-08-16",
        "asof_date": "2026-08-15",
        "built_at": "2026-08-15T00:00:00+00:00",
        "schema_version": 2,
        "jcd": 14,
        "race_no": race_no,
        "weather": "晴",
        "wind_speed": 2.0,
        "tide_phase": "満潮前後",
        "female_present": 0,
        "class_mix": "A1単騎",
        "day_index": "初日",
        "daypart": "デイ",
        "b1_class": "A1",
        "b1_racer_id": 4320,
        "b1_age": 30,
        "b1_avg_st": 0.12,
        "b1_national_rate": 7.1,
        "b1_local_rate": 6.8,
        "b1_national_rate2": 45.0,
        "b1_local_rate2": 40.0,
        "b1_motor_rate2": 42.0,
        "b1_ex_time": 6.70,
        "b1_ex_rank": 1,
        "b1_ex_dev": -0.15,
        "b1_ex_st": 0.08,
        "b1_kimarite_rate_nige": 70.0,
        "b1_accident_rate": 0.4,
        "b2_age": 35,
        "b2_avg_st": 0.15,
        "b2_national_rate": 5.5,
        "b2_local_rate": 5.0,
        "b2_national_rate2": 35.0,
        "b2_local_rate2": 30.0,
        "b2_motor_rate2": 35.0,
        "b2_ex_time": 6.80,
        "b2_ex_st": 0.10,
    }
    row.update(overrides)
    return row


@pytest.fixture
def search_db(tmp_path: Path) -> Path:
    path = tmp_path / "search.db"
    rows = [
        _row("confirmed", 1),
        _row("pending", 2, b1_ex_rank=None, b2_ex_time=None),
        _row("prior-miss", 3, b1_class="B1", b1_ex_rank=None),
        _row("prior-null", 4, b1_class=None),
    ]
    with sqlite3.connect(path) as connection:
        create_output_schema(connection)
        for row in rows:
            columns = list(row)
            connection.execute(
                f"INSERT INTO asof_race_features ({','.join(columns)}) "
                f"VALUES ({','.join('?' for _ in columns)})",
                [row[column] for column in columns],
            )
    return path


@pytest.fixture
def strategy_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "strategies.db"
    monkeypatch.setenv("KACHISUJI_STRATEGY_DB", str(path))
    return path


def test_strategy_lifecycle(strategy_db: Path) -> None:
    strategy_id = save_strategy("鳴門1-2-3", {"venue": 14, "bet": BET}, {"roi": 123.4, "n": 50})

    saved = get_strategy(strategy_id)
    assert saved is not None
    assert saved["name"] == "鳴門1-2-3"
    assert saved["conditions"] == {"venue": 14, "bet": BET}
    assert saved["backtest"] == {"roi": 123.4, "n": 50}
    assert saved["owner"] == "local"
    assert saved["is_active"] is True
    assert [item["id"] for item in list_strategies()] == [strategy_id]

    assert deactivate_strategy(strategy_id) is True
    assert deactivate_strategy(strategy_id) is False
    assert list_strategies() == []
    assert list_strategies(include_inactive=True)[0]["is_active"] is False


def test_invalid_or_empty_strategy_is_rejected(strategy_db: Path) -> None:
    with pytest.raises(ValueError, match="unknown condition key"):
        save_strategy("invalid", {"unknown": True})
    with pytest.raises(ValueError, match="name must not be empty"):
        save_strategy("  ", {"bet": BET})
    assert list_strategies() == []


@pytest.mark.parametrize("backtest", ["not-an-object", [], 123, True])
def test_non_object_backtest_is_rejected(strategy_db: Path, backtest: object) -> None:
    with pytest.raises(ValueError, match="JSONオブジェクトまたはnull"):
        save_strategy("invalid backtest", {"bet": BET}, backtest)


def test_null_backtest_is_accepted(strategy_db: Path) -> None:
    strategy_id = save_strategy("null backtest", {"bet": BET}, None)

    assert get_strategy(strategy_id)["backtest"] is None


def test_prior_day_match_is_confirmed(search_db: Path, strategy_db: Path) -> None:
    result = match_races(
        {"venue": 14, "boats": {"1": {"class": ["A1"]}}, "bet": BET},
        "2026-08-16",
        search_db,
        strategy_db,
    )

    assert [item["race_id"] for item in result["matched"]] == ["confirmed", "pending"]
    assert result["pending"] == []
    assert result["counts"] == {"races_on_date": 4, "matched": 2, "pending": 0}


def test_races_on_date_counts_schema_v4_search_population(
    search_db: Path, strategy_db: Path
) -> None:
    with sqlite3.connect(search_db) as connection:
        connection.execute("UPDATE asof_race_features SET schema_version = 4")

    result = match_races({"bet": BET}, "2026-08-16", search_db, strategy_db)

    assert result["counts"] == {"races_on_date": 4, "matched": 4, "pending": 0}


def test_same_day_null_is_pending_but_prior_day_miss_or_null_is_not(
    search_db: Path, strategy_db: Path
) -> None:
    strategy_id = save_strategy(
        "展示1〜3位",
        {"boats": {"1": {"class": ["A1"], "ex_rank": {"min": 1, "max": 3}}}, "bet": BET},
    )

    result = match_races(strategy_id, "2026-08-16", search_db, strategy_db)

    assert [item["race_id"] for item in result["matched"]] == ["confirmed"]
    assert [item["race_id"] for item in result["pending"]] == ["pending"]
    assert result["pending"][0]["status"] == "pending"
    assert result["pending"][0]["undetermined_columns"] == ["b1_ex_rank"]


def test_saved_compare_strategy_round_trips_and_matches(
    search_db: Path, strategy_db: Path
) -> None:
    comparison = {
        "metric": "motor_rate2",
        "boat": 1,
        "op": "ge",
        "other": 2,
        "margin": 5,
    }
    conditions = {
        "race_no": {"min": 1, "max": 2},
        "compare": [comparison],
        "bet": BET,
    }
    strategy_id = save_strategy("艇間比較", conditions, db_path=strategy_db)

    saved = get_strategy(strategy_id, db_path=strategy_db)
    result = match_races(strategy_id, "2026-08-16", search_db, strategy_db)

    assert saved is not None
    assert saved["conditions"] == conditions
    assert [item["race_id"] for item in result["matched"]] == ["confirmed", "pending"]


def test_same_day_compare_null_is_pending(
    search_db: Path, strategy_db: Path
) -> None:
    conditions = {
        "boats": {"1": {"class": ["A1"]}},
        "compare": [
            {"metric": "ex_time", "boat": 1, "op": "le", "other": 2, "margin": 0.05}
        ],
        "bet": BET,
    }

    result = match_races(conditions, "2026-08-16", search_db, strategy_db)

    assert [item["race_id"] for item in result["matched"]] == ["confirmed"]
    assert [item["race_id"] for item in result["pending"]] == ["pending"]
    assert result["pending"][0]["undetermined_columns"] == ["b2_ex_time"]


def test_odds_condition_is_confirmed_when_present_and_pending_when_missing(
    search_db: Path, strategy_db: Path
) -> None:
    with sqlite3.connect(search_db) as connection:
        connection.execute(
            "CREATE TABLE odds_snapshot (race_id TEXT, combination TEXT, snapshot TEXT, odds REAL, "
            "PRIMARY KEY (race_id, combination, snapshot))"
        )
        connection.executemany(
            "INSERT INTO odds_snapshot VALUES (?, '1-2-3', 'T-5min', ?)",
            [("confirmed", 10.0), ("prior-miss", 20.0), ("prior-null", 20.0)],
        )

    result = match_races(
        {"bet": BET, "odds": {"snapshot": "T-5min", "min": 5, "max": 15}},
        "2026-08-16",
        search_db,
        strategy_db,
    )

    assert [item["race_id"] for item in result["matched"]] == ["confirmed"]
    assert [item["race_id"] for item in result["pending"]] == ["pending"]
    assert result["pending"][0]["undetermined_columns"] == ["odds"]


def test_legacy_saved_final_strategy_stays_listed_but_match_is_rejected_with_guidance(
    search_db: Path, strategy_db: Path
) -> None:
    strategy_id = save_strategy(
        "旧確定オッズ手法",
        {"bet": BET, "odds": {"snapshot": "T-5min", "min": 5}},
        db_path=strategy_db,
    )
    legacy_conditions = json.dumps(
        {"bet": BET, "odds": {"snapshot": "final", "min": 5}},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    with sqlite3.connect(strategy_db) as connection:
        connection.execute(
            "UPDATE strategies SET conditions_json = ? WHERE id = ?",
            (legacy_conditions, strategy_id),
        )

    app = create_app(search_db, strategy_db)
    app.config.update(TESTING=True)
    client = app.test_client()

    listed = client.get("/api/strategies")
    matched = client.get(f"/api/strategies/{strategy_id}/matches?date=2026-08-16")

    assert listed.status_code == 200
    assert listed.get_json()[0]["conditions"]["odds"]["snapshot"] == "final"
    assert matched.status_code == 400
    assert matched.get_json()["error"] == "オッズ条件は5分前オッズ(T-5min)のみ対応しています"


def test_no_races_on_date_is_safe(search_db: Path, strategy_db: Path) -> None:
    result = match_races({"bet": BET}, "2026-08-17", search_db, strategy_db)

    assert result["matched"] == []
    assert result["pending"] == []
    assert result["counts"] == {"races_on_date": 0, "matched": 0, "pending": 0}


def test_match_opens_search_database_read_only(
    search_db: Path, strategy_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[object, dict[str, object]]] = []
    real_connect = sqlite3.connect

    def recording_connect(database, *args, **kwargs):
        calls.append((database, kwargs))
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", recording_connect)
    match_races({"bet": BET}, "2026-08-16", search_db, strategy_db)

    assert len(calls) == 1
    assert str(calls[0][0]).endswith("?mode=ro")
    assert calls[0][1].get("uri") is True


def test_api_aggregates_active_strategies_and_search_still_works(
    search_db: Path, strategy_db: Path
) -> None:
    first = save_strategy("会場", {"venue": 14, "bet": BET})
    save_strategy("展示", {"boats": {"1": {"ex_rank": {"max": 3}}}, "bet": BET})
    inactive = save_strategy("停止", {"bet": BET})
    deactivate_strategy(inactive)
    app = create_app(search_db, strategy_db)
    app.config.update(TESTING=True)
    client = app.test_client()

    response = client.get("/api/matches?date=2026-08-16")
    assert response.status_code == 200
    payload = response.get_json()
    assert [item["strategy_id"] for item in payload] == [first, first + 1]

    search = client.post("/api/search", json={"bet": BET, "fast": True})
    assert search.status_code == 200
    assert search.get_json()["n"] == 0


def test_strategy_api_lifecycle(search_db: Path, strategy_db: Path) -> None:
    app = create_app(search_db, strategy_db)
    app.config.update(TESTING=True)
    client = app.test_client()

    created = client.post(
        "/api/strategies",
        json={"name": "API手法", "conditions": {"venue": 14, "bet": BET}, "backtest": {"roi": 110, "n": 80}},
    )
    assert created.status_code == 200
    strategy_id = created.get_json()["id"]
    assert client.get("/api/strategies").get_json()[0]["id"] == strategy_id
    assert client.get(f"/api/strategies/{strategy_id}/matches?date=2026-08-16").status_code == 200
    assert client.delete(f"/api/strategies/{strategy_id}").status_code == 200
    assert client.get("/api/strategies").get_json() == []


def test_strategy_api_rejects_non_object_backtest(search_db: Path, strategy_db: Path) -> None:
    app = create_app(search_db, strategy_db)
    app.config.update(TESTING=True)
    client = app.test_client()

    response = client.post(
        "/api/strategies",
        json={"name": "不正", "conditions": {"bet": BET}, "backtest": "not-an-object"},
    )

    assert response.status_code == 400
    assert "JSONオブジェクトまたはnull" in response.get_json()["error"]
    assert client.get("/api/strategies").get_json() == []


def test_cli_matches_all_and_one_strategy(search_db: Path, strategy_db: Path) -> None:
    strategy_id = save_strategy("CLI手法", {"venue": 14, "bet": BET})
    environment = os.environ.copy()
    environment.update(
        KACHISUJI_DB=str(search_db),
        KACHISUJI_STRATEGY_DB=str(strategy_db),
        PYTHONIOENCODING="utf-8",
    )
    command = [sys.executable, "scripts/match_strategies.py", "--date", "2026-08-16"]

    all_result = subprocess.run(
        command,
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    one_result = subprocess.run(
        [*command, "--id", str(strategy_id)],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )

    assert json.loads(all_result.stdout)[0]["strategy_id"] == strategy_id
    assert json.loads(one_result.stdout)["strategy_id"] == strategy_id
