from __future__ import annotations

import os
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.db.connection import connect as db_connect
from src.roi_contract import ROI_DAILY_CACHE_VERSION, strategy_definition_signature
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
def jst_now() -> datetime:
    return datetime.now(tz=JST)


def run_py(args: list[str], timeout: int = 1800) -> bool:
    cmd = [sys.executable, *args]
    print("$ " + " ".join(args), flush=True)
    started = time.monotonic()
    proc = subprocess.run(cmd, cwd=REPO, timeout=timeout, check=False)
    elapsed = time.monotonic() - started
    print(f"exit={proc.returncode} elapsed={elapsed:.1f}s", flush=True)
    return proc.returncode == 0


def _parse_race_close_jst(closed_at, race_date: str) -> datetime | None:
    if isinstance(closed_at, datetime):
        return closed_at.replace(tzinfo=JST) if closed_at.tzinfo is None else closed_at
    if not isinstance(closed_at, str):
        return None
    try:
        if " " in closed_at and len(closed_at) >= 16:
            dt = datetime.fromisoformat(closed_at)
        else:
            time_part = closed_at if len(closed_at) >= 5 else f"{closed_at}:00"
            dt = datetime.fromisoformat(f"{race_date} {time_part}")
    except (TypeError, ValueError):
        return None
    return dt.replace(tzinfo=JST)


def find_missing_original_exhibition_races(
    now: datetime,
    *,
    target_date: str | None = None,
    past_min: int = ORIGINAL_EXHIBITION_RECOVERY_PAST_MIN,
    future_min: int = ORIGINAL_EXHIBITION_RECOVERY_FUTURE_MIN,
    limit: int = ORIGINAL_EXHIBITION_RECOVERY_LIMIT,
) -> list[tuple[str, int, int, datetime]]:
    from src.collectors import original_exhibition as original_exhibition_collector

    supported = sorted(
        int(stadium)
        for stadium, patterns in original_exhibition_collector.SOURCE_PATTERNS.items()
        if patterns
    )
    if not supported:
        return []

    target_date = target_date or now.date().isoformat()
    placeholders = ",".join("?" for _ in supported)
    with db_connect() as conn:
        rows = conn.execute(
            f"""
            SELECT r.race_id, r.stadium_number, r.race_number, r.race_closed_at,
                   COUNT(oe.race_id) AS original_rows
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
    for race_id, stadium, race_no, closed_at, original_rows in rows:
        if int(original_rows or 0) > 0:
            continue
        close = _parse_race_close_jst(closed_at, target_date)
        if close is None:
            continue
        mins_until = (close - now).total_seconds() / 60.0
        if mins_until < -abs(past_min) or mins_until > future_min:
            continue
        due.append((race_id, int(stadium), int(race_no), close))
        if limit > 0 and len(due) >= limit:
            break
    return due


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
    with db_connect() as conn:
        conn.executescript("""
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
            ALTER TABLE task_runs ENABLE ROW LEVEL SECURITY;
        """)
        conn.commit()


def record_task(task_name: str, run_date: str, status: str, detail: str | None = None) -> None:
    now_iso = jst_now().replace(tzinfo=None).isoformat(timespec="seconds")
    success_at = now_iso if status == "success" else None
    try:
        with db_connect() as conn:
            conn.execute(
                """
                INSERT INTO task_runs
                    (task_name, run_date, status, run_count, started_at, finished_at,
                     success_at, trigger, detail)
                VALUES (?, ?, ?, 1, ?, ?, ?, 'render-cron', ?)
                ON CONFLICT (task_name, run_date) DO UPDATE SET
                    status = EXCLUDED.status,
                    run_count = task_runs.run_count + 1,
                    started_at = EXCLUDED.started_at,
                    finished_at = EXCLUDED.finished_at,
                    success_at = COALESCE(EXCLUDED.success_at, task_runs.success_at),
                    trigger = EXCLUDED.trigger,
                    detail = EXCLUDED.detail
                """,
                (task_name, run_date, status, now_iso, now_iso, success_at, detail),
            )
            conn.commit()
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


def run_morning(now: datetime) -> bool:
    today = now.date().isoformat()
    ok = True
    ok &= run_py(["scripts/backfill_official.py", "--start", today, "--end", today], timeout=1800)
    ok &= run_py(["scripts/daily_collect.py", "--date", today], timeout=1800)
    # Tide rows depend on races already existing, so import after daily race data is written.
    ok &= run_tides(now)
    # Accident-based strategies and tags should be ready before the first
    # morning prediction/signal materialization.
    ok &= run_accident_self_heal(now)
    ok &= run_py(["scripts/render_cache_predictions.py", "--date", today], timeout=1800)
    ok &= run_py(["scripts/check_data_quality.py"], timeout=600)
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


def run_hourly(now: datetime) -> bool:
    ok = True
    try:
        if tide_refresh_needed(now.date().isoformat()):
            print("[hourly] tide rows missing -> rerun import", flush=True)
            ok &= run_tides(now)
    except Exception as exc:
        print(f"[hourly] tide check failed: {type(exc).__name__}: {exc}", flush=True)
    ok &= run_py(["scripts/sync_l4_summary_to_supabase.py", "--recent-days", "3"], timeout=1800)
    ok &= run_py(["scripts/check_data_quality.py"], timeout=600)
    ok &= run_py(["scripts/agent_monitor.py", "--quiet"], timeout=600)
    return ok


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


def run_tide_self_heal(now: datetime) -> bool:
    """5分周期の本体ループでも潮欠損を補修する。

    朝バッチや毎時バッチが何らかの理由で取りこぼしても、
    race が投入済みで race_tides だけ欠けているケースを拾い直す。
    """
    try:
        if tide_refresh_needed(now.date().isoformat()):
            print("[self-heal] tide rows missing -> rerun import", flush=True)
            return run_tides(now)
    except Exception as exc:
        print(f"[self-heal] tide check failed: {type(exc).__name__}: {exc}", flush=True)
        return False
    return True


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
              (SELECT COUNT(DISTINCT p.race_id)
                 FROM predictions p
                 JOIN races r ON r.race_id = p.race_id
                WHERE r.race_date = ?) AS predictions
            """,
            (run_date, run_date, run_date),
        ).fetchone()
    return {
        "races": int(row[0] or 0),
        "entries": int(row[1] or 0),
        "predictions": int(row[2] or 0),
    }


def daily_source_complete(counts: dict[str, int]) -> bool:
    races = int(counts.get("races", 0) or 0)
    entries = int(counts.get("entries", 0) or 0)
    predictions = int(counts.get("predictions", 0) or 0)
    return races > 0 and entries >= races * 6 and predictions >= races


def run_morning_catchup_if_needed(now: datetime) -> bool:
    """Recover a missed morning job at most once per hour during service hours."""
    today = now.date().isoformat()
    try:
        counts = daily_source_counts(today)
    except Exception as exc:
        print(f"[morning-catchup] source check failed: {type(exc).__name__}: {exc}", flush=True)
        return False

    print(f"[morning-catchup] source={counts}", flush=True)
    if daily_source_complete(counts):
        return True

    task = f"render_morning_catchup_{now.hour:02d}"
    if task_attempt_exists(task, today):
        print(f"[morning-catchup] already attempted slot={now.hour:02d}", flush=True)
        return False

    print("[morning-catchup] source incomplete -> rerun morning collection", flush=True)
    ok = run_morning(now)
    record_task(task, today, "success" if ok else "failure", detail=str(counts))
    return ok


def run_signal_refresh_slot(now: datetime) -> bool:
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
    ok = run_py(
        ["scripts/prewarm_strategy_pages.py", "--mode", "signals", "--date", today],
        timeout=1800,
    )
    record_task(task, today, "success" if ok else "failure")
    return ok


def run_roi_history_slot(now: datetime) -> bool:
    """Refresh historical ROI pages once in each 12-hour JST window."""
    today = now.date().isoformat()
    task = roi_history_task_name(now)
    if task_attempt_exists(task, today):
        print(f"[roi-history] already attempted task={task}", flush=True)
        return True
    if task_success_exists(task, today):
        print(f"[roi-history] already succeeded task={task}", flush=True)
        return True

    ok = run_py(
        ["scripts/prewarm_strategy_pages.py", "--mode", "history", "--date", today],
        timeout=3600,
    )
    record_task(task, today, "success" if ok else "failure")
    return ok


def should_run_roi_history_slot(now: datetime) -> bool:
    """Keep heavy ROI history refresh out of the five-minute live loop.

    The history page recompute can exceed the Render Starter 512MiB memory cap
    when Supabase is slow. Run it only in two narrow windows; current-day race
    collection, result polling, and high-ROI candidate snapshots must remain
    the priority for boatrace-regular-cron.
    """
    return now.minute < 5 and now.hour in (0, 12)


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


def run_accident_self_heal(now: datetime) -> bool:
    """Rebuild the latest completed-results day when the materialized ranking is stale."""
    target_date = latest_completed_results_date() or (now.date() - timedelta(days=1)).isoformat()
    latest_snapshot, latest_period_end = latest_accident_snapshot_state()
    if (
        latest_snapshot
        and latest_period_end
        and latest_snapshot >= target_date
        and latest_period_end >= target_date
    ):
        print(
            f"[accident-refresh] current snapshot={latest_snapshot} period_end={latest_period_end}",
            flush=True,
        )
        return True

    slot_task = f"render_accident_refresh_slot_{now.hour:02d}"
    run_date = now.date().isoformat()
    if task_attempt_exists(slot_task, run_date):
        print(f"[accident-refresh] stale but already attempted slot={now.hour:02d}", flush=True)
        return False

    print(
        "[accident-refresh] stale "
        f"snapshot={latest_snapshot or '-'} period_end={latest_period_end or '-'} "
        f"target={target_date}",
        flush=True,
    )
    record_task(slot_task, run_date, "running", detail=f"target={target_date}")
    ok = run_accident_full_refresh(target_date)
    record_task(slot_task, run_date, "success" if ok else "failure", detail=f"target={target_date}")
    verified_snapshot, verified_period_end = latest_accident_snapshot_state() if ok else (None, None)
    ok = bool(
        ok
        and verified_snapshot
        and verified_period_end
        and verified_snapshot >= target_date
        and verified_period_end >= target_date
    )
    record_task(
        "render_accident_refresh",
        target_date,
        "success" if ok else "failure",
        detail=(
            f"snapshot={verified_snapshot or '-'} "
            f"period_end={verified_period_end or '-'} target={target_date}"
        ),
    )
    return ok


def run_nightly(now: datetime) -> bool:
    today = now.date().isoformat()
    tomorrow = (now.date() + timedelta(days=1)).isoformat()
    ok = True
    ok &= run_py(["scripts/backfill_official.py", "--start", today, "--end", today], timeout=1800)
    ok &= run_py(["scripts/daily_collect.py", "--date", today], timeout=1800)
    ok &= run_tides(now)
    ok &= run_py(["scripts/sync_l4_summary_to_supabase.py", "--recent-days", "5"], timeout=1800)
    ok &= run_py(["scripts/backfill_official.py", "--start", tomorrow, "--end", tomorrow], timeout=1800)
    ok &= run_py(["scripts/daily_collect.py", "--date", tomorrow], timeout=1800)
    # Preload tomorrow after its races exist as well.
    ok &= run_tides(now)
    ok &= run_py(["scripts/render_cache_predictions.py", "--date", tomorrow], timeout=1800)
    try:
        tomorrow_counts = daily_source_counts(tomorrow)
    except Exception as exc:
        print(
            f"[nightly] tomorrow source check failed: {type(exc).__name__}: {exc}",
            flush=True,
        )
        return False
    print(f"[nightly] tomorrow source={tomorrow_counts}", flush=True)
    if not daily_source_complete(tomorrow_counts):
        # The official B file can appear a few minutes after the first 23:30
        # attempt. Keep the task failed so the next five-minute cron retries.
        print("[nightly] tomorrow source incomplete -> retry next cron", flush=True)
        return False
    # Build tomorrow's high-ROI snapshot after tomorrow's races and predictions
    # exist. Without this, nightly prewarming only refreshes today's signals and
    # previous-day confirmed candidates do not appear until the morning run.
    ok &= run_py(["scripts/prewarm_race_detail_tags.py", "--date", tomorrow], timeout=900)
    ok &= run_py(
        ["scripts/prewarm_strategy_pages.py", "--mode", "signals", "--date", tomorrow],
        timeout=1800,
    )
    ok &= run_py(
        ["scripts/backfill_accident_dent_daily_cache.py", "--recent-days", "400"],
        timeout=1800,
    )
    ok &= run_py(["scripts/aggregate_start_prediction_metrics.py", "--date", today], timeout=900)
    ok &= run_accident_full_refresh(today)
    ok &= run_db_maintenance()
    return ok


def main() -> int:
    os.environ.setdefault("BOATRACE_TASK_TRIGGER", "render-cron")
    now = jst_now()
    today = now.date().isoformat()
    print(f"[render-regular] now_jst={now.isoformat(timespec='seconds')}", flush=True)

    if not os.getenv("DATABASE_URL", "").strip():
        raise RuntimeError("DATABASE_URL is required for Render regular scheduler")
    ensure_task_runs_table()

    # Morning data and predictions: run once per JST day.
    morning_start = now.replace(hour=6, minute=0, second=0, microsecond=0)
    morning_end = now.replace(hour=9, minute=0, second=0, microsecond=0)
    if morning_start <= now < morning_end:
        task = "render_morning"
        if not task_success_exists(task, today):
            ok = run_morning(now)
            record_task(task, today, "success" if ok else "failure")
        else:
            print("[morning] already successful today", flush=True)

    # A narrow morning window must not leave the service empty all day. Render is
    # the source of truth, so verify actual rows and recover even when a PC was off
    # or a previous task_runs row incorrectly reported success.
    if morning_end <= now < now.replace(hour=22, minute=0, second=0, microsecond=0):
        run_morning_catchup_if_needed(now)

    # Live beforeinfo/original-exhibition collection and race-detail refresh are
    # owned by boatrace-exhibition-detail-cron. Keeping them out of the regular
    # five-minute scheduler prevents duplicate exhibition fetches.

    # Refresh source-dependent candidates before the slower result poll. This
    # keeps the dashboard snapshot close to the five-minute cron cadence.
    if 6 <= now.hour <= 23:
        run_tide_self_heal(now)
        signal_ok = run_signal_refresh_slot(now)
        if signal_ok and not task_success_exists("render_detail_tags_today", today):
            ok = run_py(["scripts/prewarm_race_detail_tags.py", "--date", today], timeout=900)
            record_task("render_detail_tags_today", today, "success" if ok else "failure")

    # Lightweight result polling during race hours.
    if 8 <= now.hour <= 23:
        run_py(["scripts/poll_results.py", "--no-jitter"], timeout=900)
        run_py(
            ["scripts/check_post_run_integrity.py", "--date", today, "--stage", "post-result"],
            timeout=300,
        )
        run_py(["scripts/evaluate_start_predictions.py", "--date", today], timeout=900)

    # Hourly summaries/health checks near the top of the hour.
    if now.minute < 5 and 9 <= now.hour <= 23:
        task = f"render_hourly_{now.hour:02d}"
        if not task_success_exists(task, today):
            ok = run_hourly(now)
            record_task(task, today, "success" if ok else "failure")

    # Accident rankings feed race tags and several adopted ROI strategies.
    # Refresh once daily at 07:30 JST, after the 07:00 race-detail prewarm starts,
    # so accident tags are available before most users open morning race details.
    if now.hour == 7 and 30 <= now.minute < 35:
        run_accident_self_heal(now)

    # End-of-day refresh and tomorrow preload: run once per JST day.
    if now.hour == 23 and now.minute >= 30:
        task = "render_nightly"
        if not task_success_exists(task, today):
            ok = run_nightly(now)
            record_task(task, today, "success" if ok else "failure")
        else:
            print("[nightly] already successful today", flush=True)
        finalized_date = (now.date() - timedelta(days=1)).isoformat()
        if not task_success_exists("render_roi_daily_reconcile", finalized_date):
            run_roi_daily_self_heal(now)

    # Historical ROI pages are deliberately isolated from the live five-minute
    # loop. They may be expensive, and a failed 12-hour attempt must not keep
    # retrying every five minutes while today's races are running.
    if should_run_roi_history_slot(now):
        run_roi_history_slot(now)

    print("[render-regular] done", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
