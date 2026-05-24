"""タスク実行ログ (task_runs) — ローカル SQLite 専用ヘルパー。

サーバー (データ収集を回しているローカル Windows PC) がスケジュール時刻に
ダウンしていてタスクが実行されなかった場合、起動時キャッチアップ
(scripts/startup_catchup.py) が「今日そのタスクが成功したか」を判定する
ための実行記録を管理する。

設計上の要点:
  - 記録先は **必ずローカル SQLite** (config.DB_PATH)。
    キャッチアップ判定はローカルPCの状態に基づくべきなので、DATABASE_URL
    (Supabase) の有無に関係なく sqlite3 で直接書く。
  - task_runs テーブルは存在しなければ自動生成する (マイグレーション不要)。
  - 失敗してもタスク本体を壊さないこと。例外は呼び出し側 (record_task_run.py)
    で握りつぶす想定だが、本モジュールも極力安全に振る舞う。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Optional

import config

_DDL = """
CREATE TABLE IF NOT EXISTS task_runs (
  task_name   TEXT NOT NULL,
  run_date    TEXT NOT NULL,
  status      TEXT NOT NULL,
  run_count   INTEGER NOT NULL DEFAULT 0,
  started_at  TEXT,
  finished_at TEXT,
  success_at  TEXT,
  trigger     TEXT,
  detail      TEXT,
  PRIMARY KEY (task_name, run_date)
);
"""

_COLUMNS = ["task_name", "run_date", "status", "run_count",
            "started_at", "finished_at", "success_at", "trigger", "detail"]


def _conn() -> sqlite3.Connection:
    """ローカル SQLite 接続を返す (DATABASE_URL を無視)。テーブルは自動生成。"""
    conn = sqlite3.connect(config.DB_PATH, timeout=config.SQLITE_CONNECT_TIMEOUT_SECONDS)
    conn.execute(f"PRAGMA busy_timeout={config.SQLITE_BUSY_TIMEOUT_MS};")
    conn.execute(_DDL)
    return conn


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def record(task_name: str, status: str, *,
           trigger: str = "scheduled",
           detail: Optional[str] = None,
           run_date: Optional[str] = None) -> None:
    """タスク実行を記録 (1日1行に集約)。

    - 同一 (task_name, run_date) があれば run_count を +1 し status/finished_at 更新。
    - status=='success' のときだけ success_at を更新 (失敗実行で成功記録を消さない)。
    """
    now = datetime.now()
    rd = run_date or now.strftime("%Y-%m-%d")
    now_iso = now.isoformat(timespec="seconds")
    success_at = now_iso if status == "success" else None

    conn = _conn()
    try:
        cur = conn.execute(
            "SELECT run_count FROM task_runs WHERE task_name=? AND run_date=?",
            (task_name, rd),
        )
        if cur.fetchone():
            conn.execute(
                "UPDATE task_runs SET status=?, run_count=run_count+1, finished_at=?, "
                "success_at=COALESCE(?, success_at), trigger=?, detail=? "
                "WHERE task_name=? AND run_date=?",
                (status, now_iso, success_at, trigger, detail, task_name, rd),
            )
        else:
            conn.execute(
                "INSERT INTO task_runs "
                "(task_name, run_date, status, run_count, started_at, finished_at, success_at, trigger, detail) "
                "VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?)",
                (task_name, rd, status, now_iso, now_iso, success_at, trigger, detail),
            )
        conn.commit()
    finally:
        conn.close()


def last_success_at(task_name: str, run_date: Optional[str] = None) -> Optional[datetime]:
    """指定日 (デフォルト今日) の最後の成功時刻。未成功なら None。"""
    rd = run_date or _today()
    conn = _conn()
    try:
        cur = conn.execute(
            "SELECT success_at FROM task_runs WHERE task_name=? AND run_date=?",
            (task_name, rd),
        )
        row = cur.fetchone()
    finally:
        conn.close()
    if row and row[0]:
        try:
            return datetime.fromisoformat(row[0])
        except ValueError:
            return None
    return None


def get_today(task_name: str) -> Optional[dict]:
    """今日の task_runs 行を dict で返す (無ければ None)。"""
    rd = _today()
    conn = _conn()
    try:
        cur = conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM task_runs WHERE task_name=? AND run_date=?",
            (task_name, rd),
        )
        row = cur.fetchone()
    finally:
        conn.close()
    return dict(zip(_COLUMNS, row)) if row else None
