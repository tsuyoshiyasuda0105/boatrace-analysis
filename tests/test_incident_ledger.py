import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.db.connection import _TABLE_PRIMARY_KEYS
from src.notifications import incident_ledger


JST = ZoneInfo("Asia/Tokyo")
SCHEMA_SQL = Path("src/db/schema.sql").read_text(encoding="utf-8")


@pytest.fixture
def ledger_db(tmp_path, monkeypatch):
    db_path = tmp_path / "ledger.db"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(SCHEMA_SQL)
    monkeypatch.setattr(
        incident_ledger,
        "db_connect",
        lambda: sqlite3.connect(db_path),
    )
    return db_path


def _clock(monkeypatch, *values):
    moments = iter(datetime.fromisoformat(value).replace(tzinfo=JST) for value in values)
    monkeypatch.setattr(incident_ledger, "_now_jst", lambda: next(moments))


def test_record_aggregates_active_family_and_reopens_after_resolution(ledger_db, monkeypatch):
    _clock(
        monkeypatch,
        "2026-08-16T10:00:00",
        "2026-08-16T10:01:00",
        "2026-08-16T10:02:00",
        "2026-08-16T10:03:00",
    )
    first = incident_ledger.record_incident(
        category="cron_failure",
        source="daily-job",
        title="failed 12 rows",
        dedup_key="cron_failure|daily-job",
        notified=False,
    )
    repeated = incident_ledger.record_incident(
        category="cron_failure",
        source="daily-job",
        title="failed 27 rows",
        dedup_key="cron_failure|daily-job",
        notified=True,
    )

    assert first == repeated
    rows = incident_ledger.list_incidents(limit=10)
    assert len(rows) == 1
    assert rows[0]["occurrence_count"] == 2
    assert rows[0]["last_seen_at"].startswith("2026-08-16T10:01:00")
    assert rows[0]["notified"] == 1

    assert incident_ledger.resolve_incident(
        first,
        handled_by="rin",
        response_note="原因を修正して再実行",
    ) is True
    resolved = incident_ledger.list_incidents(status="resolved")[0]
    assert resolved["handled_by"] == "rin"
    assert resolved["response_note"] == "原因を修正して再実行"
    assert resolved["resolved_at"].startswith("2026-08-16T10:02:00")

    recurrence = incident_ledger.record_incident(
        category="cron_failure",
        source="daily-job",
        title="failed 3 rows",
        dedup_key="cron_failure|daily-job",
    )
    assert recurrence != first
    assert len(incident_ledger.list_incidents(limit=10)) == 2
    assert incident_ledger.list_incidents(status="open")[0]["occurrence_count"] == 1


def test_app_name_env_and_explicit_filter_share_one_table(ledger_db, monkeypatch):
    monkeypatch.setenv("BOATRACE_INCIDENT_APP_NAME", "app-a")
    app_a_id = incident_ledger.record_incident(
        category="app_error", source="api", title="timeout 12"
    )
    monkeypatch.setenv("BOATRACE_INCIDENT_APP_NAME", "app-b")
    app_b_id = incident_ledger.record_incident(
        category="app_error", source="api", title="timeout 99"
    )

    assert app_a_id and app_b_id and app_a_id != app_b_id
    assert [row["app_name"] for row in incident_ledger.list_incidents()] == ["app-b"]
    assert [row["app_name"] for row in incident_ledger.list_incidents(app_name="app-a")] == ["app-a"]


def test_default_key_normalizes_variable_numbers_and_stats(ledger_db):
    first = incident_ledger.record_incident(
        category="app_error",
        source="src.db.connection",
        title="pool failed 12 stats={'waiting': 5}",
    )
    second = incident_ledger.record_incident(
        category="app_error",
        source="src.db.connection",
        title="pool failed 27 stats={'waiting': 99}",
    )
    assert first == second
    assert incident_ledger.list_incidents()[0]["occurrence_count"] == 2


def test_all_public_helpers_absorb_database_failure(monkeypatch):
    def broken_connect():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(incident_ledger, "db_connect", broken_connect)
    assert incident_ledger.record_incident(category="app_error", source="api", title="boom") is None
    assert incident_ledger.resolve_incident("missing", handled_by="rin", response_note="none") is False
    assert incident_ledger.list_incidents() == []


def test_schema_and_postgres_shim_register_incident_primary_key():
    assert _TABLE_PRIMARY_KEYS["incident_log"] == ["incident_id"]
    assert "CREATE TABLE IF NOT EXISTS incident_log" in SCHEMA_SQL
    assert "idx_incident_log_active_dedup" in SCHEMA_SQL
