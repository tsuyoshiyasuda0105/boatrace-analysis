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


def _race_ids(
    target_date: str,
    race_id: str | None,
    limit: int | None,
    *,
    conn=None,
) -> list[str]:
    if race_id:
        return [race_id]
    owns_connection = conn is None
    if owns_connection:
        conn = db_connect()
    try:
        rows = conn.execute(
            """
            SELECT race_id
              FROM races
             WHERE race_date = ?
             ORDER BY stadium_number, race_number
            """,
            (target_date,),
        ).fetchall()
    finally:
        if owns_connection:
            conn.close()
    ids = [str(row[0]) for row in rows]
    return ids[:limit] if limit else ids


def _missing_persistent_page_ids(race_ids: list[str], *, conn=None) -> list[str]:
    if not race_ids:
        return []
    keyed_ids = {
        web_app._race_detail_page_cache_key(race_id): race_id
        for race_id in race_ids
    }
    found: set[str] = set()
    cache_keys = list(keyed_ids)
    owns_connection = conn is None
    if owns_connection:
        conn = db_connect()
    try:
        for start in range(0, len(cache_keys), 900):
            chunk = cache_keys[start : start + 900]
            placeholders = ",".join("?" for _ in chunk)
            rows = conn.execute(
                f"SELECT cache_key FROM page_html_cache WHERE cache_key IN ({placeholders})",
                tuple(chunk),
            ).fetchall()
            found.update(str(row[0]) for row in rows)
    finally:
        if owns_connection:
            conn.close()
    return [keyed_ids[key] for key in cache_keys if key not in found]


def prewarm(
    target_date: str,
    *,
    race_id: str | None = None,
    limit: int | None = None,
    missing_only: bool = False,
    retry_missing: int = 1,
    budget_sec: float | None = None,
) -> dict:
    if budget_sec is not None and budget_sec <= 0:
        raise ValueError("budget_sec must be positive")
    _require_postgres()
    with db_connect() as conn:
        requested_ids = _race_ids(target_date, race_id, None, conn=conn)
        ids = (
            _missing_persistent_page_ids(requested_ids, conn=conn)
            if missing_only
            else requested_ids
        )
    if limit:
        ids = ids[:limit]
    app = web_app.create_app(version=config.DEFAULT_MODEL_VERSION)
    app.testing = True
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["is_member"] = True

    started = time.perf_counter()
    durations: list[float] = []
    failures_by_race: dict[str, dict[str, object]] = {}
    processed_ids: list[str] = []
    budget_exhausted = False

    def generate(rid: str, index_label: str) -> None:
        race_started = time.perf_counter()
        response = client.get(f"/race/{rid}?recompute=1")
        elapsed = time.perf_counter() - race_started
        durations.append(elapsed)
        if response.status_code != 200:
            failures_by_race[rid] = {"race_id": rid, "status": response.status_code}
        print(
            f"[race-detail-page] {index_label} race_id={rid} "
            f"status={response.status_code} elapsed={elapsed:.3f}s "
            f"bytes={len(response.data)}",
            flush=True,
        )

    with db_connect() as conn:
        prefetched = web_app._prefetch_race_detail_page_inputs(
            ids,
            config.DEFAULT_MODEL_VERSION,
            conn,
        )
        with web_app._use_race_detail_prewarm_context(conn, prefetched):
            for index, rid in enumerate(ids, 1):
                generate(rid, f"{index}/{len(ids)}")
                processed_ids.append(rid)
                if budget_sec is not None and time.perf_counter() - started >= budget_sec:
                    budget_exhausted = len(processed_ids) < len(ids)
                    break

            persistent_missing = _missing_persistent_page_ids(processed_ids, conn=conn)
            for retry in range(1, max(0, retry_missing) + 1):
                if not persistent_missing:
                    break
                print(
                    f"[race-detail-page] persistent cache retry={retry} "
                    f"missing={len(persistent_missing)}",
                    flush=True,
                )
                for index, rid in enumerate(persistent_missing, 1):
                    if budget_sec is not None and time.perf_counter() - started >= budget_sec:
                        budget_exhausted = True
                        break
                    web_app._CACHE.clear()
                    web_app._PAGE_HTML_MEM_CACHE.clear()
                    generate(rid, f"retry-{retry}:{index}/{len(persistent_missing)}")
                persistent_missing = _missing_persistent_page_ids(
                    persistent_missing,
                    conn=conn,
                )
                if budget_exhausted:
                    break

    for rid in persistent_missing:
        failures_by_race[rid] = {
            "race_id": rid,
            "status": "persistent_cache_missing",
        }

    total = time.perf_counter() - started
    failures = list(failures_by_race.values())
    success_count = len(processed_ids) - len(failures)
    remaining_count = (len(ids) - len(processed_ids)) + len(persistent_missing)
    cache_read_samples: list[dict[str, object]] = []
    sample_ids = list(dict.fromkeys(
        [processed_ids[0], processed_ids[len(processed_ids) // 2], processed_ids[-1]]
        if processed_ids else []
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
        "requested_races": len(requested_ids),
        "skipped_existing": len(requested_ids) - len(ids) if missing_only else 0,
        "races": len(ids),
        "attempted": len(processed_ids),
        "succeeded": success_count,
        "failed": len(failures),
        "persistent_missing": remaining_count,
        "remaining": remaining_count,
        "budget_exhausted": budget_exhausted,
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
    parser.add_argument("--missing-only", action="store_true")
    parser.add_argument("--retry-missing", type=int, default=1)
    parser.add_argument("--budget-sec", type=float)
    parser.add_argument("--report")
    args = parser.parse_args()
    summary = prewarm(
        args.date,
        race_id=args.race_id,
        limit=args.limit,
        missing_only=args.missing_only,
        retry_missing=args.retry_missing,
        budget_sec=args.budget_sec,
    )
    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return 0 if summary["requested_races"] > 0 and summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
