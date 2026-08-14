"""P0-2 タスク5: 多重実行スキップが success として偽装記録されないことのテスト。"""
import sqlite3
import sys

import pytest

from scripts import refresh_race_detail_after_exhibition as refresh_mod


TASK = "render_exhibition_detail_refresh"
RUN_DATE = "2026-08-14"


@pytest.fixture
def task_db(tmp_path, monkeypatch):
    db_path = tmp_path / "tasks.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE task_runs (
              task_name TEXT NOT NULL,
              run_date TEXT NOT NULL,
              status TEXT NOT NULL,
              run_count INTEGER NOT NULL DEFAULT 0,
              started_at TEXT,
              finished_at TEXT,
              success_at TEXT,
              trigger TEXT,
              detail TEXT,
              PRIMARY KEY (task_name, run_date)
            )
            """
        )
    monkeypatch.setattr(
        refresh_mod, "db_connect", lambda *a, **k: sqlite3.connect(db_path)
    )
    return db_path


def _row(db_path):
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            "SELECT status, success_at FROM task_runs WHERE task_name=? AND run_date=?",
            (TASK, RUN_DATE),
        ).fetchone()


def test_skip_records_skipped_without_success(task_db):
    refresh_mod._record_cron_skip(TASK, RUN_DATE, detail='{"skipped": true}')
    status, success_at = _row(task_db)
    assert status == "skipped"
    assert success_at is None


def test_skip_preserves_live_running_row(task_db):
    # 並行実行中の running 行を上書きすると多重実行検知が壊れるため保持する
    with sqlite3.connect(task_db) as conn:
        conn.execute(
            "INSERT INTO task_runs (task_name, run_date, status, run_count, started_at) "
            "VALUES (?, ?, 'running', 1, '2026-08-14T10:00:00')",
            (TASK, RUN_DATE),
        )
    refresh_mod._record_cron_skip(TASK, RUN_DATE)
    status, _ = _row(task_db)
    assert status == "running"


def test_skip_keeps_earlier_success_timestamp(task_db):
    with sqlite3.connect(task_db) as conn:
        conn.execute(
            "INSERT INTO task_runs (task_name, run_date, status, run_count, success_at) "
            "VALUES (?, ?, 'success', 1, '2026-08-14T09:00:00')",
            (TASK, RUN_DATE),
        )
    refresh_mod._record_cron_skip(TASK, RUN_DATE)
    status, success_at = _row(task_db)
    assert status == "skipped"
    assert success_at == "2026-08-14T09:00:00"


def test_main_skip_path_does_not_record_success(task_db, monkeypatch):
    cron_calls = []
    skip_calls = []
    monkeypatch.setattr(refresh_mod, "log_deploy_revision", lambda *_a, **_k: None)
    monkeypatch.setattr(refresh_mod, "_ensure_task_runs_table", lambda: None)
    monkeypatch.setattr(
        refresh_mod,
        "_exhibition_refresh_recently_running",
        lambda _date, _now: (True, "recent-running:test"),
    )
    monkeypatch.setattr(
        refresh_mod,
        "record_cron_run",
        lambda *args, **kwargs: cron_calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        refresh_mod,
        "_record_cron_skip",
        lambda *args, **kwargs: skip_calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        sys, "argv", ["refresh_race_detail_after_exhibition.py", "--date", RUN_DATE, "--skip-collect"]
    )

    assert refresh_mod.main() == 0
    # 偽装 success (record_cron_run(..., "success")) は書かれない
    assert cron_calls == []
    # skipped として記録される
    assert len(skip_calls) == 1
    assert skip_calls[0][0][:2] == (TASK, RUN_DATE)
