# -*- coding: utf-8 -*-
"""kachisuji デルタの Postgres 経由輸送と slim DB への適用。

step27 の当初設計 (Supabase Storage 経由) は PC・web 双方に SERVICE キーの
配布が必要で、実際には両側とも鍵が配られておらず一度も動かなかった
(2026-08-20 リン診断)。本モジュールは既に両側が持つ DATABASE_URL だけで
成立する輸送路に置き換える:

    PC 夜間 (01:00) -- upload_delta_file --> Postgres kachisuji_delta_files
    web サービス    -- apply_pending_to_slim --> /data の slim DB (ATTACH 適用)

適用ロジックは scripts/apply_kachisuji_deltas.py と同じ不変条件を守る
(スキーマ検証 / INSERT OR IGNORE / applied_deltas 記帳 / 失敗時バックアップ復元)。
web プロセスから scripts/ は import できない (パッケージでない) ため、
必要最小限をここに保持している。

内部トリガー認証: web と cron が共有する既存秘密 DATABASE_URL の SHA-256 を
トークンとして使い、新しい環境変数を増やさない。
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

TABLES = ("asof_race_features", "racers")
TRANSPORT_TABLE = "kachisuji_delta_files"
_DELTA_NAME_OK = __import__("re").compile(r"^\d{8}\.db$")


# ---------------------------------------------------------------- transport --

def _default_conn():
    from src.db.connection import connect

    return connect()


def ensure_transport_table(conn) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TRANSPORT_TABLE} (
            name TEXT PRIMARY KEY,
            payload BYTEA NOT NULL,
            sha256 TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )


def canonical_delta_name(path: Path) -> str:
    """kachisuji_delta_20260819.db → 20260819.db (Storage 版と同じ正規化)。"""
    name = path.name
    if _DELTA_NAME_OK.fullmatch(name):
        return name
    m = __import__("re").fullmatch(r"kachisuji_delta_(\d{8})\.db", name)
    if m:
        return f"{m.group(1)}.db"
    raise ValueError(f"invalid delta filename: {name}")


def upload_delta_file(path: Path, conn=None) -> dict[str, Any]:
    """デルタファイルを Postgres の輸送テーブルへ格納する (冪等)。"""
    path = Path(path)
    payload = path.read_bytes()
    if not payload.startswith(b"SQLite format 3\x00"):
        raise ValueError(f"not a SQLite database: {path}")
    name = canonical_delta_name(path)
    digest = hashlib.sha256(payload).hexdigest()
    own = conn is None
    if own:
        conn = _default_conn()
    try:
        ensure_transport_table(conn)
        conn.execute(
            f"INSERT OR IGNORE INTO {TRANSPORT_TABLE} "
            "(name, payload, sha256, size_bytes, created_at) VALUES (?, ?, ?, ?, ?)",
            (name, payload, digest, len(payload),
             datetime.now(timezone.utc).isoformat()),
        )
        row = conn.execute(
            f"SELECT sha256 FROM {TRANSPORT_TABLE} WHERE name = ?", (name,)
        ).fetchone()
        stored_sha = str(row[0]) if row else None
        if stored_sha != digest:
            # 既存行と中身が違う = 同名で別内容。事故防止のため明示エラー。
            raise ValueError(
                f"delta {name} already stored with different sha256 "
                f"(stored={stored_sha} local={digest})"
            )
        return {"name": name, "sha256": digest, "size_bytes": len(payload)}
    finally:
        if own:
            conn.close()


def fetch_pending_payloads(applied: set[str], conn=None) -> list[tuple[str, bytes]]:
    own = conn is None
    if own:
        conn = _default_conn()
    try:
        ensure_transport_table(conn)
        rows = conn.execute(
            f"SELECT name, payload, sha256 FROM {TRANSPORT_TABLE} ORDER BY name"
        ).fetchall()
    finally:
        if own:
            conn.close()
    result: list[tuple[str, bytes]] = []
    for name, payload, digest in rows:
        name = str(name)
        if name in applied:
            continue
        data = bytes(payload)  # psycopg は memoryview を返す
        if hashlib.sha256(data).hexdigest() != str(digest):
            raise ValueError(f"transport payload corrupted for {name}")
        result.append((name, data))
    return result


def prune_transport(days: int = 14, conn=None) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    own = conn is None
    if own:
        conn = _default_conn()
    try:
        ensure_transport_table(conn)
        cur = conn.execute(
            f"DELETE FROM {TRANSPORT_TABLE} WHERE created_at < ?", (cutoff,)
        )
        return int(getattr(cur, "rowcount", 0) or 0)
    finally:
        if own:
            conn.close()


# ------------------------------------------------------------------- token --

def internal_token() -> str:
    """web/cron が共有する DATABASE_URL から導出する内部トリガートークン。"""
    source = os.environ.get("DATABASE_URL", "").strip()
    if not source:
        raise RuntimeError("DATABASE_URL is required to derive the internal token")
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:40]


# ------------------------------------------------------------------- apply --
# 以下は scripts/apply_kachisuji_deltas.py と同一の不変条件を保つこと。

def _readonly_uri(path: Path) -> str:
    return path.resolve().as_uri() + "?mode=ro"


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def read_applied_names(slim_db: Path) -> set[str]:
    connection = sqlite3.connect(_readonly_uri(slim_db), uri=True)
    try:
        if not _table_exists(connection, "applied_deltas"):
            return set()
        return {
            str(row[0])
            for row in connection.execute("SELECT name FROM applied_deltas")
        }
    finally:
        connection.close()


def _validate_schema(connection: sqlite3.Connection, alias: str) -> None:
    for table in TABLES:
        main_cols = connection.execute(f"PRAGMA main.table_info({table})").fetchall()
        delta_cols = connection.execute(f"PRAGMA {alias}.table_info({table})").fetchall()
        if not main_cols or not delta_cols:
            raise ValueError(f"missing required table: {table}")
        if [r[1] for r in main_cols] != [r[1] for r in delta_cols]:
            raise ValueError(f"schema mismatch for table: {table}")


def _apply_one(connection: sqlite3.Connection, delta_path: Path, name: str) -> tuple[int, int]:
    alias = "delta_src"
    connection.execute(f"ATTACH DATABASE ? AS {alias}", (_readonly_uri(delta_path),))
    try:
        _validate_schema(connection, alias)
        before = {
            t: int(connection.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
            for t in TABLES
        }
        connection.execute("BEGIN IMMEDIATE")
        for t in TABLES:
            connection.execute(f"INSERT OR IGNORE INTO {t} SELECT * FROM {alias}.{t}")
        connection.execute(
            "INSERT OR IGNORE INTO applied_deltas(name, applied_at) VALUES (?, ?)",
            (name, datetime.now(timezone.utc).isoformat()),
        )
        connection.commit()
        after = {
            t: int(connection.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
            for t in TABLES
        }
        return (
            after["asof_race_features"] - before["asof_race_features"],
            after["racers"] - before["racers"],
        )
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute(f"DETACH DATABASE {alias}")


def _slim_summary(slim_db: Path) -> dict[str, Any]:
    connection = sqlite3.connect(_readonly_uri(slim_db), uri=True)
    try:
        row = connection.execute(
            "SELECT MAX(race_date), COUNT(*) FROM asof_race_features"
        ).fetchone()
        return {"latest_race_date": row[0], "asof_rows": int(row[1] or 0)}
    finally:
        connection.close()


def apply_pending_to_slim(slim_db: Path, conn=None) -> dict[str, Any]:
    """輸送テーブルの未適用デルタを slim DB へ適用する。

    失敗時は事前バックアップから slim を復元し、例外を再送出する。
    """
    slim_db = Path(slim_db).resolve()
    if not slim_db.is_file():
        raise FileNotFoundError(f"slim DB not found: {slim_db}")
    applied = read_applied_names(slim_db)
    pending = fetch_pending_payloads(applied, conn=conn)
    if not pending:
        return {"applied_files": 0, "asof_added": 0, "racers_added": 0,
                **_slim_summary(slim_db)}

    backup = Path(str(slim_db) + ".bak")
    shutil.copy2(slim_db, backup)
    connection: sqlite3.Connection | None = None
    tmp_dir = Path(tempfile.mkdtemp(prefix="kachisuji_delta_"))
    try:
        connection = sqlite3.connect(str(slim_db), uri=True, timeout=30.0)
        connection.execute(
            "CREATE TABLE IF NOT EXISTS applied_deltas ("
            "name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        connection.commit()
        asof_total = racers_total = 0
        names: list[str] = []
        for name, payload in pending:
            tmp = tmp_dir / name
            tmp.write_bytes(payload)
            asof_added, racers_added = _apply_one(connection, tmp, name)
            asof_total += asof_added
            racers_total += racers_added
            names.append(name)
        connection.close()
        connection = None
        backup.unlink(missing_ok=True)
        return {"applied_files": len(names), "applied_names": names,
                "asof_added": asof_total, "racers_added": racers_total,
                **_slim_summary(slim_db)}
    except Exception:
        if connection is not None:
            connection.close()
        if backup.is_file():
            shutil.copy2(backup, slim_db)
        raise
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
