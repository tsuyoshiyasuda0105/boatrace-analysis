"""Fail-closed overnight program bootstrap for Render cron.

The Render trigger runs every five minutes from 23:00 through 09:59 JST. This
module persists its own retry state, so a failed source is retried after 15,
30, then 60 minutes instead of on every trigger.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime, time, timedelta
import json
import os
from pathlib import Path
import sys
from typing import Any, Iterator
from zoneinfo import ZoneInfo


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
os.environ.setdefault("BOATRACE_TASK_TRIGGER", "render-program-bootstrap")

from scripts.backfill_official import upsert_b  # noqa: E402
from scripts.check_program_source_gate import check_program_source_gate  # noqa: E402
from src.collectors import official_dl, openapi  # noqa: E402
from src.collectors.official_manifest import fetch_official_race_manifest  # noqa: E402
from src.db.connection import (  # noqa: E402
    assert_safe_production_write,
    connect as db_connect,
)
from src.parsers.official_b import parse_b_text  # noqa: E402
from src.deploy_info import log_deploy_revision  # noqa: E402


JST = ZoneInfo("Asia/Tokyo")
BACKOFF_MINUTES = (15, 30, 60)
LOCK_NAME = "boatrace-program-bootstrap-v1"
OFFICIAL_TASK = "render_program_bootstrap_official_v1"
OPENAPI_TASK = "render_program_bootstrap_openapi_v1"
GATE_TASK = "render_program_source_gate_v1"
FINAL_TASK = "render_program_bootstrap_final_v1"
ALERT_TASK = "render_program_bootstrap_alert_v1"
STATUS_NAME = "program_source_bootstrap"


def jst_now() -> datetime:
    return datetime.now(JST)


def target_for_tick(now: datetime) -> date | None:
    if now.hour == 23 and now.minute >= 30:
        return now.date() + timedelta(days=1)
    if 0 <= now.hour < 10:
        return now.date()
    return None


def _ensure_tables() -> None:
    with db_connect() as conn:
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
            CREATE TABLE IF NOT EXISTS system_status (
              check_name TEXT NOT NULL,
              check_date TEXT NOT NULL,
              status TEXT NOT NULL,
              message TEXT,
              detail_json TEXT,
              checked_at TEXT NOT NULL,
              PRIMARY KEY (check_name, check_date)
            );
            """
        )
        conn.commit()


@contextmanager
def _run_lock() -> Iterator[bool]:
    conn = db_connect()
    locked = True
    is_postgres = getattr(conn, "_kind", "sqlite") == "postgres"
    try:
        if is_postgres:
            row = conn.execute(
                "SELECT pg_try_advisory_lock(hashtext(?))", (LOCK_NAME,)
            ).fetchone()
            locked = bool(row and row[0])
        yield locked
    finally:
        if is_postgres and locked:
            conn.execute("SELECT pg_advisory_unlock(hashtext(?))", (LOCK_NAME,))
        conn.close()


def _load_task(task_name: str, target: date) -> dict[str, Any]:
    with db_connect() as conn:
        row = conn.execute(
            """
            SELECT status, run_count, detail, success_at
              FROM task_runs
             WHERE task_name = ? AND run_date = ?
            """,
            (task_name, target.isoformat()),
        ).fetchone()
    if not row:
        return {"status": "missing", "run_count": 0, "detail": {}}
    try:
        detail = json.loads(row[2] or "{}")
    except (TypeError, json.JSONDecodeError):
        detail = {}
    return {
        "status": str(row[0]),
        "run_count": int(row[1] or 0),
        "detail": detail if isinstance(detail, dict) else {},
        "success_at": row[3],
    }


def _write_task(
    task_name: str,
    target: date,
    status: str,
    detail: dict[str, Any],
    *,
    increment: bool = True,
) -> None:
    now_iso = jst_now().replace(tzinfo=None).isoformat(timespec="seconds")
    success_at = now_iso if status == "success" else None
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO task_runs
                (task_name, run_date, status, run_count, started_at, finished_at,
                 success_at, trigger, detail)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'render-program-bootstrap', ?)
            ON CONFLICT (task_name, run_date) DO UPDATE SET
                status = EXCLUDED.status,
                run_count = task_runs.run_count + EXCLUDED.run_count,
                started_at = EXCLUDED.started_at,
                finished_at = EXCLUDED.finished_at,
                success_at = COALESCE(EXCLUDED.success_at, task_runs.success_at),
                trigger = EXCLUDED.trigger,
                detail = EXCLUDED.detail
            """,
            (
                task_name,
                target.isoformat(),
                status,
                1 if increment else 0,
                now_iso,
                now_iso,
                success_at,
                json.dumps(detail, ensure_ascii=True, sort_keys=True),
            ),
        )
        conn.commit()


def _write_status(target: date, status: str, message: str, detail: dict[str, Any]) -> None:
    now_iso = jst_now().replace(tzinfo=None).isoformat(timespec="seconds")
    payload = json.dumps(detail, ensure_ascii=True, sort_keys=True)
    with db_connect() as conn:
        exists = conn.execute(
            "SELECT 1 FROM system_status WHERE check_name=? AND check_date=?",
            (STATUS_NAME, target.isoformat()),
        ).fetchone()
        if exists:
            conn.execute(
                """
                UPDATE system_status
                   SET status=?, message=?, detail_json=?, checked_at=?
                 WHERE check_name=? AND check_date=?
                """,
                (status, message, payload, now_iso, STATUS_NAME, target.isoformat()),
            )
        else:
            conn.execute(
                """
                INSERT INTO system_status
                    (check_name, check_date, status, message, detail_json, checked_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (STATUS_NAME, target.isoformat(), status, message, payload, now_iso),
            )
        conn.commit()


def _parse_next_attempt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed.replace(tzinfo=JST) if parsed.tzinfo is None else parsed.astimezone(JST)


def phase_due(task: dict[str, Any], now: datetime, *, force: bool = False) -> bool:
    if task.get("status") == "success":
        return False
    if force:
        return True
    next_attempt = _parse_next_attempt((task.get("detail") or {}).get("next_attempt_at"))
    return next_attempt is None or now >= next_attempt


def _record_phase_failure(
    task_name: str,
    target: date,
    now: datetime,
    prior: dict[str, Any],
    *,
    source_host: str,
    reason: str,
    missing_stadiums: list[int] | None = None,
) -> None:
    previous = int((prior.get("detail") or {}).get("consecutive_failures", 0) or 0)
    failures = previous + 1
    wait_minutes = BACKOFF_MINUTES[min(failures - 1, len(BACKOFF_MINUTES) - 1)]
    detail = {
        "consecutive_failures": failures,
        "last_reason": reason,
        "source_host": source_host,
        "next_attempt_at": (now + timedelta(minutes=wait_minutes)).isoformat(timespec="seconds"),
        "circuit_open_minutes": wait_minutes,
        "missing_stadiums": sorted(set(missing_stadiums or [])),
    }
    _write_task(task_name, target, "failure", detail)


def _record_phase_success(
    task_name: str,
    target: date,
    *,
    source_host: str,
    stadiums: list[int],
    races: int,
) -> None:
    _write_task(
        task_name,
        target,
        "success",
        {
            "consecutive_failures": 0,
            "source_host": source_host,
            "stadiums": sorted(set(stadiums)),
            "races": races,
            "missing_stadiums": [],
        },
    )


def _manifest_stadiums(target: date) -> tuple[list[int], str]:
    result = fetch_official_race_manifest(target)
    if result.get("status") != "available":
        return [], str(result.get("status") or "http_error")
    payload = result.get("expected_payload") or {}
    stadiums = sorted(
        {
            int(item["stadium_number"])
            for item in payload.get("stadiums", [])
            if item.get("stadium_number") is not None
        }
    )
    return stadiums, "available" if stadiums else "empty"


def _complete_official_stadiums(races: list[dict[str, Any]]) -> set[int]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for race in races:
        grouped.setdefault(int(race.get("stadium_number") or 0), []).append(race)
    complete: set[int] = set()
    for stadium, items in grouped.items():
        race_numbers = {int(item.get("race_number") or 0) for item in items}
        if race_numbers != set(range(1, 13)):
            continue
        if all(
            len(item.get("boats") or []) == 6
            and all(
                boat.get("racer_number") is not None
                and boat.get("assigned_motor_number") is not None
                for boat in item.get("boats") or []
            )
            for item in items
        ):
            complete.add(stadium)
    return complete


def _openapi_races(payload: dict[str, Any]) -> list[dict[str, Any]]:
    races = payload.get("programs")
    if races is None:
        races = ((payload.get("today") or {}).get("programs"))
    if races is None:
        races = ((payload.get("data") or {}).get("programs"))
    return list(races or [])


def _complete_openapi_stadiums(races: list[dict[str, Any]]) -> set[int]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for race in races:
        grouped.setdefault(int(race.get("race_stadium_number") or 0), []).append(race)
    complete: set[int] = set()
    for stadium, items in grouped.items():
        race_numbers = {int(item.get("race_number") or 0) for item in items}
        if race_numbers != set(range(1, 13)):
            continue
        if all(
            len(item.get("boats") or []) == 6
            and all(
                boat.get("racer_number") is not None
                and boat.get("racer_assigned_motor_number") is not None
                for boat in item.get("boats") or []
            )
            for item in items
        ):
            complete.add(stadium)
    return complete


def collect_official(
    target: date,
    now: datetime,
    prior: dict[str, Any],
) -> bool:
    expected, manifest_state = _manifest_stadiums(target)
    if not expected:
        _record_phase_failure(
            OFFICIAL_TASK,
            target,
            now,
            prior,
            source_host="boatrace.jp",
            reason=f"manifest_{manifest_state}",
        )
        return False

    path = official_dl.fetch_one("B", target)
    if path is None:
        _record_phase_failure(
            OFFICIAL_TASK,
            target,
            now,
            prior,
            source_host="boatrace.jp",
            reason="official_b_unavailable",
            missing_stadiums=expected,
        )
        return False
    try:
        parsed = parse_b_text(path.read_bytes().decode("cp932", errors="replace"), target)
    except (OSError, ValueError, UnicodeError) as exc:
        _record_phase_failure(
            OFFICIAL_TASK,
            target,
            now,
            prior,
            source_host="boatrace.jp",
            reason=f"official_b_{type(exc).__name__}",
            missing_stadiums=expected,
        )
        return False

    complete = _complete_official_stadiums(parsed)
    missing = sorted(set(expected) - complete)
    requested = (prior.get("detail") or {}).get("missing_stadiums") or expected
    requested_set = {int(value) for value in requested}
    selected = [race for race in parsed if int(race["stadium_number"]) in requested_set]
    with db_connect() as conn:
        upsert_b(conn, selected)
        conn.commit()
    if missing:
        _record_phase_failure(
            OFFICIAL_TASK,
            target,
            now,
            prior,
            source_host="boatrace.jp",
            reason="official_b_incomplete",
            missing_stadiums=missing,
        )
        return False
    _record_phase_success(
        OFFICIAL_TASK,
        target,
        source_host="boatrace.jp",
        stadiums=expected,
        races=len(parsed),
    )
    return True


def collect_openapi(
    target: date,
    now: datetime,
    prior: dict[str, Any],
) -> bool:
    expected, manifest_state = _manifest_stadiums(target)
    if not expected:
        _record_phase_failure(
            OPENAPI_TASK,
            target,
            now,
            prior,
            source_host="boatrace-open-api.github.io",
            reason=f"manifest_{manifest_state}",
        )
        return False
    payload = openapi.fetch_programs(target)
    if not payload:
        _record_phase_failure(
            OPENAPI_TASK,
            target,
            now,
            prior,
            source_host="boatrace-open-api.github.io",
            reason="openapi_unavailable",
            missing_stadiums=expected,
        )
        return False

    races = _openapi_races(payload)
    complete = _complete_openapi_stadiums(races)
    missing = sorted(set(expected) - complete)
    requested = (prior.get("detail") or {}).get("missing_stadiums") or expected
    requested_set = {int(value) for value in requested}
    selected = [
        race
        for race in races
        if int(race.get("race_stadium_number") or 0) in requested_set
    ]
    filtered = deepcopy(payload)
    filtered["programs"] = selected
    filtered.pop("today", None)
    filtered.pop("data", None)
    with db_connect() as conn:
        openapi.upsert_programs(conn, filtered)
        conn.commit()
    if missing:
        _record_phase_failure(
            OPENAPI_TASK,
            target,
            now,
            prior,
            source_host="boatrace-open-api.github.io",
            reason="openapi_incomplete",
            missing_stadiums=missing,
        )
        return False
    _record_phase_success(
        OPENAPI_TASK,
        target,
        source_host="boatrace-open-api.github.io",
        stadiums=expected,
        races=len(races),
    )
    return True


def _attempt_gate(target: date, now: datetime, *, force: bool) -> bool:
    prior = _load_task(GATE_TASK, target)
    if prior.get("status") == "success":
        return True
    if not phase_due(prior, now, force=force):
        return False
    result = check_program_source_gate(target)
    if result.get("gate_status") in {"ready", "ready_with_warning"}:
        _write_task(GATE_TASK, target, "success", result)
        _write_status(target, "ok", "program sources ready", result)
        return True
    _record_phase_failure(
        GATE_TASK,
        target,
        now,
        prior,
        source_host="cross-source-gate",
        reason=str(result.get("reason") or result.get("gate_status") or "blocked"),
        missing_stadiums=[],
    )
    return False


def _at_or_after(now: datetime, hour: int, minute: int) -> bool:
    return now.timetz().replace(tzinfo=None) >= time(hour, minute)


def run_tick(now: datetime) -> dict[str, Any]:
    target = target_for_tick(now)
    if target is None:
        return {"status": "noop", "reason": "outside-bootstrap-window"}

    assert_safe_production_write(action="program bootstrap")
    _ensure_tables()
    with _run_lock() as locked:
        if not locked:
            return {"status": "noop", "reason": "previous-run-active", "date": target.isoformat()}

        final_prior = _load_task(FINAL_TASK, target)
        final_due = (
            now.date() == target
            and _at_or_after(now, 6, 30)
            and final_prior.get("status") != "success"
        )

        official_prior = _load_task(OFFICIAL_TASK, target)
        if phase_due(official_prior, now, force=final_due):
            collect_official(target, now, official_prior)

        openapi_prior = _load_task(OPENAPI_TASK, target)
        openapi_window = now.date() == target and _at_or_after(now, 0, 10)
        if openapi_window and phase_due(openapi_prior, now, force=final_due):
            collect_openapi(target, now, openapi_prior)

        official_ready = _load_task(OFFICIAL_TASK, target).get("status") == "success"
        openapi_ready = _load_task(OPENAPI_TASK, target).get("status") == "success"
        gate_ready = False
        if official_ready and openapi_ready:
            gate_ready = _attempt_gate(target, now, force=final_due)

        if final_due:
            _write_task(
                FINAL_TASK,
                target,
                "success",
                {
                    "official_ready": official_ready,
                    "openapi_ready": openapi_ready,
                    "gate_ready": gate_ready,
                },
            )

        alert_window = now.date() == target and _at_or_after(now, 7, 30)
        alert_prior = _load_task(ALERT_TASK, target)
        alert_due = alert_window and alert_prior.get("status") != "success"
        if alert_due and alert_prior.get("status") != "success":
            detail = {
                "official": _load_task(OFFICIAL_TASK, target),
                "openapi": _load_task(OPENAPI_TASK, target),
                "gate": _load_task(GATE_TASK, target),
            }
            status = "ok" if gate_ready or detail["gate"].get("status") == "success" else "error"
            message = "program sources ready" if status == "ok" else "program sources unresolved at 07:30 JST"
            _write_status(target, status, message, detail)
            _write_task(ALERT_TASK, target, "success", {"reported_status": status})

        gate_status = _load_task(GATE_TASK, target).get("status")
        return {
            "status": "ready" if gate_status == "success" else "waiting",
            "date": target.isoformat(),
            "official_ready": official_ready,
            "openapi_ready": openapi_ready,
            "gate_ready": gate_status == "success",
            "final_recovery": final_due,
            "alert_due": alert_due,
        }


def main(argv: list[str] | None = None) -> int:
    log_deploy_revision("boatrace-program-bootstrap-cron")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--now", help="test-only JST timestamp")
    args = parser.parse_args(argv)
    now = datetime.fromisoformat(args.now).replace(tzinfo=JST) if args.now else jst_now()
    result = run_tick(now)
    print("[program-bootstrap] " + json.dumps(result, ensure_ascii=True, sort_keys=True), flush=True)
    # Publication delays are an expected waiting state. Persisted status and
    # the admin warning report them without marking a healthy cron tick failed.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
