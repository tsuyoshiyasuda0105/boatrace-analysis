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
from src.web.app import _race_detail_tag_snapshot  # noqa: E402


def prewarm(target_date: str) -> dict[str, int]:
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

    summary = {"races": len(rows), "cached": 0, "failed": 0}
    for (race_id,) in rows:
        try:
            payload = _race_detail_tag_snapshot(str(race_id), recompute=True)
            if payload:
                summary["cached"] += 1
            else:
                summary["failed"] += 1
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
