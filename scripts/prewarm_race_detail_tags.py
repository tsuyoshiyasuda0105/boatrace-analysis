"""Precompute race-detail display tags for every race on one date."""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
os.environ.setdefault("BOATRACE_TASK_TRIGGER", "render-prewarm")

from src.db.connection import connect as db_connect  # noqa: E402
from src.web.app import JST, _race_detail_tag_snapshot  # noqa: E402,F401


def prewarm(target_date: str) -> dict[str, int]:
    with db_connect() as conn:
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
    summary = {"races": len(race_ids), "cached": 0, "failed": 0}
    if not race_ids:
        print(f"[race-detail-tags] date={target_date} {summary}", flush=True)
        return summary

    print(f"[race-detail-tags] start date={target_date} races={len(race_ids)}", flush=True)
    for idx, race_id in enumerate(race_ids, start=1):
        try:
            payload = _race_detail_tag_snapshot(str(race_id), recompute=True)
            if not isinstance(payload, dict) or not payload.get("boats"):
                summary["failed"] += 1
                print(
                    f"[race-detail-tags] empty payload race_id={race_id}",
                    flush=True,
                )
                continue
            summary["cached"] += 1
            if idx == 1 or idx % 25 == 0 or idx == len(race_ids):
                print(
                    f"[race-detail-tags] progress {idx}/{len(race_ids)} "
                    f"cached={summary['cached']} failed={summary['failed']}",
                    flush=True,
                )
        except Exception as exc:  # noqa: BLE001
            summary["failed"] += 1
            print(
                f"[race-detail-tags] failed race_id={race_id} "
                f"error={type(exc).__name__}: {exc}",
                flush=True,
            )
    print(f"[race-detail-tags] date={target_date} {summary}", flush=True)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=date.today().isoformat())
    args = parser.parse_args()
    summary = prewarm(args.date)
    return 0 if summary["races"] > 0 and summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
