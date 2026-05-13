"""Supabase (or local SQLite) に alert_subscribers / alert_sent テーブルが
存在するか確認し、無ければ作成する診断スクリプト。

実行:
  .venv\\Scripts\\python.exe scripts\\ensure_alert_tables.py

DATABASE_URL が .env に設定されていれば Supabase に作成、
未設定ならローカル SQLite (data/boatrace.db) に作成。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

# .env 読み込み
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
except ImportError:
    pass

from src.db.connection import connect

# Postgres 用 DDL (SQLite の TEXT/INTEGER/REAL を素直に対応する型に)
PG_DDL = """
CREATE TABLE IF NOT EXISTS alert_subscribers (
  email_hash              TEXT PRIMARY KEY,
  email_encrypted         TEXT NOT NULL,
  alert_types             TEXT NOT NULL DEFAULT '["L4_SG","L4_G1","L4_G2"]',
  min_recovery_rate       DOUBLE PRECISION NOT NULL DEFAULT 150.0,
  is_active               INTEGER NOT NULL DEFAULT 1,
  is_verified             INTEGER NOT NULL DEFAULT 0,
  verification_token      TEXT,
  verification_expires_at TEXT,
  unsubscribe_token       TEXT,
  created_at              TEXT NOT NULL,
  last_notified_at        TEXT,
  notify_count            INTEGER NOT NULL DEFAULT 0,
  ip_at_signup            TEXT
);
CREATE INDEX IF NOT EXISTS idx_alert_sub_active ON alert_subscribers(is_active, is_verified);

CREATE TABLE IF NOT EXISTS alert_sent (
  email_hash  TEXT NOT NULL,
  race_id     TEXT NOT NULL,
  alert_type  TEXT NOT NULL,
  sent_at     TEXT NOT NULL,
  PRIMARY KEY (email_hash, race_id, alert_type)
);
CREATE INDEX IF NOT EXISTS idx_alert_sent_at ON alert_sent(sent_at);
""".strip()

SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS alert_subscribers (
  email_hash         TEXT PRIMARY KEY,
  email_encrypted    TEXT NOT NULL,
  alert_types        TEXT NOT NULL DEFAULT '["L4_SG","L4_G1","L4_G2"]',
  min_recovery_rate  REAL NOT NULL DEFAULT 150.0,
  is_active          INTEGER NOT NULL DEFAULT 1,
  is_verified        INTEGER NOT NULL DEFAULT 0,
  verification_token TEXT,
  verification_expires_at TEXT,
  unsubscribe_token  TEXT,
  created_at         TEXT NOT NULL,
  last_notified_at   TEXT,
  notify_count       INTEGER NOT NULL DEFAULT 0,
  ip_at_signup       TEXT
);
CREATE INDEX IF NOT EXISTS idx_alert_sub_active ON alert_subscribers(is_active, is_verified);

CREATE TABLE IF NOT EXISTS alert_sent (
  email_hash  TEXT NOT NULL,
  race_id     TEXT NOT NULL,
  alert_type  TEXT NOT NULL,
  sent_at     TEXT NOT NULL,
  PRIMARY KEY (email_hash, race_id, alert_type)
);
CREATE INDEX IF NOT EXISTS idx_alert_sent_at ON alert_sent(sent_at);
""".strip()


def main():
    db_url = os.environ.get("DATABASE_URL", "").strip()
    is_pg = db_url.startswith(("postgres://", "postgresql://"))
    backend = "PostgreSQL (Supabase)" if is_pg else "SQLite (local)"
    print(f"=== alert tables ensure ===")
    print(f"Backend: {backend}")
    if is_pg:
        # マスク表示
        try:
            masked = db_url.split("@")[1] if "@" in db_url else "***"
            print(f"DSN: ***@{masked}")
        except Exception:
            print("DSN: (parse failed)")
    print()

    conn = connect()

    # 既存テーブル確認
    if is_pg:
        cur = conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name IN ('alert_subscribers','alert_sent')"
        )
        existing = {row[0] for row in cur.fetchall()}
    else:
        cur = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name IN ('alert_subscribers','alert_sent')"
        )
        existing = {row[0] for row in cur.fetchall()}

    print(f"既存テーブル: {sorted(existing) if existing else 'なし'}")

    # 不足分を作成
    if {"alert_subscribers", "alert_sent"} - existing:
        print()
        print("テーブル作成中...")
        ddl = PG_DDL if is_pg else SQLITE_DDL
        # 文ごとに実行
        for stmt in ddl.split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(stmt)
        if not is_pg:
            conn.commit()
        print("✅ 作成完了")
    else:
        print("✅ 両テーブルとも既に存在 (作成不要)")

    # 行数確認
    print()
    n_sub = conn.execute("SELECT COUNT(*) FROM alert_subscribers").fetchone()[0]
    n_sent = conn.execute("SELECT COUNT(*) FROM alert_sent").fetchone()[0]
    print(f"alert_subscribers: {n_sub} 行")
    print(f"alert_sent:        {n_sent} 行")

    conn.close()
    print()
    print("✅ done")


if __name__ == "__main__":
    main()
