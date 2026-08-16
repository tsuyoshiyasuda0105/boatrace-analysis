"""Precompute race-detail display tags for every race on one date."""
from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
from datetime import date
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
os.environ.setdefault("BOATRACE_TASK_TRIGGER", "render-prewarm")

from src.db.connection import connect as db_connect  # noqa: E402
from src.web.app import (  # noqa: E402
    JST,
    _prefetch_race_detail_tag_inputs,
    _race_detail_tag_cache_key,
    _race_detail_tag_snapshot,
    _use_race_detail_prewarm_context,
)


def _missing_cached_race_ids(race_ids: list[str], *, conn=None) -> list[str]:
    if not race_ids:
        return []
    keyed_ids = {_race_detail_tag_cache_key(race_id): race_id for race_id in race_ids}
    cache_keys = list(keyed_ids)
    found: set[str] = set()
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


def prewarm(target_date: str, *, budget_sec: float | None = None) -> dict[str, object]:
    if budget_sec is not None and budget_sec <= 0:
        raise ValueError("budget_sec must be positive")
    conn = db_connect()
    try:
        race_rows = conn.execute(
            """
            SELECT race_id
              FROM races
             WHERE race_date = ?
             ORDER BY stadium_number, race_number
            """,
            (target_date,),
        ).fetchall()
        race_ids = [str(row[0]) for row in race_rows]
        missing_ids = _missing_cached_race_ids(race_ids, conn=conn)
        summary: dict[str, object] = {
            "races": len(race_ids),
            "skipped_existing": len(race_ids) - len(missing_ids),
            "attempted": 0,
            "cached": 0,
            "failed": 0,
            "remaining": len(missing_ids),
            "budget_exhausted": False,
        }
        if not race_ids:
            print(f"[race-detail-tags] date={target_date} {summary}", flush=True)
            return summary

        print(
            f"[race-detail-tags] start date={target_date} races={len(race_ids)} "
            f"missing={len(missing_ids)} skipped_existing={summary['skipped_existing']} "
            f"budget_sec={budget_sec}",
            flush=True,
        )
        started = time.perf_counter()
        durations: list[float] = []
        # The prefetch and every per-race cache write borrow this one connection.
        with _use_race_detail_prewarm_context(conn, {}):
            prefetched = _prefetch_race_detail_tag_inputs(missing_ids, conn)
        context = _use_race_detail_prewarm_context(conn, prefetched)
        with context:
            for idx, race_id in enumerate(missing_ids, start=1):
                race_started = time.perf_counter()
                try:
                    payload = _race_detail_tag_snapshot(str(race_id), recompute=True)
                    if not isinstance(payload, dict) or not payload.get("boats"):
                        summary["failed"] = int(summary["failed"]) + 1
                        print(
                            f"[race-detail-tags] empty payload race_id={race_id}",
                            flush=True,
                        )
                    else:
                        # _race_detail_tag_snapshot writes through page_html_cache and
                        # commits before returning, so every completed race is durable.
                        summary["cached"] = int(summary["cached"]) + 1
                except Exception as exc:  # noqa: BLE001
                    summary["failed"] = int(summary["failed"]) + 1
                    print(
                        f"[race-detail-tags] failed race_id={race_id} "
                        f"error={type(exc).__name__}: {exc}",
                        flush=True,
                    )
                elapsed = time.perf_counter() - race_started
                durations.append(elapsed)
                summary["attempted"] = idx
                summary["remaining"] = len(missing_ids) - int(summary["cached"])
                print(
                    f"[race-detail-tags] race {idx}/{len(missing_ids)} race_id={race_id} "
                    f"elapsed={elapsed:.3f}s cached={summary['cached']} "
                    f"failed={summary['failed']} remaining={summary['remaining']}",
                    flush=True,
                )
                if budget_sec is not None and time.perf_counter() - started >= budget_sec:
                    summary["budget_exhausted"] = int(summary["remaining"]) > 0
                    break
        total = time.perf_counter() - started
        summary.update(
            {
                "elapsed_seconds": round(total, 3),
                "average_seconds": round(statistics.mean(durations), 3) if durations else 0.0,
                "median_seconds": round(statistics.median(durations), 3) if durations else 0.0,
                "min_seconds": round(min(durations), 3) if durations else 0.0,
                "max_seconds": round(max(durations), 3) if durations else 0.0,
            }
        )
        print(f"[race-detail-tags] date={target_date} {summary}", flush=True)
        return summary
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--budget-sec", type=float)
    args = parser.parse_args()
    summary = prewarm(args.date, budget_sec=args.budget_sec)
    return 0 if int(summary["races"]) > 0 and int(summary["failed"]) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
