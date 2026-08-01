"""Build all stable race-detail data once per JST day.

The order is intentional: racer detail, motor history, display tags, then the
complete HTML page.  Live exhibition changes are handled separately by
``refresh_race_detail_after_exhibition.py``.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
os.environ.setdefault("BOATRACE_TASK_TRIGGER", "render-detail-daily")

from scripts.prewarm_race_detail_pages import prewarm as prewarm_pages  # noqa: E402
from src.db.connection import connect as db_connect  # noqa: E402
from src.web import app as web_app  # noqa: E402


JST = ZoneInfo("Asia/Tokyo")
MOTOR_CACHE_VERSION = "v8"


def _race_ids(target_date: str) -> list[str]:
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
    return [str(row[0]) for row in rows]


def _record_failure(failures: list[dict], stage: str, race_id: str, boat: int | None, exc: Exception) -> None:
    failures.append(
        {
            "stage": stage,
            "race_id": race_id,
            "boat_number": boat,
            "error": f"{type(exc).__name__}: {exc}",
        }
    )
    print(
        f"[race-detail-daily] failed stage={stage} race_id={race_id} "
        f"boat={boat or '-'} error={type(exc).__name__}: {exc}",
        flush=True,
    )


def prewarm(target_date: str) -> dict:
    race_ids = _race_ids(target_date)
    failures: list[dict] = []
    counts = {"racer": 0, "motor": 0, "tags": 0}
    started = time.perf_counter()

    # Player data is stable for the day and is built first.
    for race_id in race_ids:
        info = web_app._race_basic_info(race_id)
        for boat in range(1, 7):
            try:
                payload = web_app._racer_course_detail_payload(race_id, boat, info=info)
                if payload is not None:
                    web_app._write_json_cache(f"racer_detail:{race_id}:{boat}", payload)
                    counts["racer"] += 1
            except Exception as exc:  # noqa: BLE001
                _record_failure(failures, "racer", race_id, boat, exc)

    # Motor history follows player data, as requested.
    for race_id in race_ids:
        info = web_app._race_basic_info(race_id)
        for boat in range(1, 7):
            try:
                payload = web_app._motor_history_payload(race_id, boat, info=info)
                if payload is not None:
                    web_app._write_json_cache(
                        f"motor_history_{MOTOR_CACHE_VERSION}:{race_id}:{boat}", payload
                    )
                    counts["motor"] += 1
            except Exception as exc:  # noqa: BLE001
                _record_failure(failures, "motor", race_id, boat, exc)

    # Tags must exist before complete HTML is rendered.
    for race_id in race_ids:
        try:
            if web_app._race_detail_tag_snapshot(race_id, recompute=True):
                counts["tags"] += 1
        except Exception as exc:  # noqa: BLE001
            _record_failure(failures, "tags", race_id, None, exc)

    page_summary = prewarm_pages(target_date)
    summary = {
        "target_date": target_date,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "races": len(race_ids),
        **counts,
        "pages": page_summary,
        "failed": len(failures) + int(page_summary.get("failed", 0)),
        "failures": failures,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    print("[race-detail-daily] summary=" + json.dumps(summary, ensure_ascii=False), flush=True)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=datetime.now(JST).date().isoformat())
    args = parser.parse_args()
    summary = prewarm(args.date)
    return 0 if summary["races"] > 0 and summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
