"""Apply unapplied kachisuji SQLite deltas to the Render slim database."""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests


BUCKET = "kachisuji-deltas"
STORAGE_NAME_RE = re.compile(r"^\d{8}\.db$")
TABLES = ("asof_race_features", "racers")
DEFAULT_DB = Path("/data/kachisuji_slim.db")
LOCAL_DELTA_NAME_RE = re.compile(r"^kachisuji_delta_(\d{8})\.db$")


def _headers(service_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {service_key}",
        "apikey": service_key,
    }


@dataclass
class SupabaseDeltaStorage:
    supabase_url: str
    service_key: str
    session: requests.Session | None = None
    timeout: float = 60.0

    def __post_init__(self) -> None:
        if self.session is None:
            self.session = requests.Session()

    def list_names(self) -> list[str]:
        names: list[str] = []
        offset = 0
        limit = 100
        url = f"{self.supabase_url.rstrip('/')}/storage/v1/object/list/{BUCKET}"
        while True:
            response = self.session.post(
                url,
                headers={**_headers(self.service_key), "Content-Type": "application/json"},
                json={
                    "prefix": "",
                    "limit": limit,
                    "offset": offset,
                    "sortBy": {"column": "name", "order": "asc"},
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            page = response.json()
            if not isinstance(page, list):
                raise ValueError("unexpected Supabase Storage list response")
            names.extend(
                str(item["name"])
                for item in page
                if isinstance(item, dict)
                and isinstance(item.get("name"), str)
                and STORAGE_NAME_RE.fullmatch(item["name"])
            )
            if len(page) < limit:
                break
            offset += limit
        return sorted(set(names))

    def download(self, name: str, destination: Path) -> None:
        if not STORAGE_NAME_RE.fullmatch(name):
            raise ValueError(f"invalid delta object name: {name}")
        url = (
            f"{self.supabase_url.rstrip('/')}/storage/v1/object/authenticated/"
            f"{BUCKET}/{quote(name, safe='')}"
        )
        response = self.session.get(
            url,
            headers=_headers(self.service_key),
            timeout=self.timeout,
        )
        response.raise_for_status()
        destination.write_bytes(response.content)


def _readonly_uri(path: Path) -> str:
    return path.resolve().as_uri() + "?mode=ro"


def _canonical_delta_name(path: Path) -> str:
    if STORAGE_NAME_RE.fullmatch(path.name):
        return path.name
    match = LOCAL_DELTA_NAME_RE.fullmatch(path.name)
    if match:
        return f"{match.group(1)}.db"
    raise ValueError(f"invalid delta filename: {path.name}")


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def _read_applied_names(slim_db: Path) -> set[str]:
    # The application may hold its own mode=ro connection. This short-lived
    # inspection does not create the bookkeeping table.
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


def _validate_schema(
    connection: sqlite3.Connection, delta_connection: sqlite3.Connection
) -> None:
    for table in TABLES:
        main_columns = connection.execute(f"PRAGMA main.table_info({table})").fetchall()
        delta_columns = delta_connection.execute(f"PRAGMA table_info({table})").fetchall()
        if not main_columns or not delta_columns:
            raise ValueError(f"missing required table: {table}")
        if [row[1] for row in main_columns] != [row[1] for row in delta_columns]:
            raise ValueError(f"schema mismatch for table: {table}")


def _apply_one(
    connection: sqlite3.Connection,
    delta_path: Path,
    name: str,
) -> tuple[int, int]:
    delta_connection = sqlite3.connect(_readonly_uri(delta_path), uri=True)
    try:
        _validate_schema(connection, delta_connection)
        before = {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in TABLES
        }
        for table in TABLES:
            column_count = len(
                delta_connection.execute(f"PRAGMA table_info({table})").fetchall()
            )
            placeholders = ",".join("?" for _ in range(column_count))
            connection.executemany(
                f"INSERT OR IGNORE INTO {table} VALUES ({placeholders})",
                delta_connection.execute(f"SELECT * FROM {table}"),
            )
        connection.execute(
            "INSERT OR IGNORE INTO applied_deltas(name, applied_at) VALUES (?, ?)",
            (name, datetime.now(timezone.utc).isoformat()),
        )
        after = {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in TABLES
        }
        return (
            after["asof_race_features"] - before["asof_race_features"],
            after["racers"] - before["racers"],
        )
    finally:
        delta_connection.close()


def apply_delta_files(
    slim_db: Path,
    deltas: list[tuple[str, Path]],
) -> dict[str, int | str | None]:
    """Apply every pending delta in one rollback-protected transaction."""
    slim_db = slim_db.resolve()
    if not slim_db.is_file():
        raise FileNotFoundError(f"slim DB not found: {slim_db}")
    applied = _read_applied_names(slim_db)
    pending = [(name, path) for name, path in sorted(deltas) if name not in applied]
    if not pending:
        return _summary(slim_db, applied_files=0, asof_added=0, racers_added=0)

    connection: sqlite3.Connection | None = None
    try:
        # Keep the same connection contract as the web apply path.
        connection = sqlite3.connect(str(slim_db), uri=True, timeout=30.0)
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS applied_deltas ("
            "name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        asof_added = 0
        racers_added = 0
        applied_files = 0
        for name, delta_path in pending:
            if connection.execute(
                "SELECT 1 FROM applied_deltas WHERE name = ?", (name,)
            ).fetchone():
                continue
            added_asof, added_racers = _apply_one(connection, delta_path, name)
            asof_added += added_asof
            racers_added += added_racers
            applied_files += 1
        connection.commit()
        connection.close()
        connection = None
        return _summary(
            slim_db,
            applied_files=applied_files,
            asof_added=asof_added,
            racers_added=racers_added,
        )
    except Exception:
        if connection is not None:
            if connection.in_transaction:
                connection.rollback()
            connection.close()
        raise


def _summary(
    slim_db: Path,
    *,
    applied_files: int,
    asof_added: int,
    racers_added: int,
) -> dict[str, int | str | None]:
    connection = sqlite3.connect(_readonly_uri(slim_db), uri=True)
    try:
        row = connection.execute("SELECT MAX(race_date) FROM asof_race_features").fetchone()
    finally:
        connection.close()
    return {
        "applied_files": applied_files,
        "asof_added": asof_added,
        "racers_added": racers_added,
        "latest_race_date": row[0] if row else None,
    }


def apply_storage_deltas(
    slim_db: Path,
    storage: SupabaseDeltaStorage,
) -> dict[str, int | str | None]:
    applied = _read_applied_names(slim_db)
    pending_names = [name for name in storage.list_names() if name not in applied]
    if not pending_names:
        return _summary(slim_db, applied_files=0, asof_added=0, racers_added=0)
    with tempfile.TemporaryDirectory(prefix="kachisuji-deltas-") as temp_dir:
        root = Path(temp_dir)
        deltas: list[tuple[str, Path]] = []
        for name in pending_names:
            destination = root / name
            storage.download(name, destination)
            deltas.append((name, destination))
        return apply_delta_files(slim_db, deltas)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(os.getenv("KACHISUJI_DB", str(DEFAULT_DB))),
    )
    parser.add_argument(
        "--delta",
        action="append",
        type=Path,
        help="Apply a local delta instead of downloading Storage objects (repeatable).",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        if args.delta:
            deltas = [(_canonical_delta_name(path), path) for path in args.delta]
            result = apply_delta_files(args.db, deltas)
        else:
            supabase_url = os.getenv("SUPABASE_URL", "").strip()
            service_key = os.getenv("SUPABASE_SERVICE_KEY", "").strip()
            if not supabase_url or not service_key:
                print(
                    "error: SUPABASE_URL and SUPABASE_SERVICE_KEY are required",
                    file=sys.stderr,
                )
                return 2
            result = apply_storage_deltas(
                args.db,
                SupabaseDeltaStorage(supabase_url, service_key),
            )
    except Exception as exc:
        print(f"error: kachisuji delta apply failed: {exc}", file=sys.stderr)
        return 1
    print("[summary] " + " ".join(f"{key}={value}" for key, value in result.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
