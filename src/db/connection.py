"""
DB 接続ヘルパー (SQLite + PostgreSQL 両対応)

ローカル開発: SQLite (config.DB_PATH)
Render 本番:  PostgreSQL (Supabase 等、env DATABASE_URL)

接続選択ロジック:
  - 環境変数 DATABASE_URL が postgres:// or postgresql:// で始まる → psycopg を使用
  - それ以外 (未設定 or sqlite path) → sqlite3 を使用

使う側は `connect()` の戻り値の execute / executemany / commit / close
を sqlite3 互換の感覚で使える (psycopg3 でも同等のメソッドが揃っている)。

Postgres 専用処理:
  - PRAGMA は SQLite 専用なので psycopg では skip
  - 自動コミットは ON (sqlite3 と同じ挙動)
"""
from __future__ import annotations

import os
import re
import sqlite3
from typing import Optional, Union

import config


def _is_postgres_url(url: str) -> bool:
    return url.startswith(("postgres://", "postgresql://"))


def _normalize_pg_url(url: str) -> str:
    """psycopg3 は postgres:// を拒否するので postgresql:// に正規化。
    Supabase が pooled connection で sslmode を求めるため、無ければ追加。"""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if "sslmode=" not in url:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}sslmode=require"
    return url


def _placeholder_pg(sql: str) -> str:
    """SQLite の `?` プレースホルダを Postgres の `%s` に変換。
    クォート内の '?' は触らない (素朴な実装)。"""
    out = []
    in_str = False
    quote = ""
    for ch in sql:
        if not in_str and ch in ("'", '"'):
            in_str = True
            quote = ch
            out.append(ch)
        elif in_str and ch == quote:
            in_str = False
            out.append(ch)
        elif not in_str and ch == "?":
            out.append("%s")
        else:
            out.append(ch)
    return "".join(out)


# schema.sql で定義された各テーブルの主キー (UPSERT 変換用)
_TABLE_PRIMARY_KEYS = {
    "stadiums": ["stadium_number"],
    "racers": ["racer_number"],
    "racer_period_stats": ["racer_number", "period_year", "period_half"],
    "races": ["race_id"],
    "race_entries": ["race_id", "boat_number"],
    "race_previews": ["race_id", "boat_number"],
    "race_parts": ["race_id", "boat_number", "part_code"],
    "race_results": ["race_id", "boat_number"],
    "race_payouts": ["race_id", "bet_type", "combination"],
    "odds_trifecta": ["race_id", "combination", "recorded_at"],
    "predictions": ["race_id", "boat_number", "model_version"],
    "value_bets": ["race_id", "bet_type", "combination", "model_version"],
    "l4_daily_stats_cache": ["race_date"],
}


def _build_upsert(table: str, columns: list[str]) -> str:
    """ON CONFLICT (pk) DO UPDATE SET col=EXCLUDED.col の SQL 末尾を生成。"""
    pk = _TABLE_PRIMARY_KEYS.get(table)
    if not pk:
        # 主キーが不明なテーブルは ON CONFLICT DO NOTHING (重複は無視)
        return " ON CONFLICT DO NOTHING"
    non_pk = [c for c in columns if c not in pk]
    if not non_pk:
        # 全列が主キー → DO NOTHING
        return f" ON CONFLICT ({', '.join(pk)}) DO NOTHING"
    set_clause = ", ".join(f"{c}=EXCLUDED.{c}" for c in non_pk)
    return f" ON CONFLICT ({', '.join(pk)}) DO UPDATE SET {set_clause}"


_INSERT_PATTERN = re.compile(
    r"\bINSERT\s+OR\s+(REPLACE|IGNORE)\s+INTO\s+(\w+)\s*\(([^)]+)\)",
    re.IGNORECASE | re.DOTALL,
)


def _rewrite_sqlite_specific(sql: str) -> str:
    """SQLite 固有の構文を Postgres 互換に書き換え。
    単一文 (1つの INSERT 文) を想定。

    - INSERT OR REPLACE INTO t (cols) ... → INSERT INTO + ON CONFLICT (pk) DO UPDATE SET
    - INSERT OR IGNORE INTO t (cols) ...  → INSERT INTO + ON CONFLICT DO NOTHING
    """
    m = _INSERT_PATTERN.search(sql)
    if not m:
        return sql
    kind = m.group(1).upper()
    table = m.group(2)
    cols_raw = m.group(3)
    cols = [c.strip() for c in cols_raw.split(",") if c.strip()]
    head = f"INSERT INTO {table} ({cols_raw})"
    if kind == "IGNORE":
        tail = " ON CONFLICT DO NOTHING"
    else:
        tail = _build_upsert(table, cols)
    rewritten = sql[:m.start()] + head + sql[m.end():]
    # 末尾セミコロンの前に ON CONFLICT を挿入
    rewritten = rewritten.rstrip()
    if rewritten.endswith(";"):
        rewritten = rewritten[:-1].rstrip() + tail + ";"
    else:
        rewritten = rewritten + tail
    return rewritten


class _PgConnection:
    """psycopg3 connection を sqlite3 風に薄くラップ。
    `execute(sql, params)` で `?` を `%s` に変換しつつ ON CONFLICT を補完。"""

    def __init__(self, dsn: str):
        import psycopg
        self._conn = psycopg.connect(dsn, autocommit=True)
        self._kind = "postgres"
        # Supabase Free (Nano) の tmp 領域不足対策:
        # 並列ワーカー無効化 + work_mem 増 (メモリ内処理でtmp書出を減らす)
        try:
            cur = self._conn.cursor()
            cur.execute("SET max_parallel_workers_per_gather = 0")
            cur.execute("SET work_mem = '64MB'")
            cur.execute("SET enable_hashjoin = on")
            cur.execute("SET enable_mergejoin = off")
            cur.close()
        except Exception:
            pass

    def execute(self, sql: str, params: Optional[tuple] = None):
        sql2 = _placeholder_pg(_rewrite_sqlite_specific(sql))
        cur = self._conn.cursor()
        cur.execute(sql2, params or ())
        return cur

    def executemany(self, sql: str, seq):
        sql2 = _placeholder_pg(_rewrite_sqlite_specific(sql))
        cur = self._conn.cursor()
        cur.executemany(sql2, list(seq))
        return cur

    def executescript(self, script: str):
        # psycopg3 は単一 execute() で複文を受け付けないため、文ごとに分割して実行
        # まず行コメント (--) を除去してから ; で分割し、各文に書き換えを適用
        cleaned = "\n".join(
            line for line in script.splitlines()
            if not line.lstrip().startswith("--")
        )
        cur = self._conn.cursor()
        for stmt in cleaned.split(";"):
            stmt = stmt.strip()
            if stmt:
                cur.execute(_rewrite_sqlite_specific(stmt))
        return cur

    def cursor(self):
        return self._conn.cursor()

    def commit(self):
        # autocommit なので no-op
        pass

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


def connect(db_path: Optional[str] = None) -> Union[sqlite3.Connection, "_PgConnection"]:
    """
    プロジェクト共通の DB 接続を返す。

    SQLite (デフォルト):
      - journal_mode=WAL: 読み書き同時を許可
      - busy_timeout: 他プロセスのロック解放まで待機
      - foreign_keys=ON: FK 制約を有効化

    PostgreSQL (DATABASE_URL 設定時):
      - psycopg3 で接続
      - autocommit=True
      - SQLite 構文を最低限書き換えて execute
    """
    db_url = os.getenv("DATABASE_URL", "").strip()
    if db_url and _is_postgres_url(db_url):
        return _PgConnection(_normalize_pg_url(db_url))

    # 本番 (Render) で DATABASE_URL 空はサイレント SQLite フォールバックで
    # 壊滅的バグになる (空 DB で起動する)。明示的に失敗させる。
    if os.getenv("RENDER", "").strip():
        raise RuntimeError(
            "DATABASE_URL is empty in RENDER environment. "
            "Set DATABASE_URL to the Supabase Postgres URL. "
            "Refusing to silently fall back to SQLite in production."
        )

    # SQLite path (ローカル開発時のみ)
    path = db_path or config.DB_PATH
    conn = sqlite3.connect(path, timeout=config.SQLITE_CONNECT_TIMEOUT_SECONDS)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute(f"PRAGMA busy_timeout={config.SQLITE_BUSY_TIMEOUT_MS};")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn
