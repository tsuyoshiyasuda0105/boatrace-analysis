"""Run ordered heavy maintenance between 04:00 and 07:00 JST.

The Render trigger runs every ten minutes. Each tick executes only the first
due incomplete phase, so a slow phase cannot overlap later work. Daytime cron
jobs remain limited to live, bounded collection.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, time, timedelta
import json
import os
from pathlib import Path
import sys
from typing import Callable, Iterator


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
os.environ.setdefault("BOATRACE_TASK_TRIGGER", "render-maintenance")

from scripts import render_regular_scheduler as regular  # noqa: E402
from src.db.connection import connect as db_connect  # noqa: E402


JST = regular.JST
LOCK_NAME = "boatrace-maintenance-scheduler-v1"
PHASES: tuple[tuple[str, time], ...] = (
    ("accident", time(4, 0)),
    ("program", time(4, 30)),
    ("motor", time(5, 0)),
    ("detail", time(5, 30)),
    ("snapshot", time(6, 15)),
    ("integrity", time(6, 30)),
)


def jst_now() -> datetime:
    return regular.jst_now()


@contextmanager
def maintenance_lock() -> Iterator[bool]:
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


def task_name(phase: str) -> str:
    return f"render_maintenance_{phase}_v1"


def phase_success(phase: str, run_date: str) -> bool:
    return regular.task_success_exists(task_name(phase), run_date)


def record_phase(phase: str, run_date: str, ok: bool, detail: dict) -> None:
    regular.record_task(
        task_name(phase),
        run_date,
        "success" if ok else "failure",
        detail=json.dumps(detail, ensure_ascii=True, sort_keys=True),
    )


def run_accident_phase(now: datetime) -> tuple[bool, dict]:
    target = regular.latest_completed_results_date() or (
        now.date() - timedelta(days=1)
    ).isoformat()
    target_dt = datetime.fromisoformat(target).replace(tzinfo=JST)
    ok = regular.run_accident_rebuild(regular.accident_period_start(target_dt), target)
    if ok:
        ok = regular.run_accident_rank_snapshot(target)
    if ok:
        ok = regular.run_py(
            ["scripts/check_post_run_integrity.py", "--date", target, "--stage", "nightly"],
            timeout=300,
        )
    return ok, {"target_date": target}


def run_program_phase(now: datetime) -> tuple[bool, dict]:
    today = now.date().isoformat()
    counts = regular.daily_source_counts(today)
    structurally_ready = (
        counts["races"] > 0
        and counts["entries"] >= counts["races"] * 6
        and counts["detail_entries"] >= counts["races"] * 6
    )
    gate_ok = structurally_ready and regular.run_program_source_gate(
        today, allow_official_fallback=True
    )
    prediction_ok = False
    if gate_ok:
        prediction_ok = regular.run_py(
            ["scripts/render_cache_predictions.py", "--date", today], timeout=1800
        )
    refreshed = regular.daily_source_counts(today)
    ok = bool(gate_ok and prediction_ok and regular.daily_source_complete(refreshed))
    return ok, {"before": counts, "after": refreshed, "gate_ok": bool(gate_ok)}


def run_motor_phase(now: datetime) -> tuple[bool, dict]:
    today = now.date().isoformat()
    ok = regular.run_py(
        [
            "scripts/prewarm_race_detail_data.py",
            "--date", today,
            "--phase", "motor",
            "--missing-only",
        ],
        timeout=1800,
    )
    return ok, {"date": today, "mode": "missing-only"}


def run_detail_phase(now: datetime) -> tuple[bool, dict]:
    today = now.date().isoformat()
    tags_ok = regular.run_py(
        ["scripts/prewarm_race_detail_tags.py", "--date", today], timeout=900
    )
    pages_ok = False
    if tags_ok:
        # Tags can change after the accident snapshot, so render every page
        # exactly once inside the maintenance window. The page builder retries
        # only persistent misses internally.
        pages_ok = regular.run_py(
            ["scripts/prewarm_race_detail_pages.py", "--date", today], timeout=1800
        )
    return bool(tags_ok and pages_ok), {
        "date": today,
        "tags_ok": bool(tags_ok),
        "pages_ok": bool(pages_ok),
    }


def run_snapshot_phase(now: datetime) -> tuple[bool, dict]:
    today = now.date().isoformat()
    signal_ok = regular.run_signal_refresh_slot(now, source_gate_verified=True)
    top_ok = signal_ok and regular.run_top_page_snapshot(now, lightweight=False)
    return bool(signal_ok and top_ok), {
        "date": today,
        "signals_ok": bool(signal_ok),
        "top_ok": bool(top_ok),
    }


def run_integrity_phase(now: datetime) -> tuple[bool, dict]:
    today = now.date().isoformat()
    ok = regular.run_py(
        ["scripts/check_post_run_integrity.py", "--date", today, "--stage", "morning"],
        timeout=300,
    )
    return ok, {"date": today, "stage": "morning"}


RUNNERS: dict[str, Callable[[datetime], tuple[bool, dict]]] = {
    "accident": run_accident_phase,
    "program": run_program_phase,
    "motor": run_motor_phase,
    "detail": run_detail_phase,
    "snapshot": run_snapshot_phase,
    "integrity": run_integrity_phase,
}


def run_tick(now: datetime) -> dict:
    if not (4 <= now.hour < 7):
        return {"status": "noop", "reason": "outside-maintenance-window"}
    run_date = now.date().isoformat()
    with maintenance_lock() as locked:
        if not locked:
            return {"status": "noop", "reason": "previous-run-active", "date": run_date}
        for phase, not_before in PHASES:
            if now.timetz().replace(tzinfo=None) < not_before:
                break
            if phase_success(phase, run_date):
                continue
            try:
                ok, detail = RUNNERS[phase](now)
            except Exception as exc:  # noqa: BLE001
                ok = False
                detail = {"error": f"{type(exc).__name__}: {exc}"[:1000]}
            record_phase(phase, run_date, ok, detail)
            return {
                "status": "success" if ok else "waiting",
                "date": run_date,
                "phase": phase,
                "detail": detail,
            }
    return {"status": "ready", "date": run_date}


def main() -> int:
    now = jst_now()
    result = run_tick(now)
    print("[maintenance] " + json.dumps(result, ensure_ascii=True, sort_keys=True), flush=True)
    # Missing/late inputs are expected retry states. Persist the failed phase
    # but keep Render healthy so the next ten-minute tick can resume it.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
