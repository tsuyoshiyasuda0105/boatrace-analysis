"""Run ordered heavy maintenance between 04:00 and 07:00 JST.

The Render trigger runs every ten minutes. Each tick executes only the first
due incomplete phase, so a slow phase cannot overlap later work. Daytime cron
jobs remain limited to live, bounded collection.
"""
from __future__ import annotations

import argparse
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
from src.deploy_info import log_deploy_revision  # noqa: E402


JST = regular.JST
LOCK_NAME = "boatrace-maintenance-scheduler-v1"
MAX_PHASE_ATTEMPTS = 3
SCHEDULER_VERSION = "v2"
PHASES: tuple[tuple[str, time], ...] = (
    ("accident", time(4, 0)),
    ("program", time(4, 30)),
    ("motor", time(5, 0)),
    ("detail", time(5, 30)),
    ("snapshot", time(6, 15)),
    ("integrity", time(6, 30)),
)
REQUIRED_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "accident": (),
    "program": (),
    "motor": ("program",),
    "detail": ("program", "motor"),
    "snapshot": ("program", "detail"),
    "integrity": ("program", "motor", "detail", "snapshot"),
}


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


def phase_attempts(phase: str, run_date: str) -> int:
    try:
        with db_connect() as conn:
            row = conn.execute(
                "SELECT run_count, detail FROM task_runs WHERE task_name = ? AND run_date = ?",
                (task_name(phase), run_date),
            ).fetchone()
        if not row:
            return 0
        try:
            detail = json.loads(row[1] or "{}")
        except (TypeError, ValueError):
            detail = {}
        # Legacy failures predate the circuit breaker and must not permanently
        # prevent the repaired scheduler from making its own bounded attempts.
        if detail.get("scheduler_version") != SCHEDULER_VERSION:
            return 0
        return int(detail.get("attempt_count") or 0)
    except Exception as exc:  # noqa: BLE001
        print(f"[maintenance] attempt read failed phase={phase} error={type(exc).__name__}", flush=True)
        return 0


def record_phase(phase: str, run_date: str, ok: bool, detail: dict) -> None:
    versioned_detail = {**detail, "scheduler_version": SCHEDULER_VERSION}
    regular.record_task(
        task_name(phase),
        run_date,
        "success" if ok else "failure",
        detail=json.dumps(versioned_detail, ensure_ascii=True, sort_keys=True),
    )


def run_accident_phase(now: datetime) -> tuple[bool, dict]:
    run_date = now.date().isoformat()
    # This phase closes the previous day's accident ledger. Live results can
    # start arriving during manual morning recovery and must not move its
    # checkpoint target to the current day halfway through the phase.
    target = (now.date() - timedelta(days=1)).isoformat()
    target_dt = datetime.fromisoformat(target).replace(tzinfo=JST)
    detail: dict[str, object] = {"target_date": target}

    rebuild_phase = "accident_rebuild"
    rebuild_ok = phase_success(rebuild_phase, run_date)
    if not rebuild_ok:
        rebuild_ok = regular.run_accident_rebuild(
            regular.accident_period_start(target_dt), target
        )
        record_phase(rebuild_phase, run_date, rebuild_ok, {"target_date": target})
    detail["rebuild_ok"] = bool(rebuild_ok)
    if not rebuild_ok:
        detail["failed_step"] = "rebuild"
        return False, detail

    snapshot_phase = "accident_snapshot"
    snapshot_ok = phase_success(snapshot_phase, run_date)
    if not snapshot_ok:
        snapshot_ok = regular.run_accident_rank_snapshot(target)
        record_phase(snapshot_phase, run_date, snapshot_ok, {"target_date": target})
    detail["snapshot_ok"] = bool(snapshot_ok)
    if not snapshot_ok:
        detail["failed_step"] = "snapshot"
        return False, detail

    integrity_phase = "accident_integrity"
    integrity_ok = phase_success(integrity_phase, run_date)
    if not integrity_ok:
        integrity_ok = regular.run_py(
            ["scripts/check_post_run_integrity.py", "--date", target, "--stage", "nightly"],
            timeout=300,
        )
        record_phase(integrity_phase, run_date, integrity_ok, {"target_date": target})
    detail["integrity_ok"] = bool(integrity_ok)
    if not integrity_ok:
        detail["failed_step"] = "integrity"
    return bool(integrity_ok), detail


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
    run_date = today
    roi_phase = "roi_reconcile"
    roi_ok = phase_success(roi_phase, run_date)
    if not roi_ok:
        roi_ok = regular.run_roi_daily_self_heal(now)
        record_phase(
            roi_phase,
            run_date,
            roi_ok,
            {"target_date": (now.date() - timedelta(days=1)).isoformat()},
        )
    if not roi_ok:
        return False, {
            "date": today,
            "stage": "morning",
            "roi_ok": False,
            "failed_step": "roi_reconcile",
        }
    ok = regular.run_py(
        [
            "scripts/check_post_run_integrity.py",
            "--date", today,
            "--stage", "morning",
            "--warnings-ok",
        ],
        timeout=300,
    )
    return ok, {"date": today, "stage": "morning", "roi_ok": True}


RUNNERS: dict[str, Callable[[datetime], tuple[bool, dict]]] = {
    "accident": run_accident_phase,
    "program": run_program_phase,
    "motor": run_motor_phase,
    "detail": run_detail_phase,
    "snapshot": run_snapshot_phase,
    "integrity": run_integrity_phase,
}


def run_tick(now: datetime, *, allow_recovery: bool = False) -> dict:
    in_automatic_window = 4 <= now.hour < 7
    in_bounded_recovery_window = allow_recovery and 7 <= now.hour < 12
    if not (in_automatic_window or in_bounded_recovery_window):
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
            attempts = phase_attempts(phase, run_date)
            if attempts >= MAX_PHASE_ATTEMPTS:
                print(
                    f"[maintenance] circuit open phase={phase} attempts={attempts}",
                    flush=True,
                )
                continue
            dependencies = REQUIRED_DEPENDENCIES.get(phase, ())
            missing_dependencies = [
                dependency
                for dependency in dependencies
                if not phase_success(dependency, run_date)
            ]
            if missing_dependencies:
                continue
            try:
                ok, detail = RUNNERS[phase](now)
            except Exception as exc:  # noqa: BLE001
                ok = False
                detail = {"error": f"{type(exc).__name__}: {exc}"[:1000]}
            detail["attempt_count"] = attempts + 1
            record_phase(phase, run_date, ok, detail)
            return {
                "status": "success" if ok else "waiting",
                "date": run_date,
                "phase": phase,
                "attempt": attempts + 1,
                "detail": detail,
            }
    incomplete = [phase for phase, _ in PHASES if not phase_success(phase, run_date)]
    return {
        "status": "ready" if not incomplete else "degraded",
        "date": run_date,
        "incomplete_phases": incomplete,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--allow-recovery",
        action="store_true",
        help="Allow a manually triggered recovery run from 07:00 through 11:59 JST.",
    )
    args = parser.parse_args()
    log_deploy_revision("boatrace-race-detail-cron")
    now = jst_now()
    result = run_tick(now, allow_recovery=args.allow_recovery)
    print("[maintenance] " + json.dumps(result, ensure_ascii=True, sort_keys=True), flush=True)
    # Missing/late inputs are expected retry states. Persist the failed phase
    # but keep Render healthy so the next ten-minute tick can resume it.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
