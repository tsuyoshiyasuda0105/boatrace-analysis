"""P0-2 タスク1: 戦略評価の失敗カウンタの回帰テスト。

_safe_signal_eval (実体: _signal_eval_guard) が
  (a) 例外時に None を返す (従来動作維持)
  (b) プロセス内カウンタ・system_status に記録する
  (c) 同日同戦略は system_status に重複記録しない
ことを検証する。
"""
import sqlite3

import pytest

from src.web import app as app_module


TARGET_DATE = "2026-08-14"


@pytest.fixture
def status_db(tmp_path, monkeypatch):
    db_path = tmp_path / "status.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE system_status (
              check_name TEXT NOT NULL,
              check_date TEXT NOT NULL,
              status TEXT NOT NULL,
              message TEXT,
              detail_json TEXT,
              checked_at TEXT NOT NULL,
              PRIMARY KEY (check_name, check_date)
            )
            """
        )
    monkeypatch.setattr(app_module, "db_connect", lambda *a, **k: sqlite3.connect(db_path))
    app_module._SIGNAL_EVAL_FAILURES.clear()
    app_module._SIGNAL_EVAL_STATUS_MARKED.clear()
    yield db_path
    app_module._SIGNAL_EVAL_FAILURES.clear()
    app_module._SIGNAL_EVAL_STATUS_MARKED.clear()


def _load_status_row(db_path):
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            "SELECT status, message, detail_json FROM system_status "
            "WHERE check_name=? AND check_date=?",
            (app_module.SIGNAL_EVAL_FAILURE_CHECK, TARGET_DATE),
        ).fetchone()


def _boom():
    raise ValueError("boom")


def test_guard_passes_through_success(status_db):
    assert app_module._signal_eval_guard("ok_strategy", lambda: 42, TARGET_DATE) == 42
    assert app_module._SIGNAL_EVAL_FAILURES == {}
    assert _load_status_row(status_db) is None


def test_failure_returns_none_and_records(status_db):
    result = app_module._signal_eval_guard("teststrat", _boom, TARGET_DATE)

    # (a) 従来動作: None を返す
    assert result is None
    # (b) プロセス内カウンタ
    entry = app_module._SIGNAL_EVAL_FAILURES["teststrat"]
    assert entry["count"] == 1
    assert "ValueError" in entry["last_error"]
    assert entry["last_date"] == TARGET_DATE
    # (b) system_status への warning 記録
    row = _load_status_row(status_db)
    assert row is not None
    assert row[0] == "warning"
    assert "teststrat" in row[1]
    assert "ValueError" in row[1]
    assert '"teststrat"' in row[2]


def test_same_day_same_strategy_recorded_once(status_db):
    app_module._signal_eval_guard("teststrat", _boom, TARGET_DATE)
    first_row = _load_status_row(status_db)
    app_module._signal_eval_guard("teststrat", _boom, TARGET_DATE)

    # カウンタは 2 に増える
    assert app_module._SIGNAL_EVAL_FAILURES["teststrat"]["count"] == 2
    # (c) system_status は同日同戦略で重複記録されない (行内容が変わらない)
    second_row = _load_status_row(status_db)
    assert second_row == first_row
    assert '"count": 1' in second_row[2]


def test_second_strategy_same_day_merges_into_same_row(status_db):
    app_module._signal_eval_guard("strat_a", _boom, TARGET_DATE)
    app_module._signal_eval_guard("strat_b", _boom, TARGET_DATE)

    row = _load_status_row(status_db)
    assert '"strat_a"' in row[2]
    assert '"strat_b"' in row[2]
    with sqlite3.connect(status_db) as conn:
        n_rows = conn.execute(
            "SELECT COUNT(*) FROM system_status WHERE check_name=?",
            (app_module.SIGNAL_EVAL_FAILURE_CHECK,),
        ).fetchone()[0]
    assert n_rows == 1


def test_status_db_error_does_not_break_guard(status_db, monkeypatch):
    def _broken_connect(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr(app_module, "db_connect", _broken_connect)
    assert app_module._signal_eval_guard("teststrat", _boom, TARGET_DATE) is None
    assert app_module._SIGNAL_EVAL_FAILURES["teststrat"]["count"] == 1


def test_admin_rows_merge_status_and_process_counters(status_db):
    app_module._signal_eval_guard("teststrat", _boom, TARGET_DATE)
    app_module._signal_eval_guard("teststrat", _boom, TARGET_DATE)

    check_row = {
        "detail_json": {
            "strategies": {
                "teststrat": {"count": 1, "last_error": "ValueError: boom", "last_at": "x"},
                "other_process_strat": {"count": 1, "last_error": "KeyError: 'y'", "last_at": "y"},
            }
        }
    }
    rows = app_module._signal_eval_failure_rows(check_row, TARGET_DATE)
    by_name = {row["name"]: row for row in rows}
    # プロセス内カウンタの 2 回が優先される
    assert by_name["teststrat"]["count"] == 2
    # 別プロセス由来の記録も表示される
    assert by_name["other_process_strat"]["count"] == 1
    # ゼロ件なら空 (テンプレート側は「なし」を表示)
    assert app_module._signal_eval_failure_rows(None, "1999-01-01") == []
