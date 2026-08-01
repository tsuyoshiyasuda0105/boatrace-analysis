"""Refresh only race-detail caches whose exhibition data settled one minute ago."""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
os.environ.setdefault("BOATRACE_TASK_TRIGGER", "render-exhibition-detail-refresh")

import config  # noqa: E402
from src.db.connection import connect as db_connect  # noqa: E402
from src.web import app as web_app  # noqa: E402


MOTOR_CACHE_VERSION = "v8"
JST = ZoneInfo("Asia/Tokyo")


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
    with db_connect() as conn:
        rows = conn.execute(
            """
            SELECT r.race_id,
                   MAX(p.live_updated_at) AS preview_updated_at,
                   MAX(o.collected_at) AS original_updated_at,
                   MAX(c.updated_at) AS page_updated_at
              FROM races r
              LEFT JOIN race_previews p ON p.race_id = r.race_id
              LEFT JOIN race_original_exhibitions o ON o.race_id = r.race_id
              LEFT JOIN page_html_cache c
                ON c.cache_key = 'race_detail_page:v1:' || r.race_id
             WHERE r.race_date = ?
             GROUP BY r.race_id
             ORDER BY r.race_id
            """,
            (target_date,),
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
            web_app._clear_web_caches()
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
    args = parser.parse_args()
    summary = refresh(args.date, delay_seconds=args.delay_seconds, limit=args.limit)
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
