from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile

import pytest

from scripts import crosscheck_roi_strategies as crosscheck
from src.features.asof_builder import create_output_schema


def _insert_v4(path: Path, race_id: str, race_date: str, **overrides: object) -> None:
    row: dict[str, object] = {
        "race_id": race_id,
        "race_date": race_date,
        "asof_date": race_date,
        "built_at": "2026-08-16T00:00:00+00:00",
        "schema_version": 4,
        "jcd": 20,
        "race_no": 9,
        "wind_speed": 2.0,
        "b1_motor_rate2": 40.0,
        "b2_avg_st": 0.18,
        "b3_motor_rate2": 42.0,
        "result_tansho_json": json.dumps(["1"]),
        "payout_tansho_json": json.dumps({"1": 150}),
        "result_nirentan_json": json.dumps(["1-3"]),
        "payout_nirentan_json": json.dumps({"1-3": 420}),
        "result_sanrentan_json": json.dumps(["1-2-3"]),
        "payout_sanrentan_json": json.dumps({"1-2-3": 900}),
    }
    row.update(overrides)
    with sqlite3.connect(path) as conn:
        create_output_schema(conn)
        columns = list(row)
        conn.execute(
            f"INSERT INTO asof_race_features ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
            [row[column] for column in columns],
        )


def test_capability_catalog_is_complete_and_disjoint() -> None:
    all_keys = set(crosscheck.EXACT_SPECS) | crosscheck.B_KEYS | set(crosscheck.C_REASONS)

    assert len(all_keys) == 94
    assert set(crosscheck.EXACT_SPECS).isdisjoint(crosscheck.B_KEYS)
    assert set(crosscheck.EXACT_SPECS).isdisjoint(crosscheck.C_REASONS)
    assert crosscheck.B_KEYS.isdisjoint(crosscheck.C_REASONS)
    assert {crosscheck.capability_for(key)[0] for key in all_keys} == {"A", "B", "C"}


def test_readonly_connection_enforces_mode_ro_and_query_only(tmp_path: Path) -> None:
    db_path = tmp_path / "readonly.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE sample(value INTEGER)")

    with crosscheck.readonly_connection(db_path) as conn:
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("INSERT INTO sample VALUES (1)")

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sample").fetchone()[0] == 0


def test_search_results_attaches_ids_without_changing_public_aggregate(tmp_path: Path) -> None:
    db_path = tmp_path / "search.sqlite3"
    _insert_v4(db_path, "match", "2026-07-01")
    _insert_v4(db_path, "miss", "2026-07-02", result_nirentan_json=json.dumps(["1-2"]), payout_nirentan_json=json.dumps({"1-2": 250}))
    _insert_v4(db_path, "filtered", "2026-07-03", b3_motor_rate2=39.9)
    conditions = {
        **crosscheck.EXACT_SPECS["wakamatsu_13_weak2_strong3_exa"].conditions,
        "date_from": "2026-07-01",
        "date_to": "2026-07-31",
    }

    result = crosscheck.search_results(db_path, conditions)

    assert result["race_ids"] == ["match", "miss"]
    assert result["n"] == 2
    assert result["hits"] == 1
    assert result["roi"] == 210.0


def test_legacy_aggregate_uses_bet_unit_for_recovery() -> None:
    result = crosscheck._legacy_aggregate(
        [
            {"key": "x", "race_id": "r1", "hit": True, "pay": 500},
            {"key": "x", "race_id": "r2", "hit": False, "pay": 0},
            {"key": "other", "race_id": "r3", "hit": True, "pay": 999},
        ],
        "x",
        200,
    )

    assert (result["n"], result["hits"], result["roi"]) == (2, 1, 125.0)
    assert result["race_ids"] == ["r1", "r2"]


def test_legacy_dump_is_removed_when_imported_evaluator_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    dump_path = Path(tempfile.gettempdir()) / "kachisuji_step10_legacy_signals.jsonl"
    if dump_path.exists():
        dump_path.unlink()

    def failing(*_args, **_kwargs):
        dump_path.write_text("partial", encoding="utf-8")
        raise RuntimeError("boom")

    monkeypatch.setattr(crosscheck, "_legacy_runtime", lambda _path: ([], {}, failing))

    with pytest.raises(RuntimeError, match="boom"):
        crosscheck.legacy_results("unused", "2026-07-01", "2026-07-31")
    assert not dump_path.exists()


def test_cross_strategy_selection_is_diagnostic_not_an_unproven_cause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = "wakamatsu_13_weak2_strong3_exa"
    registry = [{"key": key, "label": "若松"}]
    signals = [{"key": "a1_ace_motor_123_corr_tri", "race_id": "r1", "date": "2026-07-01", "hit": False, "pay": 0}]
    monkeypatch.setattr(crosscheck, "legacy_results", lambda *_args: (registry, {}, signals))
    monkeypatch.setattr(
        crosscheck,
        "search_results",
        lambda *_args: {"n": 1, "hits": 0, "roi": 0.0, "races": [], "race_ids": ["r1"]},
    )

    result = crosscheck.run_crosscheck("legacy", "search", "2026-07-01", "2026-07-31", [key])
    row = result["results"][0]

    assert row["verdict"] == "不一致"
    assert row["causes"] == ["data-source"]
    assert row["dedup_examples"] == [{"race_id": "r1", "selected_legacy_key": "a1_ace_motor_123_corr_tri"}]


def test_equal_aggregates_with_different_race_ids_are_not_reported_as_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = "a1_ace_motor_123_corr_tri"
    registry = [{"key": key, "label": "A1"}]
    signals = [{"key": key, "race_id": "legacy-race", "date": "2026-07-01", "hit": False, "pay": 0}]
    monkeypatch.setattr(crosscheck, "legacy_results", lambda *_args: (registry, {}, signals))
    monkeypatch.setattr(
        crosscheck,
        "search_results",
        lambda *_args: {"n": 1, "hits": 0, "roi": 0.0, "races": [], "race_ids": ["search-race"]},
    )

    row = crosscheck.run_crosscheck(
        "legacy", "search", "2026-07-01", "2026-07-31", [key]
    )["results"][0]

    assert row["verdict"] == "不一致"
    assert row["causes"] == ["condition-gap"]
    assert row["only_legacy"] == ["legacy-race"]
    assert row["only_search"] == ["search-race"]
