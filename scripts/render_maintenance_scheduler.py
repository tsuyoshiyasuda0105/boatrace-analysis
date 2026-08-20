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
import re
import sqlite3
import sys
from typing import Any, Callable, Iterator
from urllib.request import Request, urlopen


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
PREFLIGHT_STATUS_NAME = "preflight_0640_gate"
PREFLIGHT_ALERT_JOB_NAME = "boatrace-preflight-gate"
TICK_INTERVAL_MINUTES = 10
LOCK_NAME = "boatrace-maintenance-scheduler-v1"
MAX_PHASE_ATTEMPTS = 3
SCHEDULER_VERSION = "v2"
DETAIL_TAG_BUDGET_SEC = 600
DETAIL_PAGE_BUDGET_SEC = 600
DETAIL_PREWARM_TIMEOUT_SEC = 900
PREFLIGHT_TIME = time(6, 40)
PREFLIGHT_GATE_CAP = time(7, 30)
PREFLIGHT_DB_CONNECTION_LIMIT = 45
PHASES: tuple[tuple[str, time], ...] = (
    ("accident", time(4, 0)),
    ("program", time(4, 30)),
    ("motor", time(5, 0)),
    ("detail", time(5, 30)),
    ("snapshot", time(6, 15)),
    ("integrity", time(6, 30)),
    ("preflight", PREFLIGHT_TIME),
)
REQUIRED_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "accident": (),
    "program": (),
    "motor": ("program",),
    "detail": ("program", "motor"),
    "snapshot": ("program", "detail"),
    "integrity": ("program", "motor", "detail", "snapshot"),
    # Preflight is intentionally independent. At 06:40 it measures the real
    # state and owns one-shot repairs even when an earlier phase is degraded.
    "preflight": (),
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
    try:
        entry_change_results = regular.run_entry_change_snapshots_nonfatal(now)
    except Exception as exc:  # noqa: BLE001 - optional data cannot block maintenance
        entry_change_results = {"unexpected_error": False}
        print(
            f"[maintenance] entry-change snapshots skipped nonfatally: {type(exc).__name__}: {exc}",
            flush=True,
        )
    try:
        # 前夜 PC がアップロードした kachisuji デルタを web の slim DB に適用させる
        regular.run_kachisuji_delta_apply_nonfatal(now)
    except Exception as exc:  # noqa: BLE001 - optional data cannot block maintenance
        print(
            f"[maintenance] kachisuji delta apply skipped nonfatally: {type(exc).__name__}: {exc}",
            flush=True,
        )
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
            "entry_change_snapshots": entry_change_results,
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
    return ok, {
        "date": today,
        "stage": "morning",
        "roi_ok": True,
        "entry_change_snapshots": entry_change_results,
    }


def _check(
    check_id: int,
    name: str,
    ok: bool,
    actual: object,
    expected: str,
    *,
    critical: bool = False,
) -> dict[str, object]:
    return {
        "id": check_id,
        "name": name,
        "critical": critical,
        "status": "ok" if ok else "fail",
        "ok": bool(ok),
        "actual": actual,
        "expected": expected,
    }


def evaluate_preflight_checks(
    measurements: dict[str, object],
    *,
    target_date: str,
    yesterday: str,
) -> list[dict[str, object]]:
    """Apply the 13 documented preflight decisions to measured values."""
    races = int(measurements.get("races") or 0)
    entries = int(measurements.get("entries") or 0)
    pages = int(measurements.get("page_cache_count") or 0)
    motors = int(measurements.get("motor_cache_count") or 0)
    tags = int(measurements.get("tag_cache_count") or 0)
    predictions = int(measurements.get("predictions") or 0)
    closed_at = int(measurements.get("race_closed_at_count") or 0)
    signal_exists = bool(measurements.get("signal_cache_exists"))
    signal_pending = bool(measurements.get("signal_cache_pending", True))
    signal_nonempty = bool(measurements.get("signal_cache_nonempty"))
    page_status = measurements.get("today_races_http_status")
    candidate_count = measurements.get("today_races_candidate_count")
    accident_snapshot = measurements.get("accident_snapshot_status")
    accident_integrity = measurements.get("accident_integrity_status")
    accident_status = measurements.get("accident_check_status")
    accident_date = str(measurements.get("accident_check_date") or "")
    backtest_latest = str(measurements.get("backtest_latest_date") or "")
    open_incidents = measurements.get("open_incidents")
    cron_failures = measurements.get("cron_failures_12h")
    healthz_status = measurements.get("healthz_http_status")
    healthz_body_status = measurements.get("healthz_body_status")
    db_connections = measurements.get("db_connections")

    return [
        _check(
            1,
            "today_race_data",
            races > 0 and entries == races * 6,
            {"races": races, "entries": entries},
            "races > 0 and entries == races * 6",
            critical=True,
        ),
        _check(
            2,
            "race_detail_pages",
            races > 0 and pages == races,
            {"races": races, "cached_pages": pages},
            "cached_pages == races",
            critical=True,
        ),
        _check(
            3,
            "motor_information",
            races > 0 and motors == races * 6,
            {"expected": races * 6, "cached_motor_histories": motors},
            "cached_motor_histories == races * 6",
        ),
        _check(
            4,
            "race_tags",
            races > 0 and tags == races and signal_exists and not signal_pending,
            {
                "expected_tags": races,
                "cached_tags": tags,
                "signal_cache_exists": signal_exists,
                "signal_cache_pending": signal_pending,
            },
            "race-detail tags cover every race and today's signal cache is not pending",
        ),
        _check(
            5,
            "today_races_page",
            page_status == 200
            and isinstance(candidate_count, int)
            and candidate_count >= 0,
            {"http_status": page_status, "candidate_count": candidate_count},
            "/member/today-races renders HTTP 200 and reports a candidate count",
            critical=True,
        ),
        _check(
            6,
            "accident_processing",
            accident_snapshot == "success"
            and accident_integrity == "success"
            and accident_status == "ok"
            and yesterday <= accident_date <= target_date,
            {
                "snapshot_task": accident_snapshot,
                "integrity_task": accident_integrity,
                "integrity_status": accident_status,
                "freshness_date": accident_date or None,
            },
            "snapshot/integrity tasks succeed and post_run_accident is ok for today or yesterday",
        ),
        _check(
            7,
            "backtest_yesterday_import",
            bool(backtest_latest) and backtest_latest >= yesterday,
            {"latest_race_date": backtest_latest or None},
            f"kachisuji slim max race_date >= {yesterday}",
        ),
        _check(
            8,
            "predictions",
            races > 0 and predictions == races,
            {"races": races, "prediction_races": predictions},
            "prediction_races == races",
        ),
        _check(
            9,
            "signal_cache_payload",
            signal_exists and signal_nonempty,
            {
                "exists": signal_exists,
                "nonempty": signal_nonempty,
                "signal_count": measurements.get("signal_count"),
                "cache_key": measurements.get("signal_cache_key"),
            },
            "today's signal cache exists with computed_at and a signals object",
        ),
        _check(
            10,
            "race_closed_at",
            races > 0 and closed_at == races,
            {"races": races, "race_closed_at_count": closed_at},
            "race_closed_at_count == races",
        ),
        _check(
            11,
            "incidents_and_cron_failures",
            open_incidents == 0 and cron_failures == 0,
            {"open_incidents": open_incidents, "cron_failures_12h": cron_failures},
            "open incidents == 0 and Render cron failures in last 12h == 0",
        ),
        _check(
            12,
            "healthz",
            healthz_status == 200 and healthz_body_status not in {"error", None},
            {"http_status": healthz_status, "body_status": healthz_body_status},
            "/healthz returns HTTP 200 with a non-error status",
        ),
        _check(
            13,
            "db_connection_headroom",
            isinstance(db_connections, int)
            and db_connections < PREFLIGHT_DB_CONNECTION_LIMIT,
            {"connections": db_connections},
            f"pg_stat_activity connections < {PREFLIGHT_DB_CONNECTION_LIMIT}",
        ),
    ]


def _count_cache_keys(conn: Any, keys: list[str]) -> int:
    if not keys:
        return 0
    count = 0
    for start in range(0, len(keys), 900):
        chunk = keys[start : start + 900]
        placeholders = ",".join("?" for _ in chunk)
        row = conn.execute(
            f"SELECT COUNT(*) FROM page_html_cache WHERE cache_key IN ({placeholders})",
            tuple(chunk),
        ).fetchone()
        count += int((row[0] if row else 0) or 0)
    return count


def _load_signal_cache_measurement(conn: Any, target_date: str) -> dict[str, object]:
    from src.web.app import (  # imported lazily to keep earlier phases light
        _market_signals_cache_key,
        _market_signals_last_good_cache_key,
    )

    current_key = _market_signals_cache_key(target_date)
    last_good_key = _market_signals_last_good_cache_key(target_date)
    rows = conn.execute(
        """
        SELECT cache_key, html
          FROM page_html_cache
         WHERE cache_key = ?
            OR cache_key = ?
            OR cache_key LIKE ?
         ORDER BY CASE WHEN cache_key = ? THEN 0
                       WHEN cache_key = ? THEN 1 ELSE 2 END,
                  updated_at DESC
        """,
        (current_key, last_good_key, f"market_signals:%:{target_date}", current_key, last_good_key),
    ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row[1])
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        data_status = payload.get("data_status") or {}
        pending = bool(
            payload.get("pending")
            or str(payload.get("status") or "").lower() == "pending"
            or data_status.get("cache_miss")
            or data_status.get("cache_only")
        )
        signals = payload.get("signals")
        return {
            "signal_cache_exists": True,
            "signal_cache_pending": pending,
            "signal_cache_nonempty": bool(payload.get("computed_at"))
            and isinstance(signals, dict),
            "signal_count": len(signals) if isinstance(signals, dict) else None,
            "signal_cache_key": str(row[0]),
        }
    return {
        "signal_cache_exists": False,
        "signal_cache_pending": True,
        "signal_cache_nonempty": False,
        "signal_count": None,
        "signal_cache_key": None,
    }


def _probe_today_races_page(target_date: str) -> dict[str, object]:
    """Render the member page in-process without opening the maintenance gate."""
    from src.web.app import create_app

    previous = os.environ.get("BOATRACE_MAINTENANCE_WINDOW")
    os.environ["BOATRACE_MAINTENANCE_WINDOW"] = "0"
    try:
        try:
            app = create_app(cached_predictions_only=True)
            app.testing = True
            client = app.test_client()
            with client.session_transaction() as sess:
                sess["is_member"] = True
                sess["role"] = "admin"
                sess["auth_provider"] = "internal_preflight"
            response = client.get(f"/member/today-races?date={target_date}")
            body = response.get_data(as_text=True)
            match = re.search(r"ROIが高いレース候補\s*(\d+)件", body)
            return {
                "today_races_http_status": response.status_code,
                "today_races_candidate_count": int(match.group(1)) if match else None,
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "today_races_http_status": None,
                "today_races_candidate_count": None,
                "today_races_error": f"{type(exc).__name__}: {exc}"[:500],
            }
    finally:
        if previous is None:
            os.environ.pop("BOATRACE_MAINTENANCE_WINDOW", None)
        else:
            os.environ["BOATRACE_MAINTENANCE_WINDOW"] = previous


def _probe_healthz() -> dict[str, object]:
    base_url = os.environ.get(
        "BOATRACE_SITE_URL", "https://boatrace-web.onrender.com"
    ).rstrip("/")
    request = Request(  # noqa: S310 - fixed operator-configured HTTPS endpoint
        f"{base_url}/healthz",
        headers={"User-Agent": "boatrace-preflight/1.0"},
    )
    try:
        with urlopen(request, timeout=10) as response:  # noqa: S310
            status = int(response.status)
            payload = json.loads(response.read().decode("utf-8"))
        return {
            "healthz_http_status": status,
            "healthz_body_status": payload.get("status") if isinstance(payload, dict) else None,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "healthz_http_status": None,
            "healthz_body_status": None,
            "healthz_error": f"{type(exc).__name__}: {exc}"[:500],
        }


def _kachisuji_latest_date() -> dict[str, object]:
    configured = os.environ.get("KACHISUJI_DB")
    db_path = Path(configured) if configured else REPO / "data" / "kachisuji_slim.db"
    try:
        resolved = db_path.resolve()
        connection = sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True)
        try:
            row = connection.execute(
                "SELECT MAX(race_date) FROM asof_race_features"
            ).fetchone()
        finally:
            connection.close()
        return {
            "backtest_latest_date": str(row[0]) if row and row[0] else None,
            "backtest_db_path": str(resolved),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "backtest_latest_date": None,
            "backtest_db_path": str(db_path),
            "backtest_error": f"{type(exc).__name__}: {exc}"[:500],
        }


def collect_preflight_measurements(now: datetime) -> dict[str, object]:
    target_date = now.date().isoformat()
    yesterday = (now.date() - timedelta(days=1)).isoformat()
    measurements: dict[str, object] = {"measurement_errors": {}}
    errors = measurements["measurement_errors"]
    assert isinstance(errors, dict)

    try:
        with db_connect() as conn:
            row = conn.execute(
                """
                SELECT
                  (SELECT COUNT(*) FROM races WHERE race_date = ?),
                  (SELECT COUNT(*) FROM race_entries e JOIN races r ON r.race_id=e.race_id
                    WHERE r.race_date = ?),
                  (SELECT COUNT(DISTINCT p.race_id) FROM predictions p
                    JOIN races r ON r.race_id=p.race_id WHERE r.race_date = ?),
                  (SELECT COUNT(*) FROM races
                    WHERE race_date = ? AND race_closed_at IS NOT NULL AND race_closed_at <> '')
                """,
                (target_date, target_date, target_date, target_date),
            ).fetchone()
            measurements.update(
                {
                    "races": int(row[0] or 0),
                    "entries": int(row[1] or 0),
                    "predictions": int(row[2] or 0),
                    "race_closed_at_count": int(row[3] or 0),
                }
            )
            race_rows = conn.execute(
                "SELECT race_id FROM races WHERE race_date = ? ORDER BY stadium_number, race_number",
                (target_date,),
            ).fetchall()
            race_ids = [str(row[0]) for row in race_rows]
            from scripts.check_post_run_integrity import MOTOR_CACHE_VERSION
            from src.web.app import _race_detail_page_cache_key, _race_detail_tag_cache_key

            measurements["page_cache_count"] = _count_cache_keys(
                conn, [_race_detail_page_cache_key(race_id) for race_id in race_ids]
            )
            measurements["tag_cache_count"] = _count_cache_keys(
                conn, [_race_detail_tag_cache_key(race_id) for race_id in race_ids]
            )
            measurements["motor_cache_count"] = _count_cache_keys(
                conn,
                [
                    f"motor_history_{MOTOR_CACHE_VERSION}:{race_id}:{boat}"
                    for race_id in race_ids
                    for boat in range(1, 7)
                ],
            )
            measurements.update(_load_signal_cache_measurement(conn, target_date))

            for label, phase in (
                ("accident_snapshot_status", "accident_snapshot"),
                ("accident_integrity_status", "accident_integrity"),
            ):
                task_row = conn.execute(
                    "SELECT status FROM task_runs WHERE task_name = ? AND run_date = ?",
                    (task_name(phase), target_date),
                ).fetchone()
                measurements[label] = str(task_row[0]) if task_row else None
            accident_row = conn.execute(
                """
                SELECT check_date, status
                  FROM system_status
                 WHERE check_name = 'post_run_accident'
                   AND check_date IN (?, ?)
                 ORDER BY check_date DESC
                 LIMIT 1
                """,
                (target_date, yesterday),
            ).fetchone()
            measurements["accident_check_date"] = (
                str(accident_row[0]) if accident_row else None
            )
            measurements["accident_check_status"] = (
                str(accident_row[1]) if accident_row else None
            )
            open_row = conn.execute(
                "SELECT COUNT(*) FROM incident_log WHERE status IN ('open', 'investigating')"
            ).fetchone()
            measurements["open_incidents"] = int((open_row[0] if open_row else 0) or 0)
            cutoff = (now - timedelta(hours=12)).replace(tzinfo=None).isoformat(timespec="seconds")
            failure_row = conn.execute(
                """
                SELECT COUNT(*)
                  FROM task_runs
                 WHERE task_name LIKE 'render_%'
                   AND status = 'failure'
                   AND COALESCE(finished_at, started_at, success_at) >= ?
                """,
                (cutoff,),
            ).fetchone()
            measurements["cron_failures_12h"] = int(
                (failure_row[0] if failure_row else 0) or 0
            )
            try:
                connection_row = conn.execute(
                    "SELECT COUNT(*) FROM pg_stat_activity"
                ).fetchone()
                measurements["db_connections"] = int(
                    (connection_row[0] if connection_row else 0) or 0
                )
            except Exception as exc:  # noqa: BLE001
                measurements["db_connections"] = None
                errors["db_connections"] = f"{type(exc).__name__}: {exc}"[:500]
    except Exception as exc:  # noqa: BLE001
        errors["primary_db"] = f"{type(exc).__name__}: {exc}"[:500]

    for values in (_kachisuji_latest_date(), _probe_today_races_page(target_date), _probe_healthz()):
        measurements.update(values)
    return measurements


def _run_preflight_signal_generation(now: datetime) -> tuple[bool, dict[str, object]]:
    return _run_detail_subprocess(
        [
            "scripts/prewarm_strategy_pages.py",
            "--mode", "realtime",
            "--date", now.date().isoformat(),
        ],
        timeout=1800,
    )


def _repair_critical_failures(
    now: datetime, failed_check_ids: list[int]
) -> list[dict[str, object]]:
    repairs: list[dict[str, object]] = []
    jobs: list[str] = []
    for check_id in failed_check_ids:
        job = {1: "program", 2: "detail_pages", 5: "today_candidates"}.get(check_id)
        if job and job not in jobs:
            jobs.append(job)

    for job in jobs:
        try:
            if job == "program":
                ok, detail = run_program_phase(now)
            elif job == "detail_pages":
                ok, detail = _run_detail_subprocess(
                    [
                        "scripts/prewarm_race_detail_pages.py",
                        "--date", now.date().isoformat(),
                        "--missing-only",
                        "--budget-sec", str(DETAIL_PAGE_BUDGET_SEC),
                    ],
                    timeout=DETAIL_PREWARM_TIMEOUT_SEC,
                )
            else:
                ok, detail = _run_preflight_signal_generation(now)
        except Exception as exc:  # noqa: BLE001
            ok = False
            detail = {"error": f"{type(exc).__name__}: {exc}"[:500]}
        repairs.append({"job": job, "ok": bool(ok), "detail": detail})
    return repairs


def _preflight_gate_enabled() -> bool:
    return str(os.environ.get("BOATRACE_PREFLIGHT_GATE", "0")).strip().lower() in {
        "1", "true", "yes", "on",
    }


def run_preflight_phase(now: datetime) -> tuple[bool, dict]:
    target_date = now.date().isoformat()
    yesterday = (now.date() - timedelta(days=1)).isoformat()
    generation_ok, generation_detail = _run_preflight_signal_generation(now)
    initial_measurements = collect_preflight_measurements(now)
    initial_checks = evaluate_preflight_checks(
        initial_measurements, target_date=target_date, yesterday=yesterday
    )
    initial_critical = [
        int(item["id"])
        for item in initial_checks
        if item["critical"] and not item["ok"]
    ]
    repairs = _repair_critical_failures(now, initial_critical) if initial_critical else []
    if repairs:
        final_measurements = collect_preflight_measurements(now)
        final_checks = evaluate_preflight_checks(
            final_measurements, target_date=target_date, yesterday=yesterday
        )
    else:
        final_measurements = initial_measurements
        final_checks = initial_checks

    failed = [item for item in final_checks if not item["ok"]]
    critical_failed = [item for item in failed if item["critical"]]
    noncritical_failed = [item for item in failed if not item["critical"]]
    extend = bool(critical_failed and _preflight_gate_enabled())
    status = "error" if critical_failed else (
        "warning" if noncritical_failed or not generation_ok else "ok"
    )
    detail = {
        "date": target_date,
        "started_for": "06:40 JST",
        "signal_generation": {"ok": bool(generation_ok), **generation_detail},
        "initial_checks": initial_checks if repairs else None,
        "checks": final_checks,
        "repairs": repairs,
        "summary": {
            "ok": len(final_checks) - len(failed),
            "failed": len(failed),
            "failed_check_ids": [int(item["id"]) for item in failed],
            "critical_failed_check_ids": [int(item["id"]) for item in critical_failed],
            "noncritical_failed_check_ids": [int(item["id"]) for item in noncritical_failed],
        },
        "gate": {
            "env_enabled": _preflight_gate_enabled(),
            "extend_maintenance": extend,
            "release_at": f"{target_date}T{PREFLIGHT_GATE_CAP.isoformat(timespec='minutes')}:00+09:00"
            if extend
            else None,
            "hard_cap": "07:30 JST",
        },
        "measurements": final_measurements,
    }
    message = (
        f"preflight {status}: ok={detail['summary']['ok']}/13 "
        f"failed={detail['summary']['failed']} gate_extended={extend}"
    )
    _write_preflight_status(target_date, status, message, detail)
    if failed or not generation_ok:
        try:
            notify_cron_failure(
                PREFLIGHT_ALERT_JOB_NAME,
                message,
                detail={
                    "failed_checks": failed,
                    "signal_generation_ok": bool(generation_ok),
                    "repairs": repairs,
                    "gate": detail["gate"],
                },
                incident_category="preflight_failure",
            )
        except Exception as exc:  # noqa: BLE001
            print(
                f"[preflight] alert skipped: {type(exc).__name__}: {exc}",
                flush=True,
            )
    # The phase itself completed even when checks found an operational issue.
    # This prevents the ten-minute scheduler from performing extra repairs.
    return True, detail


RUNNERS: dict[str, Callable[[datetime], tuple[bool, dict]]] = {
    "accident": run_accident_phase,
    "program": run_program_phase,
    "motor": run_motor_phase,
    "detail": run_detail_phase,
    "snapshot": run_snapshot_phase,
    "integrity": run_integrity_phase,
    "preflight": run_preflight_phase,
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
        phases = PHASES
        current_time = now.timetz().replace(tzinfo=None)
        if PREFLIGHT_TIME <= current_time < time(7, 0):
            # 06:40 is a release gate, so it must not sit behind a late retry.
            phases = (
                ("preflight", PREFLIGHT_TIME),
                *(item for item in PHASES if item[0] != "preflight"),
            )
        for phase, not_before in phases:
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


def _write_status(
    check_name: str,
    run_date: str,
    status: str,
    message: str,
    detail: dict,
) -> None:
    """Upsert one existing-schema system_status row."""
    now_iso = jst_now().replace(tzinfo=None).isoformat(timespec="seconds")
    payload = json.dumps(detail, ensure_ascii=True, sort_keys=True, default=str)
    try:
        with db_connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM system_status WHERE check_name=? AND check_date=?",
                (check_name, run_date),
            ).fetchone()
            if exists:
                conn.execute(
                    """
                    UPDATE system_status
                       SET status=?, message=?, detail_json=?, checked_at=?
                     WHERE check_name=? AND check_date=?
                    """,
                    (status, message, payload, now_iso, check_name, run_date),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO system_status
                        (check_name, check_date, status, message, detail_json, checked_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (check_name, run_date, status, message, payload, now_iso),
                )
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        print(
            f"[maintenance] status write failed: {type(exc).__name__}: {exc}",
            flush=True,
        )


def _write_window_status(run_date: str, status: str, message: str, detail: dict) -> None:
    """Persist the final maintenance-window decision."""
    _write_status(STATUS_NAME, run_date, status, message, detail)


def _write_preflight_status(run_date: str, status: str, message: str, detail: dict) -> None:
    """Persist the 13-check release-gate result for Web and /healthz."""
    _write_status(PREFLIGHT_STATUS_NAME, run_date, status, message, detail)


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
