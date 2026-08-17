from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from functools import wraps
import os
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if __name__ == "__main__":
    os.environ.setdefault("BOATRACE_TASK_TRIGGER", "render-cron")

from src.db.connection import connect as db_connect
from src.db.cron_runtime import (
    ensure_task_runs_table as ensure_task_runs_table_on_connection,
    find_missing_original_exhibition_races as find_missing_original_exhibition_races_common,
    parse_race_close_jst as _parse_race_close_jst,
    reap_stale_running_tasks,
    record_task_run,
)
from src.roi_contract import ROI_DAILY_CACHE_VERSION, strategy_definition_signature
from src.deploy_info import log_deploy_revision
from src.notifications.cron_alerts import notify_cron_failure
import config


REPO = Path(__file__).resolve().parents[1]
JST = timezone(timedelta(hours=9))
BEFOREINFO_WINDOW_MIN = 5
BEFOREINFO_WINDOW_MAX = 9
BEFOREINFO_COOLDOWN_MIN = 8
BEFOREINFO_WRITE_BATCH_SIZE = 6
ORIGINAL_EXHIBITION_RECOVERY_PAST_MIN = 240
ORIGINAL_EXHIBITION_RECOVERY_FUTURE_MIN = 30
ORIGINAL_EXHIBITION_RECOVERY_LIMIT = 48
ORIGINAL_EXHIBITION_CATCHUP_PAST_MIN = 36 * 60
ORIGINAL_EXHIBITION_CATCHUP_FUTURE_MIN = 30
ORIGINAL_EXHIBITION_CATCHUP_LIMIT = 96
DETAIL_SELFHEAL_TAG_BUDGET_SEC = 240
DETAIL_SELFHEAL_PAGE_BUDGET_SEC = 240
DETAIL_SELFHEAL_TIMEOUT_SEC = 360
DETAIL_SELFHEAL_MIN_INTERVAL_MINUTES = 30
WATCHDOG_CACHE_MIN_COVERAGE = 0.5
WATCHDOG_RESULT_MISSING_THRESHOLD = 3
WATCHDOG_FAILURE_RUN_COUNT_THRESHOLD = 3
WATCHDOG_FAILURE_LOOKBACK_HOURS = 6
WATCHDOG_POOL_EVENT_THRESHOLD = 3
WATCHDOG_POOL_LOOKBACK_MINUTES = 30
WATCHDOG_STALE_RUNNING_HOURS = 6
WATCHDOG_ALERT_COOLDOWN_HOURS = 24.0
WATCHDOG_STATUS_PREFIX = "cron_watchdog_"
REGULAR_CRON_JOB_NAME = "boatrace-regular-cron"
REGULAR_RUN_LOCK_NAME = "boatrace-regular-scheduler-v1"
_TASK_RUNS_SCHEMA_READY = False


def jst_now() -> datetime:
    return datetime.now(tz=JST)


def render_daytime_lite_mode() -> bool:
    return os.getenv("BOATRACE_RENDER_DAYTIME_LITE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def reap_stale_task_runs(now: datetime) -> int:
    """Best-effort cleanup; a cleanup failure must not stop the regular tick."""
    try:
        with db_connect() as conn:
            reaped = reap_stale_running_tasks(conn, now=now)
    except Exception as exc:
        print(f"[render-regular] stale-running reaper warning: {exc}", flush=True)
        return 0
    if reaped:
        print(f"[render-regular] stale-running reaped={reaped}", flush=True)
    return reaped


def _notify_failure_best_effort(
    job: str,
    message: str,
    *,
    detail: dict[str, Any] | None = None,
    cooldown_hours: float | None = None,
    incident_category: str = "cron_failure",
) -> None:
    """Send an alert without ever changing the cron's control flow."""
    try:
        kwargs: dict[str, Any] = {"detail": detail or {}}
        if cooldown_hours is not None:
            kwargs["cooldown_hours"] = cooldown_hours
        kwargs["incident_category"] = incident_category
        notify_cron_failure(job, message, **kwargs)
    except Exception as exc:  # noqa: BLE001
        print(
            f"[render-regular] failure mail skipped: {type(exc).__name__}: {exc}",
            flush=True,
        )


@contextmanager
def _regular_run_lock() -> Iterator[bool]:
    ensure_task_runs_table()
    locked = True
    now = jst_now()
    lease_detail = json.dumps({"lease_seconds": 1800, "pid": os.getpid(), "started_at": now.isoformat()})
    conn = db_connect()
    is_postgres = getattr(conn, "_kind", "sqlite") == "postgres"
    if is_postgres:
        now_iso = now.replace(tzinfo=None).isoformat(timespec="seconds")
        stale_iso = (now - timedelta(minutes=30)).replace(tzinfo=None).isoformat(timespec="seconds")
        try:
            row = conn.execute(
                """
                INSERT INTO task_runs
                    (task_name, run_date, status, run_count, started_at, trigger, detail)
                VALUES (?, ?, 'running', 1, ?, 'render-cron-lock', ?)
                ON CONFLICT (task_name, run_date) DO UPDATE SET
                    status = 'running',
                    run_count = task_runs.run_count + 1,
                    started_at = EXCLUDED.started_at,
                    finished_at = NULL,
                    trigger = EXCLUDED.trigger,
                    detail = EXCLUDED.detail
                WHERE task_runs.status <> 'running'
                   OR task_runs.started_at IS NULL
                   OR task_runs.started_at < ?
                RETURNING 1
                """,
                (
                    REGULAR_RUN_LOCK_NAME,
                    now.date().isoformat(),
                    now_iso,
                    lease_detail,
                    stale_iso,
                ),
            ).fetchone()
            locked = bool(row and row[0])
        finally:
            conn.close()
    else:
        conn.close()
    try:
        yield locked
    finally:
        if is_postgres and locked:
            finished_iso = jst_now().replace(tzinfo=None).isoformat(timespec="seconds")
            with db_connect() as release_conn:
                release_conn.execute(
                    """
                    UPDATE task_runs
                       SET status = 'success', finished_at = ?, success_at = ?
                     WHERE task_name = ? AND run_date = ? AND status = 'running'
                       AND detail = ?
                    """,
                    (
                        finished_iso,
                        finished_iso,
                        REGULAR_RUN_LOCK_NAME,
                        now.date().isoformat(),
                        lease_detail,
                    ),
                )


def _with_regular_run_lock(func: Callable[[], int]) -> Callable[[], int]:
    @wraps(func)
    def wrapped() -> int:
        try:
            with _regular_run_lock() as locked:
                if not locked:
                    print("[render-regular] skip: previous run active", flush=True)
                    return 0
                return func()
        except Exception as exc:
            _notify_failure_best_effort(
                REGULAR_CRON_JOB_NAME,
                f"regular cron raised {type(exc).__name__}: {exc}"[:500],
                detail={"error_type": type(exc).__name__, "error": str(exc)[:1000]},
            )
            raise

    return wrapped


@dataclass(frozen=True)
class PyRunResult:
    returncode: int | None
    stderr_tail: str = ""
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out


def _stderr_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


def run_py_detailed(args: list[str], timeout: int = 1800) -> PyRunResult:
    """Run a Python child while retaining enough failure evidence for task_runs."""
    cmd = [sys.executable, *args]
    print("$ " + " ".join(args), flush=True)
    started = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=REPO,
            timeout=timeout,
            check=False,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
        )
        stderr = _stderr_text(proc.stderr)
        if stderr:
            print(stderr, file=sys.stderr, end="" if stderr.endswith("\n") else "\n", flush=True)
        result = PyRunResult(proc.returncode, stderr[-800:])
    except subprocess.TimeoutExpired as exc:
        stderr = _stderr_text(exc.stderr)
        if stderr:
            print(stderr, file=sys.stderr, end="" if stderr.endswith("\n") else "\n", flush=True)
        result = PyRunResult(None, stderr[-800:], timed_out=True)
    elapsed = time.monotonic() - started
    exit_label = "timeout" if result.timed_out else str(result.returncode)
    print(f"exit={exit_label} elapsed={elapsed:.1f}s", flush=True)
    return result


def run_py(args: list[str], timeout: int = 1800) -> bool:
    return run_py_detailed(args, timeout=timeout).ok


def _subprocess_failure_detail(result: PyRunResult) -> str:
    exit_code = "timeout" if result.timed_out else str(result.returncode)
    oom_suspected = result.returncode in {-9, 137}
    stderr_tail = result.stderr_tail.strip() or "<empty>"
    return (
        f"exit_code={exit_code} oom_suspected={str(oom_suspected).lower()} "
        f"stderr_tail={stderr_tail}"
    )[-1200:]


def run_program_source_gate(run_date: str, *, allow_official_fallback: bool = False) -> bool:
    task = (
        "render_program_source_gate_official_fallback_v1"
        if allow_official_fallback
        else "render_program_source_gate_v1"
    )
    if task_success_exists(task, run_date):
        return True
    args = ["scripts/check_program_source_gate.py", "--date", run_date]
    if allow_official_fallback:
        args.append("--allow-official-fallback")
    ok = run_py(args, timeout=120)
    record_task(task, run_date, "success" if ok else "failure")
    return ok


def find_missing_original_exhibition_races(
    now: datetime,
    *,
    target_date: str | None = None,
    past_min: int = ORIGINAL_EXHIBITION_RECOVERY_PAST_MIN,
    future_min: int = ORIGINAL_EXHIBITION_RECOVERY_FUTURE_MIN,
    limit: int = ORIGINAL_EXHIBITION_RECOVERY_LIMIT,
) -> list[tuple[str, int, int, datetime]]:
    return find_missing_original_exhibition_races_common(
        now,
        target_date=target_date,
        past_min=past_min,
        future_min=future_min,
        limit=limit,
        connect=db_connect,
    )


def original_exhibition_daily_counts(target_date: str) -> dict[str, int]:
    from src.collectors import original_exhibition as original_exhibition_collector

    supported = sorted(
        int(stadium)
        for stadium, patterns in original_exhibition_collector.SOURCE_PATTERNS.items()
        if patterns
    )
    if not supported:
        return {"expected_races": 0, "imported_races": 0, "rows": 0}

    placeholders = ",".join("?" for _ in supported)
    with db_connect() as conn:
        row = conn.execute(
            f"""
            SELECT
              (SELECT COUNT(*)
                 FROM races
                WHERE race_date = ?
                  AND stadium_number IN ({placeholders})) AS expected_races,
              (SELECT COUNT(DISTINCT oe.race_id)
                 FROM race_original_exhibitions oe
                 JOIN races r ON r.race_id = oe.race_id
                WHERE r.race_date = ?
                  AND r.stadium_number IN ({placeholders})) AS imported_races,
              (SELECT COUNT(*)
                 FROM race_original_exhibitions oe
                 JOIN races r ON r.race_id = oe.race_id
                WHERE r.race_date = ?
                  AND r.stadium_number IN ({placeholders})) AS rows
            """,
            (target_date, *supported, target_date, *supported, target_date, *supported),
        ).fetchone()
    return {
        "expected_races": int(row[0] or 0),
        "imported_races": int(row[1] or 0),
        "rows": int(row[2] or 0),
    }


def race_count_for_date(target_date: str) -> int:
    try:
        with db_connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM races WHERE race_date = ?",
                (target_date,),
            ).fetchone()
        return int(row[0] or 0) if row else 0
    except Exception as exc:
        print(f"[race-count] lookup failed date={target_date} error={type(exc).__name__}: {exc}", flush=True)
        return 0


def entry_change_snapshot_row_count(target_date: str) -> int | None:
    try:
        with db_connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*)
                  FROM racer_entry_change_snapshots
                 WHERE snapshot_date = ?
                """,
                (target_date,),
            ).fetchone()
        return int(row[0] or 0) if row else 0
    except Exception as exc:
        if "no such table" in str(exc).lower():
            print(f"[entry-change] row-count unavailable date={target_date} reason=missing-table", flush=True)
            return None
        print(f"[entry-change] row-count failed date={target_date} error={type(exc).__name__}: {exc}", flush=True)
        return None


def run_entry_change_snapshot(target_date: str) -> bool:
    race_count = race_count_for_date(target_date)
    if race_count <= 0:
        print(f"[entry-change] skip date={target_date} reason=no-races", flush=True)
        record_task("render_entry_change_snapshot", target_date, "success", detail="skip:no-races")
        return True
    ok = run_py(["scripts/build_racer_entry_change_stats.py", "--date", target_date], timeout=900)
    row_count = entry_change_snapshot_row_count(target_date) if ok else 0
    verified = bool(ok and (row_count is None or row_count > 0))
    print(
        f"[entry-change] date={target_date} races={race_count} rows={row_count} verified={verified}",
        flush=True,
    )
    record_task(
        "render_entry_change_snapshot",
        target_date,
        "success" if verified else "failure",
        detail=f"races={race_count} rows={row_count} build_ok={ok}",
    )
    return verified


def task_success_exists(task_name: str, run_date: str) -> bool:
    try:
        with db_connect() as conn:
            row = conn.execute(
                """
                SELECT success_at
                  FROM task_runs
                 WHERE task_name = ?
                   AND run_date = ?
                """,
                (task_name, run_date),
            ).fetchone()
        return bool(row and row[0])
    except Exception as exc:
        print(f"[task_runs] read failed: {type(exc).__name__}: {exc}", flush=True)
        return False


def signal_refresh_task_name(now: datetime) -> str:
    slot = now.minute // 5
    return f"render_signal_refresh_{now.hour:02d}_{slot}"


def roi_history_task_name(now: datetime) -> str:
    slot_hour = 0 if now.hour < 12 else 12
    return f"render_roi_history_{slot_hour:02d}"


def ensure_task_runs_table() -> None:
    global _TASK_RUNS_SCHEMA_READY
    if _TASK_RUNS_SCHEMA_READY:
        return
    with db_connect() as conn:
        ensure_task_runs_table_on_connection(conn)
    _TASK_RUNS_SCHEMA_READY = True


def record_task(task_name: str, run_date: str, status: str, detail: str | None = None) -> None:
    try:
        with db_connect() as conn:
            record_task_run(
                conn,
                task_name,
                run_date,
                status,
                detail=detail,
                increment=True,
                trigger="render-cron",
            )
    except Exception as exc:
        print(f"[task_runs] write failed: {type(exc).__name__}: {exc}", flush=True)


def signal_refresh_recently_running(now: datetime, max_age_minutes: int = 20) -> bool:
    """Return True when a previous signal refresh is still considered active.

    Render cron jobs can overlap when a five-minute run takes longer than the
    interval.  The market-signal recompute is the expensive part of the live
    loop, so use the shared task_runs table as a coarse cross-process lock.
    """
    today = now.date().isoformat()
    since = (now - timedelta(minutes=max_age_minutes)).replace(
        tzinfo=None,
    ).isoformat(timespec="seconds")
    try:
        with db_connect() as conn:
            row = conn.execute(
                """
                SELECT task_name, started_at
                  FROM task_runs
                 WHERE run_date = ?
                   AND substr(task_name, 1, 22) = 'render_signal_refresh_'
                   AND status = 'running'
                   AND started_at >= ?
                 ORDER BY started_at DESC
                 LIMIT 1
                """,
                (today, since),
            ).fetchone()
        if row:
            print(
                f"[signal-refresh] previous run still active task={row[0]} started_at={row[1]}",
                flush=True,
            )
            return True
    except Exception as exc:
        print(f"[signal-refresh] lock check failed: {type(exc).__name__}: {exc}", flush=True)
    return False


def run_beforeinfo(now: datetime) -> bool:
    from scripts.scrape_beforeinfo_live import (
        find_due_races,
        find_recent_incomplete_races,
        _merge_due_races,
        scrape_one_race,
        write_updates,
    )
    from src.collectors import original_exhibition as original_exhibition_collector
    from src.collectors import tide as tide_collector

    # 実運用は「レース5分前取得」を基準にしつつ、
    # cron の数分ズレを吸収するため 5-9 分前を取得窓にする。
    due = find_due_races(
        now,
        window_min=BEFOREINFO_WINDOW_MIN,
        window_max=BEFOREINFO_WINDOW_MAX,
        cooldown_min=BEFOREINFO_COOLDOWN_MIN,
    )
    incomplete_due = find_recent_incomplete_races(now, past_min=900, future_min=20, limit=24)
    if incomplete_due:
        print(f"[beforeinfo] incomplete_due={len(incomplete_due)}", flush=True)
    # Do not call the market-signals evaluator here. It may trigger a heavy ROI
    # recomputation before the first preview row is saved. Morning candidates are
    # already displayed without exhibition data; live collection only needs the
    # close-time window plus a bounded recovery queue.
    due = _merge_due_races(due, incomplete_due)
    original_due = find_missing_original_exhibition_races(now)
    if original_due:
        print(f"[original-exhibition] missing_due={len(original_due)}", flush=True)
    original_due = _merge_due_races(due, original_due)
    print(f"[beforeinfo] due={len(due)}", flush=True)
    if not due and not original_due:
        return True

    if due:
        try:
            tide_summary = tide_collector.refresh_tides_for_races(
                [race_id for race_id, _stadium, _race_no, _close in due]
            )
            print(
                "[beforeinfo-tides] "
                f"target={tide_summary.get('target_races', 0)} "
                f"rows={tide_summary.get('rows', 0)} "
                f"stations={tide_summary.get('stations', 0)} "
                f"failures={tide_summary.get('station_failures', 0)}",
                flush=True,
            )
        except Exception as exc:
            print(f"[beforeinfo-tides] failed: {type(exc).__name__}: {exc}", flush=True)

    original_ok = True
    try:
        s = original_exhibition_collector.collect_for_races(
            now.date(),
            [(race_id, stadium, race_no) for race_id, stadium, race_no, _close in original_due],
            force=False,
            save_html=False,
        )
        print(
            "[original-exhibition] "
            f"targeted={s['races_targeted']} fetched={s['pages_fetched']} "
            f"found={s['races_found']} rows={s['rows_inserted']}",
            flush=True,
        )
    except Exception as exc:
        original_ok = False
        print(f"[original-exhibition] failed: {type(exc).__name__}: {exc}", flush=True)

    if not due:
        return original_ok

    updates = []
    summary = {"supabase_rows": 0, "local_rows": 0, "races": 0}

    def flush_updates() -> None:
        if not updates:
            return
        batch_summary = write_updates(
            updates,
            datetime.now().isoformat(timespec="seconds"),
            also_local=False,
        )
        for key in summary:
            summary[key] += int(batch_summary.get(key, 0) or 0)
        updates.clear()

    for race_id, stadium, race_no, close in due:
        print(f"[beforeinfo] scrape {race_id} close={close.strftime('%H:%M')}", flush=True)
        page = scrape_one_race(stadium, race_no, now.date())
        if page:
            updates.append((race_id, page))
            if len(updates) >= BEFOREINFO_WRITE_BATCH_SIZE:
                flush_updates()

    flush_updates()

    # Some venue-specific original exhibition pages appear a little later than
    # the official beforeinfo page. Retry once after the live scrape writes.
    try:
        s = original_exhibition_collector.collect_for_races(
            now.date(),
            [(race_id, stadium, race_no) for race_id, stadium, race_no, _close in original_due],
            force=False,
            save_html=False,
        )
        if s.get("races_targeted", 0):
            print(
                "[original-exhibition-retry] "
                f"targeted={s['races_targeted']} fetched={s['pages_fetched']} "
                f"found={s['races_found']} rows={s['rows_inserted']}",
                flush=True,
            )
    except Exception as exc:
        original_ok = False
        print(f"[original-exhibition-retry] failed: {type(exc).__name__}: {exc}", flush=True)

    if summary["races"] <= 0:
        print("[beforeinfo] no valid pages", flush=True)
        return False

    print(f"[beforeinfo] written={summary}", flush=True)
    if summary.get("races", 0) > 0:
        # The dedicated five-minute signal cron consumes these rows. Keeping
        # candidate generation out of this collector prevents overlapping the
        # next regular scheduler run.
        return run_py(
            ["scripts/render_cache_predictions.py", "--date", now.date().isoformat()],
            timeout=1800,
        )
    return True


def run_top_page_snapshot(
    now: datetime,
    *,
    lightweight: bool,
    environment_only: bool = False,
    signals_degraded: bool = False,
) -> bool:
    today = now.date().isoformat()
    args = ["scripts/build_top_page_snapshot.py", "--date", today]
    if lightweight:
        args.append("--lightweight")
    if environment_only:
        args.append("--environment-only")
    if signals_degraded:
        args.append("--signals-degraded")
    ok = run_py(args, timeout=900)
    record_task(
        (
            "render_top_snapshot_environment"
            if environment_only
            else "render_top_snapshot_lightweight"
            if lightweight
            else "render_top_snapshot_full"
        ),
        today,
        "success" if ok else "failure",
    )
    return ok


def run_lite_daytime_bootstrap(now: datetime) -> bool:
    """Run at most one lightweight recovery attempt per daytime hour.

    Full-day racer/motor/tag/page generation belongs to the 04:00-07:00
    maintenance scheduler. Daytime recovery may validate sources, refresh the
    signal snapshot, and rebuild the lightweight TOP snapshot only.
    """

    today = now.date().isoformat()
    task = "render_lite_daytime_bootstrap"
    attempt_task = f"render_lite_daytime_recovery_{now.hour:02d}"
    if not run_yesterday_results_backfill(now):
        record_task(task, today, "failure", detail="results_backfill_failed")
        record_task(attempt_task, today, "failure", detail="results_backfill_failed")
        return False
    if task_success_exists(task, today):
        # The broad bootstrap may already be complete while budgeted detail
        # prewarm still has remaining races. Coverage + cooldown inside this
        # helper decide whether another bounded slice is due.
        return run_detail_pages_selfheal(now)
    if task_attempt_exists(attempt_task, today):
        print(f"[lite-bootstrap] hourly attempt already completed hour={now.hour:02d}", flush=True)
        return True

    try:
        source_counts = daily_source_counts(today)
    except Exception as exc:
        print(f"[lite-bootstrap] source recheck failed: {type(exc).__name__}: {exc}", flush=True)
        record_task(task, today, "failure", detail="source_recheck_failed")
        return False
    if not daily_source_complete(source_counts):
        print(f"[lite-bootstrap] source incomplete -> skip downstream prewarm: {source_counts}", flush=True)
        record_task(task, today, "failure", detail=f"source_incomplete={source_counts}")
        record_task(attempt_task, today, "failure", detail=f"source_incomplete={source_counts}")
        return False

    source_recovery_ok = task_success_exists("render_program_source_gate_v1", today)
    if not source_recovery_ok:
        # The dedicated collector may still be inside its source backoff even
        # after another canonical collector has completed today's rows.
        source_recovery_ok = run_program_source_gate(
            today,
            allow_official_fallback=True,
        )
    if not source_recovery_ok:
        print("[lite-bootstrap] source gate not ready -> skip downstream prewarm", flush=True)
        record_task(task, today, "failure", detail="source_gate_not_ready")
        record_task(attempt_task, today, "failure", detail="source_gate_not_ready")
        return False

    ok = run_signal_refresh_slot(now, source_gate_verified=True)
    if not ok:
        record_task(task, today, "failure", detail="signal_refresh_failed")
        record_task(attempt_task, today, "failure", detail="signal_refresh_failed")
        return False

    detail_selfheal_ok = run_detail_pages_selfheal(now)
    if not detail_selfheal_ok:
        record_task(task, today, "failure", detail="detail_selfheal_failed")
        record_task(attempt_task, today, "failure", detail="detail_selfheal_failed")
        return False

    snapshot_ok = run_top_page_snapshot(now, lightweight=True)
    ok &= snapshot_ok
    record_task(task, today, "success" if ok else "failure")
    record_task(attempt_task, today, "success" if ok else "failure")
    return ok


def _race_detail_page_cache_coverage_on_connection(conn: Any, run_date: str) -> dict[str, int]:
    """Count races covered by the current version of the detail-page cache."""
    from src.web.app import _race_detail_page_cache_key

    key_prefix = _race_detail_page_cache_key("")
    row = conn.execute(
        """
        SELECT COUNT(*) AS race_count,
               SUM(CASE WHEN EXISTS (
                   SELECT 1
                     FROM page_html_cache p
                    WHERE p.cache_key = ? || r.race_id
               ) THEN 1 ELSE 0 END) AS covered_races
          FROM races r
         WHERE r.race_date = ?
        """,
        (key_prefix, run_date),
    ).fetchone()
    return {
        "races": int(row[0] or 0) if row else 0,
        "covered": int(row[1] or 0) if row else 0,
    }


def race_detail_page_cache_coverage(run_date: str) -> dict[str, int]:
    """Count current-version detail pages without hardcoding the version."""
    with db_connect() as conn:
        return _race_detail_page_cache_coverage_on_connection(conn, run_date)


def _parse_jst_timestamp(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=JST)
    return parsed.astimezone(JST)


def _watchdog_missing_result_count_on_connection(conn: Any, target_date: str) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*)
          FROM races r
          LEFT JOIN (
              SELECT race_id, COUNT(*) AS result_rows
                FROM race_results
               WHERE finishing_position IS NOT NULL
               GROUP BY race_id
          ) rr ON rr.race_id = r.race_id
         WHERE r.race_date = ?
           AND COALESCE(rr.result_rows, 0) < 6
        """,
        (target_date,),
    ).fetchone()
    return int(row[0] or 0) if row else 0


def _watchdog_missing_result_count(target_date: str) -> int:
    with db_connect() as conn:
        return _watchdog_missing_result_count_on_connection(conn, target_date)


def _watchdog_stale_running_on_connection(conn: Any, now: datetime) -> list[dict[str, str]]:
    stale_cutoff = now - timedelta(hours=WATCHDOG_STALE_RUNNING_HOURS)
    running_rows = conn.execute(
        """
        SELECT task_name, run_date, started_at
          FROM task_runs
         WHERE status = 'running' AND started_at IS NOT NULL
        """
    ).fetchall()
    return [
        {
            "task_name": str(row[0]),
            "run_date": str(row[1]),
            "started_at": str(row[2]),
        }
        for row in running_rows
        if (_parse_jst_timestamp(row[2]) or now) < stale_cutoff
    ]


def _watchdog_stale_running(now: datetime) -> list[dict[str, str]]:
    with db_connect() as conn:
        return _watchdog_stale_running_on_connection(conn, now)


def _watchdog_snapshot(now: datetime) -> dict[str, Any]:
    """Read the watchdog signals in a bounded, query-light snapshot."""
    today = now.date().isoformat()
    yesterday = (now.date() - timedelta(days=1)).isoformat()
    failure_cutoff = now - timedelta(hours=WATCHDOG_FAILURE_LOOKBACK_HOURS)
    pool_cutoff = now - timedelta(minutes=WATCHDOG_POOL_LOOKBACK_MINUTES)

    with db_connect() as conn:
        coverage = _race_detail_page_cache_coverage_on_connection(conn, today)

        missing_results: int | None = None
        if now.hour == 8:
            missing_results = _watchdog_missing_result_count_on_connection(conn, yesterday)

        failure_rows = conn.execute(
            """
            SELECT task_name, run_count, finished_at, detail, success_at
              FROM task_runs
             WHERE status = 'failure'
               AND finished_at IS NOT NULL
            """
        ).fetchall()
        repeated_failures = [
            {
                "task_name": str(row[0]),
                "run_count": int(row[1] or 0),
                "finished_at": str(row[2]),
                "detail": str(row[3] or "")[:300],
            }
            for row in failure_rows
            if int(row[1] or 0) >= WATCHDOG_FAILURE_RUN_COUNT_THRESHOLD
            and not row[4]
            and (_parse_jst_timestamp(row[2]) or datetime.min.replace(tzinfo=JST)) >= failure_cutoff
        ]

        pool_row = conn.execute(
            """
            SELECT detail_json
              FROM system_status
             WHERE check_name = 'transient_db_error' AND check_date = ?
            """,
            (today,),
        ).fetchone()
        try:
            pool_detail = json.loads(str(pool_row[0])) if pool_row and pool_row[0] else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            pool_detail = {}
        recent_pool_events = [
            event
            for event in pool_detail.get("recent", [])
            if isinstance(event, dict)
            and (_parse_jst_timestamp(event.get("at")) or datetime.min.replace(tzinfo=JST))
            >= pool_cutoff
        ]

        stale_running = _watchdog_stale_running_on_connection(conn, now)

    return {
        "detail_cache": coverage,
        "yesterday": yesterday,
        "missing_results": missing_results,
        "repeated_failures": repeated_failures,
        "pool_events": len(recent_pool_events),
        "pool_recent": recent_pool_events[-10:],
        "stale_running": stale_running,
    }


def _write_watchdog_status(
    issue: str,
    now: datetime,
    status: str,
    message: str,
    detail: dict[str, Any],
) -> None:
    """Best-effort daily upsert using the existing system_status table."""
    check_name = WATCHDOG_STATUS_PREFIX + issue
    check_date = now.date().isoformat()
    checked_at = now.replace(tzinfo=None).isoformat(timespec="seconds")
    payload = json.dumps(detail, ensure_ascii=True, sort_keys=True, default=str)
    try:
        with db_connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM system_status WHERE check_name=? AND check_date=?",
                (check_name, check_date),
            ).fetchone()
            if exists:
                conn.execute(
                    """
                    UPDATE system_status
                       SET status=?, message=?, detail_json=?, checked_at=?
                     WHERE check_name=? AND check_date=?
                    """,
                    (status, message, payload, checked_at, check_name, check_date),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO system_status
                        (check_name, check_date, status, message, detail_json, checked_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (check_name, check_date, status, message, payload, checked_at),
                )
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        print(
            f"[cron-watchdog] status write failed issue={issue}: {type(exc).__name__}: {exc}",
            flush=True,
        )


def _watchdog_alert(issue: str, message: str, detail: dict[str, Any]) -> None:
    _notify_failure_best_effort(
        f"boatrace-watchdog-{issue}",
        message,
        detail=detail,
        cooldown_hours=WATCHDOG_ALERT_COOLDOWN_HOURS,
        incident_category="watchdog",
    )


def run_cron_watchdog(now: datetime, *, initial_reaped: int = 0) -> bool:
    """Detect, repair, persist, and alert without ever owning cron control flow."""
    try:
        snapshot = _watchdog_snapshot(now)
    except Exception as exc:  # noqa: BLE001
        print(f"[cron-watchdog] snapshot failed: {type(exc).__name__}: {exc}", flush=True)
        return False

    coverage = snapshot["detail_cache"]
    races = int(coverage.get("races", 0))
    covered = int(coverage.get("covered", 0))
    if races > 0 and covered / races < WATCHDOG_CACHE_MIN_COVERAGE:
        repaired = False
        try:
            repaired = bool(run_detail_pages_selfheal(now))
        except Exception as exc:  # noqa: BLE001
            print(f"[cron-watchdog] detail selfheal failed: {type(exc).__name__}: {exc}", flush=True)
        after = coverage
        if repaired:
            try:
                after = race_detail_page_cache_coverage(now.date().isoformat())
            except Exception as exc:  # noqa: BLE001
                print(f"[cron-watchdog] detail recheck failed: {type(exc).__name__}: {exc}", flush=True)
        after_races = int(after.get("races", 0))
        after_covered = int(after.get("covered", 0))
        resolved = bool(
            repaired
            and after_races > 0
            and after_covered / after_races >= WATCHDOG_CACHE_MIN_COVERAGE
        )
        detail = {"before": coverage, "after": after, "repair_ok": repaired}
        _write_watchdog_status(
            "detail_cache",
            now,
            "ok" if resolved else "error",
            f"current detail cache coverage {covered}/{races}; after={after_covered}/{after_races}",
            detail,
        )
        if not resolved:
            _watchdog_alert("detail-cache", "detail cache coverage remains low after selfheal", detail)

    missing_results = snapshot.get("missing_results")
    if isinstance(missing_results, int) and missing_results >= WATCHDOG_RESULT_MISSING_THRESHOLD:
        repaired = False
        try:
            repaired = bool(run_yesterday_results_backfill(now))
        except Exception as exc:  # noqa: BLE001
            print(f"[cron-watchdog] result backfill failed: {type(exc).__name__}: {exc}", flush=True)
        remaining = missing_results
        if repaired:
            try:
                remaining = _watchdog_missing_result_count(snapshot["yesterday"])
            except Exception as exc:  # noqa: BLE001
                print(f"[cron-watchdog] result recheck failed: {type(exc).__name__}: {exc}", flush=True)
        detail = {
            "date": snapshot["yesterday"],
            "before_missing": missing_results,
            "remaining_missing": remaining,
            "repair_ok": repaired,
        }
        resolved = repaired and remaining < WATCHDOG_RESULT_MISSING_THRESHOLD
        _write_watchdog_status(
            "yesterday_results",
            now,
            "ok" if resolved else "error",
            f"yesterday result gaps before={missing_results} remaining={remaining}",
            detail,
        )
        if not resolved:
            _watchdog_alert("yesterday-results", "yesterday result gaps remain after backfill", detail)

    repeated_failures = snapshot["repeated_failures"]
    if repeated_failures:
        detail = {"threshold": WATCHDOG_FAILURE_RUN_COUNT_THRESHOLD, "tasks": repeated_failures}
        _write_watchdog_status(
            "cron_failures",
            now,
            "error",
            f"repeated cron failures detected for {len(repeated_failures)} task(s)",
            detail,
        )
        _watchdog_alert("cron-failures", "repeated cron failures detected", detail)

    pool_events = int(snapshot["pool_events"])
    if pool_events >= WATCHDOG_POOL_EVENT_THRESHOLD:
        detail = {
            "lookback_minutes": WATCHDOG_POOL_LOOKBACK_MINUTES,
            "event_count": pool_events,
            "recent": snapshot["pool_recent"],
        }
        _write_watchdog_status(
            "pool_exhaustion",
            now,
            "error",
            f"transient DB errors repeated {pool_events} times in {WATCHDOG_POOL_LOOKBACK_MINUTES}m",
            detail,
        )
        _watchdog_alert("pool-exhaustion", "transient DB/pool failures are recurring", detail)

    stale_running = snapshot["stale_running"]
    if stale_running or initial_reaped:
        if stale_running:
            reap_stale_task_runs(now)
        try:
            remaining_stale = _watchdog_stale_running(now)
        except Exception as exc:  # noqa: BLE001
            print(f"[cron-watchdog] stale recheck failed: {type(exc).__name__}: {exc}", flush=True)
            remaining_stale = stale_running
        detail = {
            "initial_reaped": initial_reaped,
            "detected_after_reaper": stale_running,
            "remaining": remaining_stale,
        }
        _write_watchdog_status(
            "stale_running",
            now,
            "error" if remaining_stale else "ok",
            f"stale running tasks remaining={len(remaining_stale)}",
            detail,
        )
        if remaining_stale:
            _watchdog_alert("stale-running", "stale running tasks remain after reaper", detail)
    return True


def run_yesterday_results_backfill(now: datetime) -> bool:
    """Retry yesterday's idempotent result poll once each JST morning."""
    if now.hour != 8:
        return True
    target_date = (now.date() - timedelta(days=1)).isoformat()
    task = "render_results_backfill_yesterday"
    if task_success_exists(task, target_date):
        return True
    ok = run_py(
        ["scripts/poll_results.py", "--date", target_date, "--no-jitter"],
        timeout=900,
    )
    record_task(
        task,
        target_date,
        "success" if ok else "failure",
        detail=f"target_date={target_date}",
    )
    return ok


def run_detail_pages_selfheal(now: datetime) -> bool:
    """Resume today's detail caches in bounded slices until coverage is complete."""
    today = now.date().isoformat()
    task = "render_detail_pages_selfheal"
    try:
        coverage = race_detail_page_cache_coverage(today)
    except Exception as exc:
        print(f"[detail-selfheal] coverage check failed: {type(exc).__name__}: {exc}", flush=True)
        record_task(task, today, "failure", detail="coverage_check_failed")
        return False

    races = coverage["races"]
    covered = coverage["covered"]
    if races <= 0:
        record_task(task, today, "failure", detail="race_count=0")
        return False
    if covered >= races:
        record_task(task, today, "success", detail=f"skip:coverage={covered}/{races}")
        return True

    if task_attempt_recently_finished(
        task,
        today,
        now,
        min_interval_minutes=DETAIL_SELFHEAL_MIN_INTERVAL_MINUTES,
    ):
        print(
            f"[detail-selfheal] cooldown coverage={covered}/{races} "
            f"min_interval={DETAIL_SELFHEAL_MIN_INTERVAL_MINUTES}m",
            flush=True,
        )
        return True

    print(f"[detail-selfheal] incomplete coverage={covered}/{races} -> prewarm", flush=True)
    tags_ok = run_py(
        [
            "scripts/prewarm_race_detail_tags.py",
            "--date", today,
            "--budget-sec", str(DETAIL_SELFHEAL_TAG_BUDGET_SEC),
        ],
        timeout=DETAIL_SELFHEAL_TIMEOUT_SEC,
    )
    pages_ok = run_py(
        [
            "scripts/prewarm_race_detail_pages.py",
            "--date", today,
            "--missing-only",
            "--budget-sec", str(DETAIL_SELFHEAL_PAGE_BUDGET_SEC),
        ],
        timeout=DETAIL_SELFHEAL_TIMEOUT_SEC,
    )
    ok = bool(tags_ok and pages_ok)
    try:
        after = race_detail_page_cache_coverage(today)
    except Exception as exc:  # noqa: BLE001
        print(f"[detail-selfheal] post-coverage failed: {type(exc).__name__}: {exc}", flush=True)
        after = coverage
    remaining = max(0, after["races"] - after["covered"])
    record_task(
        task,
        today,
        "success" if ok else "failure",
        detail=(
            f"coverage={covered}/{races} after={after['covered']}/{after['races']} "
            f"remaining={remaining} partial={remaining > 0} "
            f"tags_ok={tags_ok} pages_ok={pages_ok}"
        ),
    )
    return ok


def tide_refresh_needed(run_date: str) -> bool:
    from src.collectors.tide import load_tide_station_map

    mapping = load_tide_station_map()
    tide_stadiums = sorted(int(k) for k in mapping.keys() if str(k).isdigit())
    if not tide_stadiums:
        return False

    placeholders = ",".join("?" for _ in tide_stadiums) or "NULL"
    params = [run_date, *tide_stadiums]
    with db_connect() as conn:
        expected = conn.execute(
            f"""
            SELECT COUNT(*) FROM races
             WHERE race_date = ?
               AND stadium_number IN ({placeholders})
            """,
            params,
        ).fetchone()[0] or 0
        if expected == 0:
            return False
        imported = conn.execute(
            f"""
            SELECT COUNT(DISTINCT rt.race_id)
              FROM race_tides rt
              JOIN races r ON r.race_id = rt.race_id
             WHERE r.race_date = ?
               AND r.stadium_number IN ({placeholders})
            """,
            params,
        ).fetchone()[0] or 0
    print(f"[tides] expected={expected} imported={imported}", flush=True)
    return imported < expected


def roi_daily_cache_needs_repair(target_date: str) -> bool:
    """Return True when yesterday's finalized ROI cache is absent or invalid."""
    try:
        with db_connect() as conn:
            row = conn.execute(
                "SELECT stats_json FROM l4_daily_stats_cache WHERE race_date = ?",
                (target_date,),
            ).fetchone()
        if not row or not row[0]:
            return True
        payload = json.loads(row[0])
        return bool(
            payload.get("_adopted_market_signals_cache_missing")
            or payload.get("_adopted_daily_select_version") != ROI_DAILY_CACHE_VERSION
            or payload.get("_strategy_definition_signature") != strategy_definition_signature(REPO)
        )
    except Exception as exc:
        print(f"[roi-cache] check failed: {type(exc).__name__}: {exc}", flush=True)
        return True


def run_roi_daily_self_heal(now: datetime) -> bool:
    """Materialize yesterday after results and payouts have arrived."""
    target_date = (now.date() - timedelta(days=1)).isoformat()
    if not roi_daily_cache_needs_repair(target_date):
        print(f"[roi-cache] current date={target_date}", flush=True)
        return True
    print(f"[roi-cache] repair date={target_date}", flush=True)
    ok = run_py(
        ["scripts/prewarm_strategy_pages.py", "--mode", "daily-reconcile", "--date", now.date().isoformat()],
        timeout=1800,
    )
    if ok:
        ok &= run_derived_start_stats(target_date, target_date)
    if ok:
        ok &= run_py(
            ["scripts/backfill_accident_dent_daily_cache.py", "--from", target_date, "--to", target_date],
            timeout=900,
        )
    verified = ok and not roi_daily_cache_needs_repair(target_date)
    record_task(
        "render_roi_daily_reconcile",
        target_date,
        "success" if verified else "failure",
        detail=f"cache_verified={verified}",
    )
    return verified


def run_derived_start_stats(from_date: str, to_date: str) -> bool:
    return run_py(
        ["scripts/build_derived_start_stats.py", "--from", from_date, "--to", to_date],
        timeout=1800,
    )


def task_attempt_exists(task_name: str, run_date: str) -> bool:
    """Return whether this recovery slot has already been attempted."""
    try:
        with db_connect() as conn:
            row = conn.execute(
                """
                SELECT run_count
                  FROM task_runs
                 WHERE task_name = ?
                   AND run_date = ?
                """,
                (task_name, run_date),
            ).fetchone()
        return bool(row and int(row[0] or 0) > 0)
    except Exception as exc:
        print(f"[task_runs] attempt read failed: {type(exc).__name__}: {exc}", flush=True)
        return False


def task_attempt_recently_finished(
    task_name: str,
    run_date: str,
    now: datetime,
    *,
    min_interval_minutes: int,
) -> bool:
    """Return True while a completed attempt is inside its cooldown window."""
    try:
        with db_connect() as conn:
            row = conn.execute(
                """
                SELECT finished_at, started_at
                  FROM task_runs
                 WHERE task_name = ?
                   AND run_date = ?
                """,
                (task_name, run_date),
            ).fetchone()
        raw = (row[0] or row[1]) if row else None
        if not raw:
            return False
        attempted_at = raw if isinstance(raw, datetime) else datetime.fromisoformat(str(raw))
        if attempted_at.tzinfo is None:
            attempted_at = attempted_at.replace(tzinfo=JST)
        else:
            attempted_at = attempted_at.astimezone(JST)
        return now - attempted_at < timedelta(minutes=min_interval_minutes)
    except Exception as exc:  # noqa: BLE001
        print(f"[task_runs] cooldown read failed: {type(exc).__name__}: {exc}", flush=True)
        return False


def daily_source_counts(run_date: str) -> dict[str, int]:
    """Read the minimum source-data counts required to render today's races."""
    with db_connect() as conn:
        row = conn.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM races WHERE race_date = ?) AS races,
              (SELECT COUNT(*)
                 FROM race_entries e
                 JOIN races r ON r.race_id = e.race_id
                WHERE r.race_date = ?) AS entries,
              (SELECT COUNT(*)
                 FROM race_entries e
                 JOIN races r ON r.race_id = e.race_id
                WHERE r.race_date = ?
                  AND e.racer_number IS NOT NULL
                  AND e.assigned_motor_number IS NOT NULL
                  AND e.assigned_motor_top_2_percent IS NOT NULL) AS detail_entries,
              (SELECT COUNT(DISTINCT p.race_id)
                 FROM predictions p
                 JOIN races r ON r.race_id = p.race_id
                WHERE r.race_date = ?) AS predictions
            """,
            (run_date, run_date, run_date, run_date),
        ).fetchone()
    return {
        "races": int(row[0] or 0),
        "entries": int(row[1] or 0),
        "detail_entries": int(row[2] or 0),
        "predictions": int(row[3] or 0),
    }


def daily_source_complete(counts: dict[str, int]) -> bool:
    races = int(counts.get("races", 0) or 0)
    entries = int(counts.get("entries", 0) or 0)
    detail_entries = int(counts.get("detail_entries", entries) or 0)
    predictions = int(counts.get("predictions", 0) or 0)
    return (
        races > 0
        and entries >= races * 6
        and detail_entries >= races * 6
        and predictions >= races
    )


def run_signal_refresh_slot(
    now: datetime,
    *,
    source_gate_verified: bool = False,
) -> bool:
    """Rebuild today's ROI candidate snapshot once per five-minute slot.

    Failed attempts are retried by the next cron tick. A cold or missing signal
    cache leaves the high-ROI list blank, so failures are never marked as a
    successful slot.
    """
    today = now.date().isoformat()
    slot = now.minute // 5
    task = signal_refresh_task_name(now)
    if task_attempt_exists(task, today):
        print(f"[signal-refresh] already attempted slot={now.hour:02d}:{slot}", flush=True)
        return True
    if signal_refresh_recently_running(now):
        print(f"[signal-refresh] skip overlapping slot={now.hour:02d}:{slot}", flush=True)
        return True

    record_task(task, today, "running")
    if not source_gate_verified and not run_program_source_gate(today):
        record_task(task, today, "failure", detail="program_source_gate_not_ready")
        return False
    ok = run_derived_start_stats(today, today)
    if not ok:
        record_task(task, today, "failure", detail="derived_start_stats_failed")
        return False
    result = run_py_detailed(
        ["scripts/prewarm_strategy_pages.py", "--mode", "signals", "--date", today],
        timeout=1800,
    )
    record_task(
        task,
        today,
        "success" if result.ok else "failure",
        detail=None if result.ok else _subprocess_failure_detail(result),
    )
    return result.ok


def run_original_exhibition_catchup(now: datetime, target_date: str, *, label: str) -> bool:
    """Fill missed original exhibition rows without waiting for manual repair.

    Live collection still runs around close time. This catch-up path exists for
    venue pages that appear late, transient fetch failures, or Render restarts.
    It only targets races that are already closed or close very soon.
    """
    task = f"render_original_exhibition_{label}_{now.hour:02d}"
    if task_attempt_exists(task, target_date):
        print(f"[original-exhibition-catchup] already attempted task={task} date={target_date}", flush=True)
        return True

    before_counts = original_exhibition_daily_counts(target_date)
    due = find_missing_original_exhibition_races(
        now,
        target_date=target_date,
        past_min=ORIGINAL_EXHIBITION_CATCHUP_PAST_MIN,
        future_min=ORIGINAL_EXHIBITION_CATCHUP_FUTURE_MIN,
        limit=ORIGINAL_EXHIBITION_CATCHUP_LIMIT,
    )
    print(
        "[original-exhibition-catchup] "
        f"date={target_date} expected={before_counts['expected_races']} "
        f"imported={before_counts['imported_races']} due={len(due)}",
        flush=True,
    )
    if not due:
        record_task(
            task,
            target_date,
            "success",
            detail=(
                f"expected={before_counts['expected_races']} "
                f"imported={before_counts['imported_races']} rows={before_counts['rows']} "
                "due=0"
            ),
        )
        return True

    from src.collectors import original_exhibition as original_exhibition_collector

    ok = True
    detail = ""
    try:
        s = original_exhibition_collector.collect_for_races(
            datetime.fromisoformat(target_date).date(),
            [(race_id, stadium, race_no) for race_id, stadium, race_no, _close in due],
            force=False,
            save_html=False,
            pattern_limit=8,
        )
        after_counts = original_exhibition_daily_counts(target_date)
        detail = (
            f"expected={after_counts['expected_races']} "
            f"imported={after_counts['imported_races']} rows={after_counts['rows']} "
            f"due={len(due)} targeted={s['races_targeted']} "
            f"fetched={s['pages_fetched']} found={s['races_found']} "
            f"inserted={s['rows_inserted']}"
        )
        print(f"[original-exhibition-catchup] {detail}", flush=True)
    except Exception as exc:
        ok = False
        detail = f"{type(exc).__name__}: {exc}"[:1000]
        print(f"[original-exhibition-catchup] failed: {detail}", flush=True)

    record_task(task, target_date, "success" if ok else "failure", detail=detail)
    return ok


def run_tides(now: datetime) -> bool:
    year_from = now.year
    year_to = (now.date() + timedelta(days=1)).year
    args = [
        "scripts/fetch_and_import_jma_tides.py",
        "--year-from", str(year_from),
        "--year-to", str(year_to),
        "--only-missing",
        "--timeout", "30",
    ]
    return run_py(args, timeout=1800)


def run_db_maintenance() -> bool:
    # Supabase keeps only recent operational odds data.
    # Historical full archives remain on local SQLite / backfill workflows.
    return run_py(
        [
            "scripts/db_size_check.py",
            "--cleanup",
            "--auto",
            "--keep-days", "30",
            "--keep-raw-days", "90",
        ],
        timeout=1800,
    )


def accident_period_start(d: datetime) -> str:
    if 5 <= d.month <= 10:
        return f"{d.year}-05-01"
    if d.month >= 11:
        return f"{d.year}-11-01"
    return f"{d.year - 1}-11-01"


def _accident_local_mode() -> bool:
    return not os.getenv("RENDER", "").strip()


def run_accident_rebuild(date_from: str, date_to: str) -> bool:
    args = ["scripts/rebuild_racer_accident_stats.py", "--from", date_from, "--to", date_to]
    if _accident_local_mode():
        args.insert(1, "--local")
    return run_py(args, timeout=900)


def run_accident_rank_snapshot(target_date: str) -> bool:
    args = ["scripts/cache_racer_accident_rank_snapshot.py", "--date", target_date]
    if _accident_local_mode():
        args.extend(["--db-path", config.DB_PATH])
    return run_py(args, timeout=300)


def run_accident_external_check(target_date: str) -> bool:
    return run_py(["scripts/check_external_accident_snapshot.py", "--date", target_date], timeout=300)


def latest_accident_snapshot_state() -> tuple[str | None, str | None]:
    try:
        with db_connect() as conn:
            row = conn.execute(
                """
                SELECT source_kind, MAX(snapshot_date), MAX(period_end)
                  FROM racer_accident_rank_snapshots
                 WHERE source_kind IN ('official_external', 'reconstructed')
                 GROUP BY source_kind
                 ORDER BY CASE WHEN source_kind = 'official_external' THEN 0 ELSE 1 END,
                          MAX(period_end) DESC,
                          MAX(snapshot_date) DESC
                 LIMIT 1
                """
            ).fetchone()
        snapshot_date = str(row[1]) if row and row[1] else None
        period_end = str(row[2]) if row and row[2] else None
        return snapshot_date, period_end
    except Exception as exc:
        print(
            f"[accident-refresh] snapshot check failed: {type(exc).__name__}: {exc}",
            flush=True,
        )
        return None, None


def latest_completed_results_date() -> str | None:
    try:
        with db_connect() as conn:
            row = conn.execute(
                """
                SELECT MAX(r.race_date)
                  FROM race_results rr
                  JOIN races r ON r.race_id = rr.race_id
                 WHERE rr.finishing_position IS NOT NULL
                """
            ).fetchone()
        return str(row[0]) if row and row[0] else None
    except Exception as exc:
        print(
            f"[accident-refresh] latest result-date check failed: {type(exc).__name__}: {exc}",
            flush=True,
        )
        return None


def run_accident_full_refresh(target_date: str) -> bool:
    target_dt = datetime.fromisoformat(target_date).replace(tzinfo=JST)
    ok = run_accident_rebuild(accident_period_start(target_dt), target_date)
    if ok:
        ok = run_accident_external_check(target_date)
    if ok:
        ok = run_accident_rank_snapshot(target_date)
    race_count = race_count_for_date(target_date) if ok else 0
    if ok and race_count == 0:
        print(f"[accident-refresh] skip detail prewarm date={target_date} reason=no-races", flush=True)
        return True
    if ok:
        ok = run_py(["scripts/prewarm_race_detail_tags.py", "--date", target_date], timeout=900)
    if ok:
        ok = run_py(["scripts/prewarm_race_detail_pages.py", "--date", target_date], timeout=1800)
    if ok:
        ok = run_py(
            ["scripts/check_post_run_integrity.py", "--date", target_date, "--stage", "nightly"],
            timeout=300,
        )
    return ok


@_with_regular_run_lock
def main() -> int:
    log_deploy_revision("boatrace-regular-cron")
    os.environ.setdefault("BOATRACE_TASK_TRIGGER", "render-cron")
    now = jst_now()
    today = now.date().isoformat()
    print(f"[render-regular] now_jst={now.isoformat(timespec='seconds')}", flush=True)

    if not os.getenv("DATABASE_URL", "").strip():
        raise RuntimeError("DATABASE_URL is required for Render regular scheduler")
    ensure_task_runs_table()
    reaped = reap_stale_task_runs(now)
    try:
        run_cron_watchdog(now, initial_reaped=reaped)
    except Exception as exc:  # noqa: BLE001
        print(f"[cron-watchdog] nonfatal failure: {type(exc).__name__}: {exc}", flush=True)
    lite_mode = render_daytime_lite_mode()
    exit_code = 0

    # Live beforeinfo/original-exhibition collection and race-detail refresh are
    # owned by boatrace-exhibition-detail-cron. Keeping them out of the regular
    # five-minute scheduler prevents duplicate exhibition fetches.

    # Results settle live ROI rows and must run before signal/detail prewarming.
    # Daytime work is bounded; full detail generation belongs to maintenance.
    if 8 <= now.hour <= 23:
        run_py(["scripts/poll_results.py", "--no-jitter"], timeout=900)
        run_py(
            ["scripts/check_post_run_integrity.py", "--date", today, "--stage", "post-result"],
            timeout=300,
        )
        run_py(["scripts/evaluate_start_predictions.py", "--date", today], timeout=900)

    if lite_mode and 8 <= now.hour <= 23:
        if not run_lite_daytime_bootstrap(now):
            exit_code = 1

    # Refresh the top snapshot after result polling and any signal rebuild.
    if 8 <= now.hour <= 23:
        run_top_page_snapshot(now, lightweight=True, environment_only=True)

    print("[render-regular] done", flush=True)
    if exit_code:
        _notify_failure_best_effort(
            REGULAR_CRON_JOB_NAME,
            "regular cron completed with a failure status",
            detail={"date": today, "exit_code": exit_code, "lite_mode": lite_mode},
        )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
