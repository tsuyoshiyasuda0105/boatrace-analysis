from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.db.connection import connect as db_connect


REPO = Path(__file__).resolve().parents[1]
JST = timezone(timedelta(hours=9))
BEFOREINFO_WINDOW_MIN = 5
BEFOREINFO_WINDOW_MAX = 9
BEFOREINFO_COOLDOWN_MIN = 8


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
            f"stations={tide_summary.get('stations', 0)}",
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
    for race_id, stadium, race_no, close in due:
        print(f"[beforeinfo] scrape {race_id} close={close.strftime('%H:%M')}", flush=True)
        page = scrape_one_race(stadium, race_no, now.date())
        if page:
            updates.append((race_id, page))

    if not updates:
        print("[beforeinfo] no valid pages", flush=True)
        return True

    now_iso = datetime.now().isoformat(timespec="seconds")
    summary = write_updates(updates, now_iso, also_local=False)
    print(f"[beforeinfo] written={summary}", flush=True)
    if summary.get("races", 0) > 0:
        return run_py(["scripts/render_cache_predictions.py", "--date", now.date().isoformat()], timeout=1800)
    return True


def run_morning(now: datetime) -> bool:
    today = now.date().isoformat()
    ok = True
    ok &= run_py(["scripts/backfill_official.py", "--start", today, "--end", today], timeout=1800)
    ok &= run_py(["scripts/daily_collect.py", "--date", today], timeout=1800)
    # Tide rows depend on races already existing, so import after daily race data is written.
    ok &= run_tides(now)
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


def run_accident_rebuild(date_from: str, date_to: str) -> bool:
    return run_py(
        ["scripts/rebuild_racer_accident_stats.py", "--from", date_from, "--to", date_to],
        timeout=900,
    )


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
    ok &= run_py(["scripts/prewarm_strategy_pages.py", "--mode", "nightly"], timeout=3600)
    ok &= run_accident_rebuild(accident_period_start(now), today)
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

    # Live beforeinfo/weather correction. The scrape function has its own cooldown.
    if 8 <= now.hour <= 22:
        run_beforeinfo(now)
        # Build the expensive adopted-strategy snapshot in the cron process.
        # The web request then reads one cached JSON row instead of recomputing
        # strategy joins when a user opens or refreshes the race list.
        run_py(["scripts/prewarm_strategy_pages.py", "--mode", "signals"], timeout=900)

    # Lightweight result polling during race hours.
    if 8 <= now.hour <= 23:
        run_py(["scripts/poll_results.py", "--no-jitter"], timeout=900)
        run_accident_rebuild(today, today)

    # Self-heal tide rows on every loop so missing imports do not survive until the next hour.
    if 6 <= now.hour <= 23:
        run_tide_self_heal(now)

    # Hourly summaries/health checks near the top of the hour.
    if now.minute < 5 and 9 <= now.hour <= 23:
        task = f"render_hourly_{now.hour:02d}"
        if not task_success_exists(task, today):
            ok = run_hourly(now)
            record_task(task, today, "success" if ok else "failure")

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
