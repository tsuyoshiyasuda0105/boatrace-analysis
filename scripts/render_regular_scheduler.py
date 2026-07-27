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
import config


REPO = Path(__file__).resolve().parents[1]
JST = timezone(timedelta(hours=9))
BEFOREINFO_WINDOW_MIN = 5
BEFOREINFO_WINDOW_MAX = 9
BEFOREINFO_COOLDOWN_MIN = 8
BEFOREINFO_WRITE_BATCH_SIZE = 6
# Keep this in sync with src.web.app.ADOPTED_DAILY_SELECT_VERSION.
# If this lags behind, the self-heal path treats fresh ROI rows as stale and
# can leave the dashboard looking empty after a successful cron run.
ROI_DAILY_CACHE_VERSION = "adopted_daily_select_v34"


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
    slot = now.minute // 30
    return f"render_signal_refresh_{now.hour:02d}_{slot}"


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
    print(f"[beforeinfo] due={len(due)}", flush=True)
    if not due:
        return True

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

    try:
        s = original_exhibition_collector.collect_for_races(
            now.date(),
            [(race_id, stadium, race_no) for race_id, stadium, race_no, _close in due],
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
        print(f"[original-exhibition] failed: {type(exc).__name__}: {exc}", flush=True)

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
    if summary["races"] <= 0:
        print("[beforeinfo] no valid pages", flush=True)
        return False

    print(f"[beforeinfo] written={summary}", flush=True)
    if summary.get("races", 0) > 0:
        # Keep the high-ROI list visible even if prediction refresh is slow.
        # A second prewarm after prediction refresh still runs below when due.
        run_py(["scripts/prewarm_strategy_pages.py", "--mode", "signals"], timeout=900)
        ok = run_py(["scripts/render_cache_predictions.py", "--date", now.date().isoformat()], timeout=1800)
        slot_task = signal_refresh_task_name(now)
        if not task_success_exists(slot_task, now.date().isoformat()):
            slot_ok = run_py(["scripts/prewarm_strategy_pages.py", "--mode", "signals"], timeout=900)
            record_task(slot_task, now.date().isoformat(), "success" if slot_ok else "failure")
            ok &= slot_ok
        else:
            print(f"[beforeinfo] skip signals prewarm; slot already fresh {slot_task}", flush=True)
        return ok
    return True


def run_morning(now: datetime) -> bool:
    today = now.date().isoformat()
    ok = True
    ok &= run_py(["scripts/backfill_official.py", "--start", today, "--end", today], timeout=1800)
    ok &= run_py(["scripts/daily_collect.py", "--date", today], timeout=1800)
    # Tide rows depend on races already existing, so import after daily race data is written.
    ok &= run_tides(now)
    # Prewarm before the heavier prediction refresh so the UI does not show an
    # empty high-ROI list if prediction generation or syncing stalls.
    ok &= run_py(["scripts/prewarm_strategy_pages.py", "--mode", "morning-check"], timeout=1800)
    ok &= run_py(["scripts/render_cache_predictions.py", "--date", today], timeout=1800)
    ok &= run_py(["scripts/check_data_quality.py"], timeout=600)
    ok &= run_py(["scripts/prewarm_strategy_pages.py", "--mode", "morning-check"], timeout=1800)
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
    ok &= run_py(["scripts/prewarm_strategy_pages.py", "--mode", "realtime"], timeout=1800)
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
    """Rebuild today's ROI candidate snapshot once per 10-minute slot.

    Failed attempts are intentionally retried by the next five-minute cron
    tick. A cold or missing signal cache leaves the high-ROI list blank, so a
    failure must not block the whole 30-minute slot.
    """
    today = now.date().isoformat()
    slot = now.minute // 30
    task = signal_refresh_task_name(now)
    if task_success_exists(task, today):
        print(f"[signal-refresh] already succeeded slot={now.hour:02d}:{slot}", flush=True)
        return True

    ok = run_py(
        ["scripts/prewarm_strategy_pages.py", "--mode", "signals", "--date", today],
        timeout=1800,
    )
    record_task(task, today, "success" if ok else "failure")
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


def latest_accident_snapshot_state() -> tuple[str | None, str | None]:
    try:
        with db_connect() as conn:
            row = conn.execute(
                """
                SELECT MAX(snapshot_date), MAX(period_end)
                  FROM racer_accident_rank_snapshots
                 WHERE source_kind = 'reconstructed'
                """
            ).fetchone()
        snapshot_date = str(row[0]) if row and row[0] else None
        period_end = str(row[1]) if row and row[1] else None
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
        ok = run_accident_rank_snapshot(target_date)
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
    ok &= run_py(
        ["scripts/prewarm_strategy_pages.py", "--mode", "signals", "--date", tomorrow],
        timeout=1800,
    )
    ok &= run_py(
        ["scripts/backfill_accident_dent_daily_cache.py", "--recent-days", "400"],
        timeout=1800,
    )
    ok &= run_py(["scripts/prewarm_strategy_pages.py", "--mode", "nightly"], timeout=3600)
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
    morning_start = now.replace(hour=6, minute=25, second=0, microsecond=0)
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

    # Live beforeinfo/weather correction. The scrape function has its own cooldown.
    if 8 <= now.hour <= 22:
        try:
            beforeinfo_ok = run_beforeinfo(now)
            record_task(
                "render_beforeinfo_live",
                today,
                "success" if beforeinfo_ok else "failure",
                detail=f"checked_at={now.isoformat(timespec='minutes')}",
            )
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"[:1000]
            print(f"[beforeinfo] failed: {detail}", flush=True)
            record_task("render_beforeinfo_live", today, "failure", detail=detail)
        run_py(["scripts/generate_start_predictions.py", "--date", today], timeout=900)
        # run_beforeinfo rebuilds the snapshot only when source rows changed.
        # Recomputing it unconditionally here duplicated the heaviest query and
        # could overlap the next five-minute cron run.

    # A dedicated prewarm service is optional. Keep the existing five-minute
    # scheduler self-contained and guarantee a fresh snapshot twice per hour.
    if 6 <= now.hour <= 23:
        run_signal_refresh_slot(now)

    # Lightweight result polling during race hours.
    if 8 <= now.hour <= 23:
        run_py(["scripts/poll_results.py", "--no-jitter"], timeout=900)
        run_py(["scripts/evaluate_start_predictions.py", "--date", today], timeout=900)

    # Self-heal tide rows on every loop so missing imports do not survive until the next hour.
    if 6 <= now.hour <= 23:
        run_tide_self_heal(now)

    # Hourly summaries/health checks near the top of the hour.
    if now.minute < 5 and 9 <= now.hour <= 23:
        task = f"render_hourly_{now.hour:02d}"
        if not task_success_exists(task, today):
            ok = run_hourly(now)
            record_task(task, today, "success" if ok else "failure")

    # Accident rankings change slowly and the rebuild can be expensive. Keep the
    # self-heal, but run it only near the top of the hour so it does not compete
    # with five-minute candidate refreshes during live race hours.
    if now.minute < 5 and 6 <= now.hour <= 23:
        run_accident_self_heal(now)

    if now.minute < 5 and 6 <= now.hour <= 23:
        run_roi_daily_self_heal(now)

    # End-of-day refresh and tomorrow preload: run once per JST day.
    if now.hour == 23 and now.minute >= 30:
        task = "render_nightly"
        if not task_success_exists(task, today):
            ok = run_nightly(now)
            record_task(task, today, "success" if ok else "failure")
        else:
            print("[nightly] already successful today", flush=True)

    print("[render-regular] done", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
