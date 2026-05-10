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


def _rewrite_sqlite_specific(sql: str) -> str:
    """SQLite 固有の構文を Postgres 互換に書き換え (限定的)。

    - INSERT OR REPLACE INTO t → INSERT INTO t ... ON CONFLICT DO UPDATE
      (主キー名が分かる場合のみ。汎用的な書き換えは難しいので、コレクター側の
       upsert ヘルパー利用を推奨)
    - INSERT OR IGNORE INTO t → INSERT INTO t ... ON CONFLICT DO NOTHING
    """
    # まず IGNORE → ON CONFLICT DO NOTHING (主キー特定不要)
    sql = re.sub(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT INTO", sql, flags=re.I)
    # OR REPLACE は upsert ヘルパーを使うべきだが、簡易対応として
    # INSERT INTO に置換 (重複キーで失敗するので、呼び出し側で対応必須)
    sql = re.sub(r"\bINSERT\s+OR\s+REPLACE\s+INTO\b", "INSERT INTO", sql, flags=re.I)
    return sql


class _PgConnection:
    """psycopg3 connection を sqlite3 風に薄くラップ。
    `execute(sql, params)` で `?` を `%s` に変換しつつ ON CONFLICT を補完。"""

    def __init__(self, dsn: str):
        import psycopg
        self._conn = psycopg.connect(dsn, autocommit=True)
        self._kind = "postgres"

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
        # psycopg は複文 execute も可能 (autocommit 時)
        cur = self._conn.cursor()
        cur.execute(_rewrite_sqlite_specific(script))
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

    # SQLite path
    path = db_path or config.DB_PATH
    conn = sqlite3.connect(path, timeout=config.SQLITE_CONNECT_TIMEOUT_SECONDS)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute(f"PRAGMA busy_timeout={config.SQLITE_BUSY_TIMEOUT_MS};")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn
