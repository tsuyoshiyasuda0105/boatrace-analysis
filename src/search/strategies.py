"""Persist ROI search strategies and match them against a race date."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Any, Mapping

from src.search.roi_search import SUPPORTED_SCHEMA_VERSIONS, _compile_conditions


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SEARCH_DB = PROJECT_ROOT / "data" / "kachisuji_search.db"
DEFAULT_STRATEGY_DB = PROJECT_ROOT / "data" / "kachisuji_strategies.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS strategies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner TEXT NOT NULL DEFAULT 'local',
  name TEXT NOT NULL,
  conditions_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  backtest_json TEXT,
  is_active INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_strategies_owner_active
  ON strategies(owner, is_active);
"""

# These are the only Step 2 condition columns whose values are established on
# race day.  All other columns returned by _compile_conditions are prior-day
# facts; a NULL in one of those must not become a morning candidate.
_SAME_DAY_COLUMNS = frozenset({"weather", "wind_speed"}) | frozenset(
    f"b{boat}_{suffix}"
    for boat in range(1, 7)
    for suffix in ("ex_time", "ex_rank", "ex_dev", "ex_st")
)
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")
_BET_LABELS = {"tansho": "単勝", "nirentan": "2連単", "sanrentan": "3連単"}


def _strategy_db_path() -> Path:
    return Path(os.environ.get("KACHISUJI_STRATEGY_DB") or DEFAULT_STRATEGY_DB)


def _json_text(value: Any, label: str) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be valid JSON") from exc


def _validated_conditions(conditions: Any) -> dict[str, Any]:
    if not isinstance(conditions, Mapping):
        raise ValueError("conditions must be an object")
    # JSON round-tripping both guarantees storable input and detaches the
    # saved value from caller-owned mutable dictionaries.
    encoded = _json_text(dict(conditions), "conditions")
    normalized = json.loads(encoded)
    _compile_conditions(normalized)
    return normalized


@contextmanager
def _write_connect(path: str | Path):
    resolved = Path(path).resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(resolved, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            connection.executescript(_SCHEMA)
            yield connection
    finally:
        connection.close()


@contextmanager
def _read_connect(path: str | Path):
    resolved = Path(path).resolve()
    connection = sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        yield connection
    finally:
        connection.close()


def _decode_strategy(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "owner": row["owner"],
        "name": row["name"],
        "conditions": json.loads(row["conditions_json"]),
        "created_at": row["created_at"],
        "backtest": json.loads(row["backtest_json"]) if row["backtest_json"] is not None else None,
        "is_active": bool(row["is_active"]),
    }


def save_strategy(
    name: str,
    conditions: Mapping[str, Any],
    backtest: Any = None,
    owner: str = "local",
    *,
    db_path: str | Path | None = None,
) -> int:
    """Validate and save one strategy, returning its sequence ID."""

    if not isinstance(name, str) or not name.strip():
        raise ValueError("name must not be empty")
    if not isinstance(owner, str) or not owner.strip():
        raise ValueError("owner must not be empty")
    normalized = _validated_conditions(conditions)
    conditions_json = _json_text(normalized, "conditions")
    backtest_json = None if backtest is None else _json_text(backtest, "backtest")
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _write_connect(db_path or _strategy_db_path()) as connection:
        cursor = connection.execute(
            "INSERT INTO strategies "
            "(owner, name, conditions_json, created_at, backtest_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (owner.strip(), name.strip(), conditions_json, created_at, backtest_json),
        )
        return int(cursor.lastrowid)


def list_strategies(
    owner: str = "local",
    include_inactive: bool = False,
    *,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Return strategies for an owner, newest first."""

    if not isinstance(owner, str) or not owner.strip():
        raise ValueError("owner must not be empty")
    sql = "SELECT * FROM strategies WHERE owner = ?"
    params: list[Any] = [owner.strip()]
    if not include_inactive:
        sql += " AND is_active = ?"
        params.append(1)
    sql += " ORDER BY created_at DESC, id DESC"
    with _write_connect(db_path or _strategy_db_path()) as connection:
        return [_decode_strategy(row) for row in connection.execute(sql, params)]


def get_strategy(strategy_id: int, *, db_path: str | Path | None = None) -> dict[str, Any] | None:
    """Return a strategy by ID, including inactive strategies."""

    if isinstance(strategy_id, bool) or not isinstance(strategy_id, int):
        raise ValueError("strategy id must be an integer")
    with _write_connect(db_path or _strategy_db_path()) as connection:
        row = connection.execute("SELECT * FROM strategies WHERE id = ?", (strategy_id,)).fetchone()
    return _decode_strategy(row) if row is not None else None


def deactivate_strategy(strategy_id: int, *, db_path: str | Path | None = None) -> bool:
    """Soft-delete an active strategy."""

    if isinstance(strategy_id, bool) or not isinstance(strategy_id, int):
        raise ValueError("strategy id must be an integer")
    with _write_connect(db_path or _strategy_db_path()) as connection:
        cursor = connection.execute(
            "UPDATE strategies SET is_active = 0 WHERE id = ? AND is_active = 1",
            (strategy_id,),
        )
        return cursor.rowcount == 1


def _get_strategy_from(path: str | Path, strategy_id: int) -> dict[str, Any] | None:
    try:
        with _read_connect(path) as connection:
            row = connection.execute("SELECT * FROM strategies WHERE id = ?", (strategy_id,)).fetchone()
    except sqlite3.OperationalError as exc:
        raise ValueError("strategy database is not initialized") from exc
    return _decode_strategy(row) if row is not None else None


def _bet_label(kind: str, expected: int | str) -> str:
    return f"{_BET_LABELS[kind]} {expected}"


def match_races(
    strategy_id_or_conditions: int | Mapping[str, Any],
    target_date: str,
    search_db: str | Path = DEFAULT_SEARCH_DB,
    strategies_db: str | Path = DEFAULT_STRATEGY_DB,
) -> dict[str, Any]:
    """Match one saved strategy or an ad-hoc condition object for a date.

    The Step 2 compiler supplies validation, SQL predicates, referenced
    columns, and bet parsing.  Search-period bounds are backtest settings, so
    the explicit target date replaces them for forward matching.
    """

    try:
        normalized_date = date.fromisoformat(target_date).isoformat()
    except (TypeError, ValueError) as exc:
        raise ValueError("date must be an ISO date") from exc

    strategy_id: int | None
    strategy_name: str | None
    if isinstance(strategy_id_or_conditions, bool):
        raise ValueError("strategy id must be an integer")
    if isinstance(strategy_id_or_conditions, int):
        strategy = _get_strategy_from(strategies_db, strategy_id_or_conditions)
        if strategy is None:
            raise ValueError("strategy not found")
        strategy_id = strategy["id"]
        strategy_name = strategy["name"]
        conditions = strategy["conditions"]
    else:
        strategy_id = None
        strategy_name = None
        conditions = _validated_conditions(strategy_id_or_conditions)

    daily_conditions = dict(conditions)
    daily_conditions.pop("date_from", None)
    daily_conditions.pop("date_to", None)
    where, params, referenced_columns, bet = _compile_conditions(daily_conditions)
    if any(_IDENTIFIER.fullmatch(column) is None for column in referenced_columns):
        raise ValueError("condition compiler returned an invalid column")

    selected_columns = ["race_id", "jcd", "race_no", *referenced_columns]
    sql = (
        f"SELECT {', '.join(selected_columns)} FROM asof_race_features "
        f"WHERE race_date = ? AND {where} ORDER BY jcd, race_no, race_id"
    )
    with _read_connect(search_db) as connection:
        races_on_date = int(
            connection.execute(
                "SELECT COUNT(*) FROM asof_race_features "
                "WHERE race_date = ? AND schema_version IN (?, ?)",
                (normalized_date, *SUPPORTED_SCHEMA_VERSIONS),
            ).fetchone()[0]
        )
        rows = connection.execute(sql, [normalized_date, *params]).fetchall()

    matched: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    ticket = _bet_label(bet.kind, bet.expected)
    for row in rows:
        missing = [column for column in referenced_columns if row[column] is None]
        # NULL in a prior-day condition is not evidence of a match.
        if any(column not in _SAME_DAY_COLUMNS for column in missing):
            continue
        item = {
            "race_id": row["race_id"],
            "jcd": row["jcd"],
            "race_no": row["race_no"],
            "bet": ticket,
        }
        if missing:
            item.update(status="pending", undetermined_columns=missing)
            pending.append(item)
        else:
            item["status"] = "confirmed"
            matched.append(item)

    return {
        "strategy_id": strategy_id,
        "strategy_name": strategy_name,
        "target_date": normalized_date,
        "matched": matched,
        "pending": pending,
        "counts": {
            "races_on_date": races_on_date,
            "matched": len(matched),
            "pending": len(pending),
        },
    }


def match_all_strategies(
    target_date: str,
    search_db: str | Path = DEFAULT_SEARCH_DB,
    strategies_db: str | Path = DEFAULT_STRATEGY_DB,
    owner: str = "local",
) -> list[dict[str, Any]]:
    """Match every active strategy from one strategy database."""

    with _write_connect(strategies_db) as connection:
        ids = [
            int(row[0])
            for row in connection.execute(
                "SELECT id FROM strategies WHERE owner = ? AND is_active = 1 ORDER BY id",
                (owner,),
            )
        ]
    return [match_races(item, target_date, search_db, strategies_db) for item in ids]


__all__ = [
    "deactivate_strategy",
    "get_strategy",
    "list_strategies",
    "match_all_strategies",
    "match_races",
    "save_strategy",
]
