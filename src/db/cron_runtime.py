"""Shared runtime helpers for Render and local cron jobs."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
import os
from typing import Iterator
from zoneinfo import ZoneInfo

from src.db.connection import connect as db_connect


JST = ZoneInfo("Asia/Tokyo")
TASK_STATUSES = {"running", "skipped", "success", "failure"}


def _now_iso() -> str:
    return datetime.now(JST).replace(tzinfo=None).isoformat(timespec="seconds")


def ensure_task_runs_table(conn) -> None:
    """Ensure local SQLite has task_runs; production Postgres is migration-owned."""
    if getattr(conn, "_kind", "sqlite") == "postgres":
        conn.execute("SELECT 1 FROM task_runs LIMIT 0")
        return
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS task_runs (
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
        );
        """
    )
    conn.commit()


def _naive_jst(value: datetime) -> datetime:
    if value.tzinfo is not None:
        value = value.astimezone(JST).replace(tzinfo=None)
    return value


def reap_stale_running_tasks(
    conn,
    *,
    older_than_hours: int = 6,
    now: datetime | None = None,
) -> int:
    """Mark only unfinished running rows older than the safety threshold failed."""
    if isinstance(older_than_hours, bool) or older_than_hours <= 0:
        raise ValueError("older_than_hours must be a positive number")

    now_naive = _naive_jst(now or datetime.now(JST)).replace(microsecond=0)
    threshold = now_naive - timedelta(hours=older_than_hours)
    rows = conn.execute(
        """
        SELECT task_name, run_date, started_at
          FROM task_runs
         WHERE status = 'running'
           AND finished_at IS NULL
           AND started_at IS NOT NULL
        """
    ).fetchall()

    stale_rows: list[tuple[str, str, object]] = []
    for task_name, run_date, started_at in rows:
        try:
            parsed_start = (
                started_at
                if isinstance(started_at, datetime)
                else datetime.fromisoformat(str(started_at))
            )
            parsed_start = _naive_jst(parsed_start)
        except (TypeError, ValueError):
            continue
        if parsed_start < threshold:
            stale_rows.append((str(task_name), str(run_date), started_at))

    if not stale_rows:
        return 0

    finished_at = now_naive.isoformat(timespec="seconds")
    reaped = 0
    for task_name, run_date, started_at in stale_rows:
        cursor = conn.execute(
            """
            UPDATE task_runs
               SET status = 'failure',
                   finished_at = ?,
                   detail = 'stale_running_reaped'
             WHERE task_name = ?
               AND run_date = ?
               AND status = 'running'
               AND finished_at IS NULL
               AND started_at = ?
            """,
            (finished_at, task_name, run_date, started_at),
        )
        reaped += max(int(cursor.rowcount), 0)
    conn.commit()
    return reaped


def record_task_run(
    conn,
    task_name: str,
    run_date: str,
    status: str,
    *,
    detail: str | None = None,
    increment: bool = False,
    trigger: str | None = None,
) -> None:
    """Persist one task transition without overwriting an attempt start at completion."""
    if status not in TASK_STATUSES:
        raise ValueError(f"unsupported cron status: {status}")

    now_iso = _now_iso()
    trigger_name = trigger or os.getenv("BOATRACE_TASK_TRIGGER", "render-cron")
    run_increment = 1 if increment else 0

    if status == "running":
        conn.execute(
            """
            INSERT INTO task_runs
                (task_name, run_date, status, run_count, started_at, finished_at,
                 success_at, trigger, detail)
            VALUES (?, ?, 'running', ?, ?, NULL, NULL, ?, ?)
            ON CONFLICT (task_name, run_date) DO UPDATE SET
                status = 'running',
                run_count = task_runs.run_count + EXCLUDED.run_count,
                started_at = EXCLUDED.started_at,
                finished_at = NULL,
                trigger = EXCLUDED.trigger,
                detail = EXCLUDED.detail
            """,
            (task_name, run_date, run_increment, now_iso, trigger_name, detail),
        )
    elif status == "skipped":
        conn.execute(
            """
            INSERT INTO task_runs
                (task_name, run_date, status, run_count, started_at, finished_at,
                 success_at, trigger, detail)
            VALUES (?, ?, 'skipped', ?, ?, ?, NULL, ?, ?)
            ON CONFLICT (task_name, run_date) DO UPDATE SET
                status = CASE WHEN task_runs.status = 'running'
                              THEN task_runs.status ELSE 'skipped' END,
                run_count = task_runs.run_count + CASE
                    WHEN task_runs.status = 'running' THEN 0 ELSE EXCLUDED.run_count END,
                started_at = task_runs.started_at,
                finished_at = CASE WHEN task_runs.status = 'running'
                                   THEN task_runs.finished_at ELSE EXCLUDED.finished_at END,
                trigger = CASE WHEN task_runs.status = 'running'
                               THEN task_runs.trigger ELSE EXCLUDED.trigger END,
                detail = CASE WHEN task_runs.status = 'running'
                              THEN task_runs.detail ELSE EXCLUDED.detail END
            """,
            (
                task_name,
                run_date,
                run_increment,
                now_iso,
                now_iso,
                trigger_name,
                detail,
            ),
        )
    else:
        success_at = now_iso if status == "success" else None
        conn.execute(
            """
            INSERT INTO task_runs
                (task_name, run_date, status, run_count, started_at, finished_at,
                 success_at, trigger, detail)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (task_name, run_date) DO UPDATE SET
                status = EXCLUDED.status,
                run_count = task_runs.run_count + CASE
                    WHEN task_runs.status = 'running' THEN 0 ELSE EXCLUDED.run_count END,
                started_at = COALESCE(task_runs.started_at, EXCLUDED.started_at),
                finished_at = EXCLUDED.finished_at,
                success_at = COALESCE(EXCLUDED.success_at, task_runs.success_at),
                trigger = EXCLUDED.trigger,
                detail = EXCLUDED.detail
            """,
            (
                task_name,
                run_date,
                status,
                run_increment,
                now_iso,
                now_iso,
                success_at,
                trigger_name,
                detail,
            ),
        )
    conn.commit()


@contextmanager
def advisory_lock(
    conn,
    name: str,
    *,
    suppress_unlock_errors: bool = False,
) -> Iterator[bool]:
    """Use a named Postgres advisory lock; SQLite always acquires it."""
    is_postgres = getattr(conn, "_kind", "sqlite") == "postgres"
    locked = True
    if is_postgres:
        row = conn.execute(
            "SELECT pg_try_advisory_lock(hashtext(?))",
            (name,),
        ).fetchone()
        locked = bool(row and row[0])
    try:
        yield locked
    finally:
        if is_postgres and locked:
            try:
                conn.execute("SELECT pg_advisory_unlock(hashtext(?))", (name,))
            except Exception:
                if not suppress_unlock_errors:
                    raise


def parse_race_close_jst(closed_at: object, race_date: str) -> datetime | None:
    if isinstance(closed_at, datetime):
        return closed_at.replace(tzinfo=JST) if closed_at.tzinfo is None else closed_at
    if not isinstance(closed_at, str):
        return None
    try:
        if " " in closed_at and len(closed_at) >= 16:
            parsed = datetime.fromisoformat(closed_at)
        else:
            time_part = closed_at if len(closed_at) >= 5 else f"{closed_at}:00"
            parsed = datetime.fromisoformat(f"{race_date} {time_part}")
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=JST)


def find_missing_original_exhibition_races(
    now: datetime,
    *,
    target_date: str | None = None,
    past_min: int,
    future_min: int,
    limit: int,
    connect=None,
) -> list[tuple[str, int, int, datetime]]:
    """Return races whose six original-exhibition rows or metrics are incomplete."""
    from src.collectors import original_exhibition as collector

    supported = sorted(
        int(stadium)
        for stadium, patterns in collector.SOURCE_PATTERNS.items()
        if patterns
    )
    if not supported:
        return []

    target_date = target_date or now.date().isoformat()
    placeholders = ",".join("?" for _ in supported)
    connect = connect or db_connect
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT r.race_id, r.stadium_number, r.race_number, r.race_closed_at,
                   COUNT(DISTINCT oe.boat_number) AS original_rows,
                   COUNT(DISTINCT CASE
                       WHEN oe.lap_time IS NOT NULL AND oe.lap_time != 0
                       THEN oe.boat_number END) AS lap_rows,
                   COUNT(DISTINCT CASE
                       WHEN oe.turn_time IS NOT NULL AND oe.turn_time != 0
                       THEN oe.boat_number END) AS turn_rows,
                   COUNT(DISTINCT CASE
                       WHEN oe.straight_time IS NOT NULL AND oe.straight_time != 0
                       THEN oe.boat_number END) AS straight_rows
              FROM races r
              LEFT JOIN race_original_exhibitions oe ON oe.race_id = r.race_id
             WHERE r.race_date = ?
               AND r.stadium_number IN ({placeholders})
               AND r.race_closed_at IS NOT NULL
             GROUP BY r.race_id, r.stadium_number, r.race_number, r.race_closed_at
             ORDER BY r.race_closed_at
            """,
            (target_date, *supported),
        ).fetchall()

    due: list[tuple[str, int, int, datetime]] = []
    for row in rows:
        race_id, stadium, race_no, closed_at, original_rows, lap_rows, turn_rows, straight_rows = row
        original_count = int(original_rows or 0)
        metric_counts = [int(lap_rows or 0), int(turn_rows or 0), int(straight_rows or 0)]
        if original_count >= 6 and all(count >= 6 for count in metric_counts):
            continue
        close = parse_race_close_jst(closed_at, target_date)
        if close is None:
            continue
        mins_until = (close - now).total_seconds() / 60.0
        if mins_until < -abs(past_min) or mins_until > future_min:
            continue
        due.append((str(race_id), int(stadium), int(race_no), close))
        if limit > 0 and len(due) >= limit:
            break
    return due
