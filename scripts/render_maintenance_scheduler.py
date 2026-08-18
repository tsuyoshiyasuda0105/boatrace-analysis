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
from src.db.cron_runtime import advisory_lock  # noqa: E402
from src.deploy_info import log_deploy_revision  # noqa: E402
from src.notifications.cron_alerts import notify_cron_failure  # noqa: E402


JST = regular.JST
CRON_JOB_NAME = "boatrace-race-detail-cron"
STATUS_NAME = "maintenance_window"
TICK_INTERVAL_MINUTES = 10
LOCK_NAME = "boatrace-maintenance-scheduler-v1"
MAX_PHASE_ATTEMPTS = 3
SCHEDULER_VERSION = "v2"
DETAIL_TAG_BUDGET_SEC = 600
DETAIL_PAGE_BUDGET_SEC = 600
DETAIL_PREWARM_TIMEOUT_SEC = 900
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
    try:
        with advisory_lock(conn, LOCK_NAME) as locked:
            yield locked
    finally:
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


def _child_peak_rss_mb() -> float | None:
    """Return the largest completed-child RSS observed by this Linux process."""
    try:
        import resource

        peak = float(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
        # Linux reports KiB. Render runs Linux; keep other platforms diagnostic-only.
        if not sys.platform.startswith("linux"):
            return None
        return round(peak / 1024.0, 1)
    except (ImportError, OSError, TypeError, ValueError):
        return None


def _run_detail_subprocess(args: list[str], *, timeout: int) -> tuple[bool, dict]:
    """Run one detail child without losing spawn/exit evidence."""
    try:
        result = regular.run_py_detailed(args, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - spawn failures must reach task_runs
        result = regular.PyRunResult(
            None,
            f"spawn_error={type(exc).__name__}: {exc}",
        )
    return result.ok, {
        "return_code": result.returncode,
        "timed_out": bool(result.timed_out),
        "oom_suspected": result.returncode in {-9, 137},
        "stderr_tail": result.stderr_tail[-500:],
        "peak_rss_mb": _child_peak_rss_mb(),
    }


def run_detail_phase(now: datetime) -> tuple[bool, dict]:
    today = now.date().isoformat()
    tags_ok, tags_diagnostic = _run_detail_subprocess(
        [
            "scripts/prewarm_race_detail_tags.py",
            "--date", today,
            "--budget-sec", str(DETAIL_TAG_BUDGET_SEC),
        ],
        timeout=DETAIL_PREWARM_TIMEOUT_SEC,
    )
    # A partial tag refresh must not prevent the primary page prewarm. The final
    # integrity check below distinguishes a real cache gap from an acceptable
    # new-motor warning, so always give every page a chance to render first.
    pages_ok, pages_diagnostic = _run_detail_subprocess(
        [
            "scripts/prewarm_race_detail_pages.py",
            "--date", today,
            "--missing-only",
            "--budget-sec", str(DETAIL_PAGE_BUDGET_SEC),
        ],
        timeout=DETAIL_PREWARM_TIMEOUT_SEC,
    )
    integrity_ok, integrity_diagnostic = _run_detail_subprocess(
        [
            "scripts/check_post_run_integrity.py",
            "--date", today,
            "--scope", "detail_rows",
            "--scope", "motor_cache",
            "--scope", "detail_cache",
            "--warnings-ok",
        ],
        timeout=300,
    )
    coverage: dict[str, int] | None = None
    try:
        coverage = regular.race_detail_page_cache_coverage(today)
    except Exception as exc:  # noqa: BLE001
        print(f"[maintenance-detail] coverage check failed: {type(exc).__name__}: {exc}", flush=True)
    remaining = (
        max(0, coverage["races"] - coverage["covered"])
        if coverage is not None
        else None
    )
    partial = bool(tags_ok and pages_ok and remaining is not None and remaining > 0)
    # Budget exhaustion is an expected partial success. Keep the integrity
    # result visible, but do not block snapshot/integrity phases while the
    # regular self-heal owns the remaining page coverage.
    ok = bool(pages_ok and (integrity_ok or partial))
    return ok, {
        "date": today,
        "tags_ok": bool(tags_ok),
        "pages_ok": bool(pages_ok),
        "integrity_ok": bool(integrity_ok),
        "partial": partial,
        "remaining": remaining,
        "subprocesses": {
            "tags": tags_diagnostic,
            "pages": pages_diagnostic,
            "integrity": integrity_diagnostic,
        },
    }


def run_snapshot_phase(now: datetime) -> tuple[bool, dict]:
    today = now.date().isoformat()
    signal_ok = regular.run_signal_refresh_slot(now, source_gate_verified=True)
    # A failed signal refresh must not suppress the TOP rebuild. The snapshot
    # builder reuses same-day last-good signals (or persisted race badges), so
    # users retain a degraded but useful page while the next slot retries.
    top_ok = regular.run_top_page_snapshot(
        now,
        lightweight=False,
        signals_degraded=not signal_ok,
    )
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


def is_final_window_tick(now: datetime) -> bool:
    """Return True on the last automatic tick (06:50) of the 04:00-07:00 window."""
    if not (4 <= now.hour < 7):
        return False
    return (now + timedelta(minutes=TICK_INTERVAL_MINUTES)).hour >= 7


def _write_window_status(run_date: str, status: str, message: str, detail: dict) -> None:
    """system_status へ窓の最終判定を upsert する (bootstrap の _write_status と同型)。"""
    now_iso = jst_now().replace(tzinfo=None).isoformat(timespec="seconds")
    payload = json.dumps(detail, ensure_ascii=True, sort_keys=True, default=str)
    try:
        with db_connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM system_status WHERE check_name=? AND check_date=?",
                (STATUS_NAME, run_date),
            ).fetchone()
            if exists:
                conn.execute(
                    """
                    UPDATE system_status
                       SET status=?, message=?, detail_json=?, checked_at=?
                     WHERE check_name=? AND check_date=?
                    """,
                    (status, message, payload, now_iso, STATUS_NAME, run_date),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO system_status
                        (check_name, check_date, status, message, detail_json, checked_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (STATUS_NAME, run_date, status, message, payload, now_iso),
                )
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        print(
            f"[maintenance] status write failed: {type(exc).__name__}: {exc}",
            flush=True,
        )


def final_window_failure(now: datetime, result: dict) -> list[str]:
    """最終 tick で未完フェーズが残っていればそのリストを返す (無ければ空)。

    "waiting"/"success" はフェーズを実行した tick の戻り値で incomplete_phases を
    含まないため、task_runs から再判定する。"noop" (ロック競合等) と
    "ready" (全フェーズ完了) は最終失敗ではない。
    """
    if not is_final_window_tick(now):
        return []
    if result.get("status") in {"noop", "ready"}:
        return []
    incomplete = result.get("incomplete_phases")
    if incomplete is None:
        run_date = now.date().isoformat()
        incomplete = [phase for phase, _ in PHASES if not phase_success(phase, run_date)]
    return list(incomplete)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--allow-recovery",
        action="store_true",
        help="Allow a manually triggered recovery run from 07:00 through 11:59 JST.",
    )
    args = parser.parse_args()
    log_deploy_revision(CRON_JOB_NAME)
    now = jst_now()
    result = run_tick(now, allow_recovery=args.allow_recovery)
    print("[maintenance] " + json.dumps(result, ensure_ascii=True, sort_keys=True), flush=True)
    # Missing/late inputs are expected retry states: mid-window ticks return 0
    # so the next ten-minute tick can resume the failed phase. The final tick
    # of the automatic window has no retries left, so incomplete phases become
    # a real cron failure: exit non-zero, record system_status, mail the admin.
    incomplete = final_window_failure(now, result)
    if incomplete:
        run_date = now.date().isoformat()
        message = "maintenance window ended degraded: " + ", ".join(incomplete)
        detail = {
            "incomplete_phases": incomplete,
            "tick_status": result.get("status"),
            "date": run_date,
        }
        _write_window_status(run_date, "error", message, detail)
        try:
            notify_cron_failure(CRON_JOB_NAME, message, detail=detail)
        except Exception as exc:  # noqa: BLE001
            print(
                f"[maintenance] failure mail skipped: {type(exc).__name__}: {exc}",
                flush=True,
            )
        print(f"[maintenance] final-tick failure incomplete={incomplete}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
