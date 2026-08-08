"""Collect exhibition data and refresh affected race-detail caches.

This cron owns live beforeinfo/original-exhibition collection. The regular
five-minute scheduler intentionally does not fetch exhibition data, preventing
duplicate requests while keeping detail pages current after source rows change.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
os.environ.setdefault("BOATRACE_TASK_TRIGGER", "render-exhibition-detail-refresh")
os.environ.setdefault("BOATRACE_ALLOW_EXPENSIVE_WEB_RECOMPUTE", "1")

import config  # noqa: E402
from scripts import scrape_beforeinfo_live as live_beforeinfo  # noqa: E402
from src.collectors import original_exhibition as original_exhibition_collector  # noqa: E402
from src.db.cron_run_log import record_cron_run  # noqa: E402
from src.db.connection import connect as db_connect  # noqa: E402


MOTOR_CACHE_VERSION = "v9"
JST = ZoneInfo("Asia/Tokyo")
BEFOREINFO_WINDOW_MIN = 5
BEFOREINFO_WINDOW_MAX = 9
BEFOREINFO_COOLDOWN_MIN = 8
BEFOREINFO_WRITE_BATCH_SIZE = 6
INCOMPLETE_PAST_MIN = 900
INCOMPLETE_FUTURE_MIN = 20
INCOMPLETE_LIMIT = 24
POST_RACE_INCOMPLETE_PAST_MIN = 36 * 60
POST_RACE_INCOMPLETE_FUTURE_MIN = 0
POST_RACE_INCOMPLETE_LIMIT = 72
ORIGINAL_EXHIBITION_PAST_MIN = 36 * 60
ORIGINAL_EXHIBITION_FUTURE_MIN = 30
ORIGINAL_EXHIBITION_LIMIT = 96
SIGNAL_REFRESH_MIN_GAP_MIN = 2
EXHIBITION_REFRESH_MAX_ACTIVE_MIN = 15


def _run_py(args: list[str], timeout: int = 900) -> bool:
    cmd = [sys.executable, *args]
    print("$ " + " ".join(args), flush=True)
    started = time.monotonic()
    proc = subprocess.run(cmd, cwd=REPO, timeout=timeout, check=False)
    elapsed = time.monotonic() - started
    print(f"exit={proc.returncode} elapsed={elapsed:.1f}s", flush=True)
    return proc.returncode == 0


def _ensure_task_runs_table() -> None:
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
            ALTER TABLE task_runs ENABLE ROW LEVEL SECURITY;
            """
        )
        conn.commit()


def _record_task(task_name: str, run_date: str, status: str, detail: str | None = None) -> None:
    now_iso = datetime.now(JST).replace(tzinfo=None).isoformat(timespec="seconds")
    success_at = now_iso if status == "success" else None
    try:
        with db_connect() as conn:
            conn.execute(
                """
                INSERT INTO task_runs
                    (task_name, run_date, status, run_count, started_at, finished_at,
                     success_at, trigger, detail)
                VALUES (?, ?, ?, 1, ?, ?, ?, 'render-exhibition-detail-refresh', ?)
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


def _signal_refresh_recent_activity(target_date: str, now: datetime) -> tuple[bool, str]:
    since = (now - timedelta(minutes=SIGNAL_REFRESH_MIN_GAP_MIN)).replace(
        tzinfo=None,
    ).isoformat(timespec="seconds")
    try:
        with db_connect() as conn:
            row = conn.execute(
                """
                SELECT task_name,
                       status,
                       COALESCE(success_at, started_at, finished_at, '') AS activity_at
                  FROM task_runs
                 WHERE run_date = ?
                   AND task_name LIKE 'render_signal_refresh_%'
                   AND (
                        (status = 'running' AND started_at >= ?)
                        OR success_at >= ?
                   )
                 ORDER BY activity_at DESC
                 LIMIT 1
                """,
                (target_date, since, since),
            ).fetchone()
        if row:
            return True, f"recent-{row[1]}:{row[0]}@{row[2]}"
    except Exception as exc:
        print(f"[signal-refresh] recent-activity check failed: {type(exc).__name__}: {exc}", flush=True)
    return False, ""


def _exhibition_refresh_recently_running(target_date: str, now: datetime) -> tuple[bool, str]:
    since = (now - timedelta(minutes=EXHIBITION_REFRESH_MAX_ACTIVE_MIN)).replace(
        tzinfo=None,
    ).isoformat(timespec="seconds")
    try:
        with db_connect() as conn:
            row = conn.execute(
                """
                SELECT task_name, started_at
                  FROM task_runs
                 WHERE run_date = ?
                   AND task_name = 'render_exhibition_detail_refresh'
                   AND status = 'running'
                   AND started_at >= ?
                 ORDER BY started_at DESC
                 LIMIT 1
                """,
                (target_date, since),
            ).fetchone()
        if row:
            return True, f"recent-running:{row[0]}@{row[1]}"
    except Exception as exc:
        print(
            f"[exhibition-detail-refresh] overlap check failed: {type(exc).__name__}: {exc}",
            flush=True,
        )
    return False, ""


def _should_refresh_market_signals(target_date: str, collect_summary: dict, refresh_summary: dict) -> tuple[bool, str]:
    if target_date != datetime.now(JST).date().isoformat():
        return False, "non-today"
    beforeinfo_rows = int(collect_summary.get("beforeinfo_rows", 0) or 0)
    original_rows = int(((collect_summary.get("original") or {}).get("rows_inserted", 0)) or 0)
    refreshed = int(refresh_summary.get("refreshed", 0) or 0)
    if beforeinfo_rows <= 0 and original_rows <= 0 and refreshed <= 0:
        return False, "no-source-change"
    return True, f"beforeinfo_rows={beforeinfo_rows} original_rows={original_rows} refreshed={refreshed}"


def refresh_market_signals_if_needed(target_date: str, collect_summary: dict, refresh_summary: dict) -> dict:
    should_refresh, reason = _should_refresh_market_signals(target_date, collect_summary, refresh_summary)
    if not should_refresh:
        summary = {"target_date": target_date, "triggered": False, "reason": reason}
        print(f"[signal-refresh] {summary}", flush=True)
        return summary

    _ensure_task_runs_table()
    now = datetime.now(JST)
    is_recent, recent_reason = _signal_refresh_recent_activity(target_date, now)
    if is_recent:
        summary = {"target_date": target_date, "triggered": False, "reason": recent_reason}
        print(f"[signal-refresh] {summary}", flush=True)
        return summary

    task_name = f"render_signal_refresh_{now.hour:02d}_{now.minute // 5}_exhibition"
    _record_task(task_name, target_date, "running", detail=reason)
    ok = _run_py(
        ["scripts/prewarm_strategy_pages.py", "--mode", "signals", "--date", target_date],
        timeout=1800,
    )
    _record_task(task_name, target_date, "success" if ok else "failure", detail=reason)
    summary = {
        "target_date": target_date,
        "triggered": True,
        "ok": ok,
        "task_name": task_name,
        "reason": reason,
    }
    print(f"[signal-refresh] {summary}", flush=True)
    return summary


def _parse_race_close_jst(closed_at: object, race_date: str) -> datetime | None:
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


def _find_missing_original_exhibition_races(
    now: datetime,
    *,
    target_date: str,
    past_min: int = ORIGINAL_EXHIBITION_PAST_MIN,
    future_min: int = ORIGINAL_EXHIBITION_FUTURE_MIN,
    limit: int = ORIGINAL_EXHIBITION_LIMIT,
) -> list[tuple[str, int, int, datetime]]:
    supported = sorted(
        int(stadium)
        for stadium, patterns in original_exhibition_collector.SOURCE_PATTERNS.items()
        if patterns
    )
    if not supported:
        return []

    placeholders = ",".join("?" for _ in supported)
    with db_connect() as conn:
        rows = conn.execute(
            f"""
            SELECT r.race_id, r.stadium_number, r.race_number, r.race_closed_at,
                   COUNT(DISTINCT oe.boat_number) AS original_rows,
                   COUNT(DISTINCT CASE
                       WHEN oe.lap_time IS NOT NULL AND oe.lap_time != 0
                       THEN oe.boat_number
                   END) AS lap_rows,
                   COUNT(DISTINCT CASE
                       WHEN oe.turn_time IS NOT NULL AND oe.turn_time != 0
                       THEN oe.boat_number
                   END) AS turn_rows,
                   COUNT(DISTINCT CASE
                       WHEN oe.straight_time IS NOT NULL AND oe.straight_time != 0
                       THEN oe.boat_number
                   END) AS straight_rows
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
    for race_id, stadium, race_no, closed_at, original_rows, lap_rows, turn_rows, straight_rows in rows:
        original_count = int(original_rows or 0)
        metric_counts = [int(lap_rows or 0), int(turn_rows or 0), int(straight_rows or 0)]
        metric_partly_missing = any(0 < count < 6 for count in metric_counts)
        no_useful_metric_rows = original_count > 0 and all(count == 0 for count in metric_counts)
        if original_count >= 6 and not metric_partly_missing and not no_useful_metric_rows:
            continue
        close = _parse_race_close_jst(closed_at, target_date)
        if close is None:
            continue
        mins_until = (close - now).total_seconds() / 60.0
        if mins_until < -abs(past_min) or mins_until > future_min:
            continue
        due.append((str(race_id), int(stadium), int(race_no), close))
        if limit > 0 and len(due) >= limit:
            break
    return due


def _collect_original_exhibition(
    target_date: str,
    due: list[tuple[str, int, int, datetime]],
) -> dict:
    if not due:
        return {
            "races_targeted": 0,
            "pages_fetched": 0,
            "races_found": 0,
            "rows_inserted": 0,
        }
    summary = original_exhibition_collector.collect_for_races(
        date.fromisoformat(target_date),
        [(race_id, stadium, race_no) for race_id, stadium, race_no, _close in due],
        force=False,
        save_html=False,
        pattern_limit=8,
    )
    print(
        "[exhibition-original] "
        f"targeted={summary['races_targeted']} fetched={summary['pages_fetched']} "
        f"found={summary['races_found']} rows={summary['rows_inserted']}",
        flush=True,
    )
    return summary


def collect_live_exhibition(target_date: str, now: datetime | None = None) -> dict:
    """Collect the same live exhibition sources the regular cron used to own.

    Targeting uses the wider recovery window from the old original-exhibition
    catch-up path, so moving ownership to this two-minute cron does not reduce
    coverage.
    """
    now = now or datetime.now(JST)
    if target_date != now.date().isoformat():
        original_due = _find_missing_original_exhibition_races(now, target_date=target_date)
        original_summary = _collect_original_exhibition(target_date, original_due)
        return {
            "target_date": target_date,
            "beforeinfo_due": 0,
            "post_race_incomplete_due": 0,
            "beforeinfo_races": 0,
            "beforeinfo_rows": 0,
            "original_due": len(original_due),
            "original": original_summary,
        }

    beforeinfo_due = live_beforeinfo.find_due_races(
        now,
        window_min=BEFOREINFO_WINDOW_MIN,
        window_max=BEFOREINFO_WINDOW_MAX,
        cooldown_min=BEFOREINFO_COOLDOWN_MIN,
    )
    incomplete_due = live_beforeinfo.find_recent_incomplete_races(
        now,
        past_min=INCOMPLETE_PAST_MIN,
        future_min=INCOMPLETE_FUTURE_MIN,
        limit=INCOMPLETE_LIMIT,
    )
    if incomplete_due:
        print(f"[exhibition-beforeinfo] incomplete_due={len(incomplete_due)}", flush=True)
    post_race_incomplete_due = live_beforeinfo.find_recent_incomplete_races(
        now,
        past_min=POST_RACE_INCOMPLETE_PAST_MIN,
        future_min=POST_RACE_INCOMPLETE_FUTURE_MIN,
        limit=POST_RACE_INCOMPLETE_LIMIT,
    )
    if post_race_incomplete_due:
        print(
            f"[exhibition-beforeinfo] post_race_incomplete_due={len(post_race_incomplete_due)}",
            flush=True,
        )
    beforeinfo_due = live_beforeinfo._merge_due_races(
        beforeinfo_due,
        incomplete_due,
        post_race_incomplete_due,
    )

    original_due = live_beforeinfo._merge_due_races(
        beforeinfo_due,
        _find_missing_original_exhibition_races(now, target_date=target_date),
    )
    print(
        f"[exhibition-beforeinfo] due={len(beforeinfo_due)} "
        f"original_due={len(original_due)}",
        flush=True,
    )

    original_summary = _collect_original_exhibition(target_date, original_due)

    updates: list[tuple[str, dict]] = []
    beforeinfo_summary = {"supabase_rows": 0, "local_rows": 0, "races": 0}

    def flush_updates() -> None:
        if not updates:
            return
        batch_summary = live_beforeinfo.write_updates(
            updates,
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            also_local=False,
        )
        for key in beforeinfo_summary:
            beforeinfo_summary[key] += int(batch_summary.get(key, 0) or 0)
        updates.clear()

    for race_id, stadium, race_no, close in beforeinfo_due:
        print(f"[exhibition-beforeinfo] scrape {race_id} close={close.strftime('%H:%M')}", flush=True)
        page = live_beforeinfo.scrape_one_race(stadium, race_no, now.date())
        if page:
            updates.append((race_id, page))
            if len(updates) >= BEFOREINFO_WRITE_BATCH_SIZE:
                flush_updates()
    flush_updates()

    if beforeinfo_summary["races"] > 0:
        _run_py(["scripts/build_derived_start_stats.py", "--from", target_date, "--to", target_date], timeout=1800)
        _run_py(["scripts/render_cache_predictions.py", "--date", target_date], timeout=1800)
        _run_py(["scripts/generate_start_predictions.py", "--date", target_date], timeout=900)

    return {
        "target_date": target_date,
        "beforeinfo_due": len(beforeinfo_due),
        "post_race_incomplete_due": len(post_race_incomplete_due),
        "beforeinfo_races": beforeinfo_summary["races"],
        "beforeinfo_rows": beforeinfo_summary["supabase_rows"],
        "original_due": len(original_due),
        "original": original_summary,
    }


def _source_timestamp(value: object) -> float:
    if value is None:
        return 0.0
    raw = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        # Render stores the legacy naive live_updated_at value in UTC.
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _due_races(target_date: str, delay_seconds: int, limit: int) -> list[str]:
    from src.web import app as web_app

    page_cache_prefix = web_app._race_detail_page_cache_key("")
    with db_connect() as conn:
        rows = conn.execute(
            """
            SELECT r.race_id,
                   MAX(p.live_updated_at) AS preview_updated_at,
                   MAX(o.collected_at) AS original_updated_at,
                   CAST(MAX(c.updated_at) AS DOUBLE PRECISION) AS page_updated_at
              FROM races r
              LEFT JOIN race_previews p ON p.race_id = r.race_id
              LEFT JOIN race_original_exhibitions o ON o.race_id = r.race_id
              LEFT JOIN page_html_cache c
                ON c.cache_key = ? || r.race_id
             WHERE r.race_date = ?
             GROUP BY r.race_id
             ORDER BY r.race_id
            """,
            (page_cache_prefix, target_date),
        ).fetchall()

    now_ts = time.time()
    due: list[str] = []
    for race_id, preview_updated_at, original_updated_at, page_updated_at in rows:
        source_ts = max(
            _source_timestamp(preview_updated_at),
            _source_timestamp(original_updated_at),
        )
        page_ts = float(page_updated_at or 0)
        if source_ts and source_ts + delay_seconds <= now_ts and page_ts < source_ts:
            due.append(str(race_id))
            if limit > 0 and len(due) >= limit:
                break
    return due


def refresh(target_date: str, *, delay_seconds: int = 60, limit: int = 12) -> dict:
    race_ids = _due_races(target_date, delay_seconds, limit)
    if not race_ids:
        summary = {"target_date": target_date, "due": 0, "refreshed": 0, "failed": 0}
        print(f"[exhibition-detail-refresh] {summary}", flush=True)
        return summary

    # Import the web application only after live exhibition rows have already
    # been collected.  The app import/page render path is the heavy part of this
    # cron; if Render cancels the process during page refresh, the source rows
    # should still have been written so the detail page can render facts from DB.
    from src.web import app as web_app

    app = web_app.create_app(version=config.DEFAULT_MODEL_VERSION)
    app.testing = True
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["is_member"] = True

    refreshed = 0
    failures: list[dict] = []
    for race_id in race_ids:
        try:
            info = web_app._race_basic_info(race_id)
            conditions = web_app._race_current_conditions(race_id)
            web_app._write_json_cache(f"race_conditions:{race_id}", conditions)
            for boat in range(1, 7):
                payload = web_app._motor_history_payload(race_id, boat, info=info)
                if payload is not None:
                    web_app._write_json_cache(
                        f"motor_history_{MOTOR_CACHE_VERSION}:{race_id}:{boat}", payload
                    )
            web_app.invalidate_cache()
            response = client.get(f"/race/{race_id}?recompute=1")
            if response.status_code != 200:
                raise RuntimeError(f"page status={response.status_code}")
            refreshed += 1
            print(f"[exhibition-detail-refresh] race_id={race_id} status=200", flush=True)
        except Exception as exc:  # noqa: BLE001
            failures.append({"race_id": race_id, "error": f"{type(exc).__name__}: {exc}"})
            print(
                f"[exhibition-detail-refresh] failed race_id={race_id} "
                f"error={type(exc).__name__}: {exc}",
                flush=True,
            )
    summary = {
        "target_date": target_date,
        "due": len(race_ids),
        "race_ids": race_ids,
        "refreshed": refreshed,
        "failed": len(failures),
        "failures": failures,
    }
    print(f"[exhibition-detail-refresh] {summary}", flush=True)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=datetime.now(JST).date().isoformat())
    parser.add_argument("--delay-seconds", type=int, default=60)
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--skip-collect", action="store_true")
    args = parser.parse_args()
    task_name = "render_exhibition_detail_refresh"
    _ensure_task_runs_table()
    now = datetime.now(JST)
    is_running, running_reason = _exhibition_refresh_recently_running(args.date, now)
    if is_running:
        detail = json.dumps(
            {"skipped": True, "reason": running_reason},
            ensure_ascii=False,
        )
        print(
            f"[exhibition-detail-refresh] skip overlapping run reason={running_reason}",
            flush=True,
        )
        record_cron_run(task_name, args.date, "success", detail=detail)
        return 0

    record_cron_run(task_name, args.date, "running")
    _record_task(task_name, args.date, "running")
    collect_summary: dict = {"skipped": True}
    signal_summary: dict = {"triggered": False, "ok": True, "reason": "not-run"}
    validation_summary: dict = {"status": "ok", "skipped": True, "reason": "no-refreshed-races"}
    try:
        if not args.skip_collect:
            collect_summary = collect_live_exhibition(args.date)
            print(f"[exhibition-collect] {collect_summary}", flush=True)
        summary = refresh(args.date, delay_seconds=args.delay_seconds, limit=args.limit)
        if summary.get("race_ids"):
            from scripts.check_post_run_integrity import (
                run_checks as run_post_run_checks,
                scopes_for_stage,
            )

            validation_summary = run_post_run_checks(
                args.date,
                scopes_for_stage("exhibition"),
                list(summary["race_ids"]),
                persist=True,
            )
            print(
                "[exhibition-detail-refresh] validation="
                + json.dumps(validation_summary, ensure_ascii=False),
                flush=True,
            )
        signal_summary = refresh_market_signals_if_needed(args.date, collect_summary, summary)
    except Exception as exc:
        _record_task(task_name, args.date, "failure", detail=f"{type(exc).__name__}: {exc}"[:1000])
        record_cron_run(
            task_name,
            args.date,
            "failure",
            detail=f"{type(exc).__name__}: {exc}"[:1000],
        )
        raise

    succeeded = summary["failed"] == 0 and (
        not signal_summary.get("triggered") or bool(signal_summary.get("ok"))
    ) and validation_summary.get("status") != "error"
    detail = json.dumps(
        {
            "beforeinfo_due": int(collect_summary.get("beforeinfo_due", 0) or 0),
            "beforeinfo_races": int(collect_summary.get("beforeinfo_races", 0) or 0),
            "beforeinfo_rows": int(collect_summary.get("beforeinfo_rows", 0) or 0),
            "refresh_due": summary["due"],
            "refreshed": summary["refreshed"],
            "failed": summary["failed"],
            "validation_status": validation_summary.get("status"),
            "signal_refresh_triggered": bool(signal_summary.get("triggered")),
            "signal_refresh_ok": bool(signal_summary.get("ok")),
            "signal_refresh_reason": signal_summary.get("reason"),
        },
        ensure_ascii=False,
    )
    record_cron_run(
        task_name,
        args.date,
        "success" if succeeded else "failure",
        detail=detail,
    )
    _record_task(task_name, args.date, "success" if succeeded else "failure", detail=detail)
    return 0 if succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
