"""Pre-render complete race-detail HTML for one race date.

The generated HTML is stored in ``page_html_cache`` and served by the web
route with a single cache lookup.  This script is intentionally standalone so
it can be measured manually before being attached to a dedicated Render cron.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
os.environ.setdefault("BOATRACE_TASK_TRIGGER", "render-detail-prewarm")

import config  # noqa: E402
from src.db.connection import connect as db_connect  # noqa: E402
from src.web import app as web_app  # noqa: E402


JST = ZoneInfo("Asia/Tokyo")


def _require_postgres() -> None:
    db_url = os.getenv("DATABASE_URL", "").strip()
    if not db_url.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("DATABASE_URL must point to Supabase/Postgres")


def _race_ids(target_date: str, race_id: str | None, limit: int | None) -> list[str]:
    if race_id:
        return [race_id]
    with db_connect() as conn:
        rows = conn.execute(
            """
            SELECT race_id
              FROM races
             WHERE race_date = ?
             ORDER BY stadium_number, race_number
            """,
            (target_date,),
        ).fetchall()
    ids = [str(row[0]) for row in rows]
    return ids[:limit] if limit else ids


def prewarm(
    target_date: str,
    *,
    race_id: str | None = None,
    limit: int | None = None,
) -> dict:
    _require_postgres()
    ids = _race_ids(target_date, race_id, limit)
    app = web_app.create_app(version=config.DEFAULT_MODEL_VERSION)
    app.testing = True
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["is_member"] = True

    started = time.perf_counter()
    durations: list[float] = []
    failures: list[dict[str, object]] = []
    for index, rid in enumerate(ids, 1):
        race_started = time.perf_counter()
        response = client.get(f"/race/{rid}?recompute=1")
        elapsed = time.perf_counter() - race_started
        durations.append(elapsed)
        if response.status_code != 200:
            failures.append({"race_id": rid, "status": response.status_code})
        print(
            f"[race-detail-page] {index}/{len(ids)} race_id={rid} "
            f"status={response.status_code} elapsed={elapsed:.3f}s "
            f"bytes={len(response.data)}",
            flush=True,
        )

    total = time.perf_counter() - started
    success_count = len(ids) - len(failures)
    cache_read_samples: list[dict[str, object]] = []
    sample_ids = list(dict.fromkeys(
        [ids[0], ids[len(ids) // 2], ids[-1]] if ids else []
    ))
    for rid in sample_ids:
        # Force this verification through the persistent page cache rather than
        # the view decorator or process-local JSON/HTML dictionaries.
        web_app._CACHE.clear()
        web_app._PAGE_HTML_MEM_CACHE.clear()
        read_started = time.perf_counter()
        response = client.get(f"/race/{rid}")
        cache_read_samples.append(
            {
                "race_id": rid,
                "status": response.status_code,
                "elapsed_seconds": round(time.perf_counter() - read_started, 3),
                "bytes": len(response.data),
            }
        )
    summary = {
        "target_date": target_date,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "races": len(ids),
        "succeeded": success_count,
        "failed": len(failures),
        "elapsed_seconds": round(total, 3),
        "average_seconds": round(statistics.mean(durations), 3) if durations else 0.0,
        "median_seconds": round(statistics.median(durations), 3) if durations else 0.0,
        "min_seconds": round(min(durations), 3) if durations else 0.0,
        "max_seconds": round(max(durations), 3) if durations else 0.0,
        "cache_read_samples": cache_read_samples,
        "failures": failures,
    }
    print("[race-detail-page] summary=" + json.dumps(summary, ensure_ascii=False), flush=True)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=datetime.now(JST).date().isoformat())
    parser.add_argument("--race-id")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--report")
    args = parser.parse_args()
    summary = prewarm(args.date, race_id=args.race_id, limit=args.limit)
    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return 0 if summary["races"] > 0 and summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
