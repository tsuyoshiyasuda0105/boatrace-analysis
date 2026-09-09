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
import re
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

TABLES = ("asof_race_features", "racers")
# デルタから受け取ってよい列名の形。ALTER TABLE は識別子をパラメータに
# できないので、文字列として組み立てる前にここで必ず狭める。
_COLUMN_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")
TRANSPORT_TABLE = "kachisuji_delta_files"
_DELTA_NAME_OK = __import__("re").compile(r"^\d{8}\.db$")
# 一度きりの補正デルタ。名前を保存したまま輸送し、適用側で
# _delta_wants_replace が既存行を上書きできるようにする (通常の \d{8}.db は
# 追加専用のまま)。安全な文字だけ許可してパス経路の混入を防ぐ。
_BACKFILL_NAME_OK = __import__("re").compile(r"^backfill_[A-Za-z0-9_]+\.db$")
# 値だけを運ぶ「継ぎ当て」。既存行の一部の列を書き換えるだけで、行は増やさない。
_PATCH_NAME_OK = __import__("re").compile(r"^patch_[A-Za-z0-9_]+\.db$")
PATCH_TABLE = "asof_column_patch"
MIN_FREE_BYTES = 100 * 1024 * 1024


class InsufficientDiskSpaceError(RuntimeError):
    def __init__(self, free_bytes: int, required_bytes: int = MIN_FREE_BYTES):
        self.free_bytes = int(free_bytes)
        self.required_bytes = int(required_bytes)
        super().__init__(
            f"insufficient free space: {self.free_bytes} bytes available; "
            f"at least {self.required_bytes} bytes required"
        )


def disk_free_bytes(path: Path) -> int:
    return int(shutil.disk_usage(Path(path).resolve().parent).free)


# 旧実装 (2026-08-20 以前) は適用前に slim の全量コピーを .bak として作っていた。
# 573MB の DB に対し 1GB のディスクでは必ず溢れ、途中まで書かれた .bak が
# 空き容量を食い尽くしたまま残る。新実装は .bak を一切作らないので、
# 残っている .bak は復旧不要のゴミであり、安全に削除してよい。
STALE_SUFFIXES = (".bak", ".bak.tmp", ".tmp")


def disk_report(path: Path) -> dict[str, Any]:
    """slim DB を置くディレクトリの容量と中身を返す (障害調査用)。"""
    target = Path(path).resolve()
    directory = target.parent
    usage = shutil.disk_usage(directory)
    entries = []
    try:
        for child in sorted(directory.iterdir()):
            try:
                entries.append({"name": child.name, "size_bytes": child.stat().st_size})
            except OSError:
                entries.append({"name": child.name, "size_bytes": None})
    except OSError as exc:
        entries.append({"name": f"<unreadable: {exc}>", "size_bytes": None})
    return {
        "directory": str(directory),
        "total_bytes": int(usage.total),
        "used_bytes": int(usage.used),
        "free_bytes": int(usage.free),
        "entries": entries,
    }


def cleanup_stale_artifacts(slim_db: Path) -> list[dict[str, Any]]:
    """旧実装が残した .bak 等のゴミを削除し、削除した内容を返す。

    slim DB 本体・WAL/SHM には触れない (使用中の可能性があるため)。
    """
    slim_db = Path(slim_db).resolve()
    removed: list[dict[str, Any]] = []
    for suffix in STALE_SUFFIXES:
        candidate = Path(str(slim_db) + suffix)
        if not candidate.is_file():
            continue
        try:
            size = candidate.stat().st_size
            candidate.unlink()
            removed.append({"name": candidate.name, "size_bytes": size})
        except OSError as exc:
            removed.append({"name": candidate.name, "error": str(exc)[:200]})
    return removed


# ---------------------------------------------------------------- transport --

def _default_conn():
    from src.db.connection import connect

    # direct=True: web の共有プールを使わない。デルタ適用は 06:30 の朝バッチ
    # 集中時間帯に走り、ペイロード読み込みで数秒接続を握る。プール (枠 6) を
    # 経由すると、混雑時に自分が枠を取れず ConnectionCheckoutBudgetExceeded で
    # 失敗する (2026-08-25 06:31 実障害: 昨日分の取込が 500 になり、preflight が
    # backtest_yesterday_import fail を出した)。逆に自分が握れば閲覧者を
    # 待たせる。短命の直結接続なら双方に影響しない (cron が無傷なのと同じ理屈)。
    return connect(direct=True)


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
    if _BACKFILL_NAME_OK.fullmatch(name) or _PATCH_NAME_OK.fullmatch(name):
        # backfill_*.db / patch_*.db は名前を保持 (適用側の判定に使う)。
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


def _adopt_new_columns(
    connection: sqlite3.Connection,
    table: str,
    main_names: list[str],
    delta_names: list[str],
) -> list[str]:
    """デルタ側にだけある列を slim 側へ足す。足した列名を返す。

    PC 側で特徴量に列が増えると、本番の slim DB には無いので列名の並びが
    合わず、デルタが丸ごと拒否される。すると本番の更新が黙って止まる
    (2026-09-10 に進入変更率を足して気づいた)。

    足してよいのは「末尾に付け足すだけで並びが一致する」場合に限る。
    途中に差し込まれていたら本当の食い違いなので、そのまま拒否する。
    ALTER TABLE ADD COLUMN は末尾にしか足せないため、この条件は
    「足したあと必ず一致する」ことと同じ意味になる。
    """
    missing = [name for name in delta_names if name not in main_names]
    if not missing or main_names + missing != delta_names:
        return []
    # 列名はデルタファイル (外部から届くバイト列) 由来なので、そのまま
    # DDL へ入れない。特徴量の列名として有り得る形だけを通す。
    bad = [name for name in missing if not _COLUMN_NAME.fullmatch(name)]
    if bad:
        raise ValueError(f"unsafe column name in delta: {bad}")
    for name in missing:
        # 型は取らず NULL 許容で足す。値はデルタ側の行がそのまま運ぶ。
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {name}")
    return missing


def _validate_schema(
    connection: sqlite3.Connection, delta_connection: sqlite3.Connection
) -> list[str]:
    added: list[str] = []
    for table in TABLES:
        main_cols = connection.execute(f"PRAGMA main.table_info({table})").fetchall()
        delta_cols = delta_connection.execute(f"PRAGMA table_info({table})").fetchall()
        if not main_cols or not delta_cols:
            raise ValueError(f"missing required table: {table}")
        main_names = [r[1] for r in main_cols]
        delta_names = [r[1] for r in delta_cols]
        if main_names != delta_names:
            added += _adopt_new_columns(connection, table, main_names, delta_names)
            main_names = [
                r[1] for r in connection.execute(f"PRAGMA main.table_info({table})")
            ]
            if main_names != delta_names:
                raise ValueError(f"schema mismatch for table: {table}")
    return added


def _delta_is_patch(name: str) -> bool:
    """名前が "patch_" で始まるものだけ「値の継ぎ当て」として扱う。

    丸ごと運ぶデルタは 1 行 1KB 強あり、後から足した列の値を過去全行へ
    届けるには 638MB 動かすことになる。継ぎ当ては race_id と対象の列だけを
    運ぶので 56MB で済む (2026-09-10: 進入変更率 569,082 行)。

    行は作らない。slim に無い race_id は黙って無視する。列を足すことは
    あるが、値を消すことはしない。
    """
    return name.lower().startswith("patch_")


def _apply_patch(
    connection: sqlite3.Connection, delta_connection: sqlite3.Connection
) -> tuple[int, list[str]]:
    """継ぎ当てを当てる。(書き換えた行数, 足した列名) を返す。"""
    columns = [
        str(row[1])
        for row in delta_connection.execute(f"PRAGMA table_info({PATCH_TABLE})")
    ]
    if not columns:
        raise ValueError(f"missing required table: {PATCH_TABLE}")
    if columns[0] != "race_id":
        raise ValueError(f"{PATCH_TABLE} must start with race_id")
    targets = columns[1:]
    if not targets:
        raise ValueError(f"{PATCH_TABLE} has no columns to write")
    # 列名は外部から届くファイル由来。DDL にも UPDATE にも入れる前に狭める。
    bad = [name for name in targets if not _COLUMN_NAME.fullmatch(name)]
    if bad:
        raise ValueError(f"unsafe column name in patch: {bad}")
    if len(set(targets)) != len(targets):
        raise ValueError(f"{PATCH_TABLE} repeats a column")

    table = "asof_race_features"
    existing = {
        str(row[1]) for row in connection.execute(f"PRAGMA main.table_info({table})")
    }
    if "race_id" not in existing:
        raise ValueError(f"missing required table: {table}")
    added = [name for name in targets if name not in existing]
    for name in added:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {name}")

    assignments = ", ".join(f"{name}=?" for name in targets)
    cursor = connection.executemany(
        f"UPDATE {table} SET {assignments} WHERE race_id=?",
        (
            (*row[1:], row[0])
            for row in delta_connection.execute(
                f"SELECT race_id, {', '.join(targets)} FROM {PATCH_TABLE}"
            )
        ),
    )
    return int(cursor.rowcount or 0), added


def _delta_wants_replace(name: str) -> bool:
    """名前が "backfill" で始まるデルタだけ既存行の上書きを許す。

    通常の毎晩デルタは追加専用 (INSERT OR IGNORE) のまま。既にある行を
    直せないので、過去の穴埋め (2026-08-29: 福岡2016-24・多摩川2016-20 の
    選手情報欠測 約3万レース) を本番へ届けられなかった。名前で明示された
    一度きりの補正デルタに限り INSERT OR REPLACE で古い行を置き換える。
    誤って通常デルタを上書きモードにしないよう、判定は名前の接頭辞に固定する。
    """
    return name.lower().startswith("backfill")


def _apply_one(connection: sqlite3.Connection, delta_path: Path, name: str) -> tuple[int, int]:
    delta_connection = sqlite3.connect(_readonly_uri(delta_path), uri=True)
    try:
        if _delta_is_patch(name):
            updated, added_columns = _apply_patch(connection, delta_connection)
            if added_columns:
                print(f"[patch] added columns: {', '.join(added_columns)}", flush=True)
            print(f"[patch] {name}: updated {updated:,} rows", flush=True)
            connection.execute(
                "INSERT OR IGNORE INTO applied_deltas(name, applied_at) VALUES (?, ?)",
                (name, datetime.now(timezone.utc).isoformat()),
            )
            # 行は増えない。呼び出し側の集計と噛み合うよう 0 を返す。
            return (0, 0)
        added_columns = _validate_schema(connection, delta_connection)
        if added_columns:
            print(f"[delta] added columns: {', '.join(added_columns)}", flush=True)
        verb = "INSERT OR REPLACE" if _delta_wants_replace(name) else "INSERT OR IGNORE"
        before = {
            t: int(connection.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
            for t in TABLES
        }
        for t in TABLES:
            column_count = len(delta_connection.execute(f"PRAGMA table_info({t})").fetchall())
            placeholders = ",".join("?" for _ in range(column_count))
            connection.executemany(
                f"{verb} INTO {t} VALUES ({placeholders})",
                delta_connection.execute(f"SELECT * FROM {t}"),
            )
        connection.execute(
            "INSERT OR IGNORE INTO applied_deltas(name, applied_at) VALUES (?, ?)",
            (name, datetime.now(timezone.utc).isoformat()),
        )
        after = {
            t: int(connection.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
            for t in TABLES
        }
        return (
            after["asof_race_features"] - before["asof_race_features"],
            after["racers"] - before["racers"],
        )
    finally:
        delta_connection.close()


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
    # 旧実装が残した .bak (最大 573MB) を先に回収する。これがディスクを
    # 食い潰していると、正しい実装でも空き容量チェックで止まってしまう。
    reclaimed = cleanup_stale_artifacts(slim_db)
    applied = read_applied_names(slim_db)
    pending = fetch_pending_payloads(applied, conn=conn)
    free_bytes = disk_free_bytes(slim_db)
    if not pending:
        return {"applied_files": 0, "asof_added": 0, "racers_added": 0,
                "free_bytes": free_bytes, "reclaimed": reclaimed,
                **_slim_summary(slim_db)}
    if free_bytes < MIN_FREE_BYTES:
        raise InsufficientDiskSpaceError(free_bytes)

    connection: sqlite3.Connection | None = None
    with tempfile.TemporaryDirectory(prefix="kachisuji_delta_") as tmp_dir_name:
        tmp_dir = Path(tmp_dir_name)
        for name, payload in pending:
            (tmp_dir / name).write_bytes(payload)
        try:
            connection = sqlite3.connect(str(slim_db), uri=True, timeout=30.0)
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS applied_deltas ("
                "name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            asof_total = racers_total = 0
            names: list[str] = []
            for name, _payload in pending:
                if connection.execute(
                    "SELECT 1 FROM applied_deltas WHERE name = ?", (name,)
                ).fetchone():
                    continue
                asof_added, racers_added = _apply_one(connection, tmp_dir / name, name)
                asof_total += asof_added
                racers_total += racers_added
                names.append(name)
            connection.commit()
        except Exception:
            if connection is not None and connection.in_transaction:
                connection.rollback()
            raise
        finally:
            if connection is not None:
                connection.close()
    return {"applied_files": len(names), "applied_names": names,
            "asof_added": asof_total, "racers_added": racers_total,
            "free_bytes": disk_free_bytes(slim_db), "reclaimed": reclaimed,
            **_slim_summary(slim_db)}
