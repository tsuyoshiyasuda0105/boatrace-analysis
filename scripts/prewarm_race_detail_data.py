"""Build all stable race-detail data once per JST day.

The order is intentional: racer detail, motor history, display tags, then the
complete HTML page.  Live exhibition changes are handled separately by
``refresh_race_detail_after_exhibition.py``.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
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
os.environ.setdefault("BOATRACE_ALLOW_EXPENSIVE_WEB_RECOMPUTE", "1")

from scripts.prewarm_race_detail_pages import prewarm as prewarm_pages  # noqa: E402
from scripts.check_post_run_integrity import run_checks as run_post_run_checks  # noqa: E402
from src.db.cron_run_log import record_cron_run  # noqa: E402
from src.db.connection import connect as db_connect  # noqa: E402
from src.web import app as web_app  # noqa: E402


JST = ZoneInfo("Asia/Tokyo")
MOTOR_CACHE_VERSION = "v9"


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


def _race_infos(target_date: str) -> dict[str, dict]:
    """Load the immutable race headers in one query instead of 156 connections."""
    with db_connect() as conn:
        rows = conn.execute(
            """
            SELECT r.race_id, r.race_date, r.stadium_number, r.race_number,
                   r.race_grade_number, r.race_title, r.race_subtitle,
                   r.race_closed_at, s.name
              FROM races r
              LEFT JOIN stadiums s ON s.stadium_number = r.stadium_number
             WHERE r.race_date = ?
             ORDER BY r.stadium_number, r.race_number
            """,
            (target_date,),
        ).fetchall()
    keys = (
        "race_id", "race_date", "stadium_number", "race_number",
        "race_grade_number", "race_title", "race_subtitle", "race_closed_at",
        "stadium_name",
    )
    return {str(row[0]): dict(zip(keys, row)) for row in rows}


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


def _prewarm_motors(
    race_ids: list[str],
    race_infos: dict[str, dict],
    failures: list[dict],
    *,
    workers: int,
) -> int:
    """Build all motor histories with bounded DB concurrency."""
    jobs: list[tuple[str, int, dict]] = []
    for race_id in race_ids:
        info = race_infos.get(race_id)
        if not info:
            _record_failure(failures, "motor", race_id, None, RuntimeError("race info not found"))
            continue
        jobs.extend((race_id, boat, info) for boat in range(1, 7))

    def build(job: tuple[str, int, dict]) -> tuple[str, int, bool]:
        race_id, boat, info = job
        payload = web_app._motor_history_payload(race_id, boat, info=info)
        if payload is None:
            return race_id, boat, False
        web_app._write_json_cache(
            f"motor_history_{MOTOR_CACHE_VERSION}:{race_id}:{boat}", payload
        )
        return race_id, boat, True

    completed = 0
    generated = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        future_jobs = {executor.submit(build, job): job for job in jobs}
        for future in as_completed(future_jobs):
            race_id, boat, _info = future_jobs[future]
            completed += 1
            try:
                _race_id, _boat, written = future.result()
                generated += int(written)
            except Exception as exc:  # noqa: BLE001
                _record_failure(failures, "motor", race_id, boat, exc)
            if completed == 1 or completed % 25 == 0 or completed == len(jobs):
                print(
                    f"[race-detail-daily] motor progress={completed}/{len(jobs)} "
                    f"written={generated} failed={len(failures)}",
                    flush=True,
                )
    return generated


def prewarm(target_date: str, *, phase: str = "all", motor_workers: int = 4) -> dict:
    race_infos = _race_infos(target_date)
    race_ids = list(race_infos)
    failures: list[dict] = []
    counts = {"racer": 0, "motor": 0, "tags": 0}
    started = time.perf_counter()

    # Player data is stable for the day and is built first.
    if phase == "all":
        for race_id in race_ids:
            info = race_infos.get(race_id)
            for boat in range(1, 7):
                try:
                    payload = web_app._racer_course_detail_payload(race_id, boat, info=info)
                    if payload is not None:
                        web_app._write_json_cache(f"racer_detail:{race_id}:{boat}", payload)
                        counts["racer"] += 1
                except Exception as exc:  # noqa: BLE001
                    _record_failure(failures, "racer", race_id, boat, exc)

    # Motor history follows player data, as requested.
    counts["motor"] = _prewarm_motors(
        race_ids,
        race_infos,
        failures,
        workers=motor_workers,
    )

    # Tags must exist before complete HTML is rendered.
    if phase == "all":
        for race_id in race_ids:
            try:
                if web_app._race_detail_tag_snapshot(race_id, recompute=True):
                    counts["tags"] += 1
            except Exception as exc:  # noqa: BLE001
                _record_failure(failures, "tags", race_id, None, exc)

    page_summary = prewarm_pages(target_date) if phase == "all" else {"generated": 0, "failed": 0}
    validation_scopes = ["detail_rows", "motor_cache"]
    if phase == "all":
        validation_scopes.append("detail_cache")
    validation = run_post_run_checks(target_date, validation_scopes, race_ids, persist=True)
    print("[race-detail-daily] validation=" + json.dumps(validation, ensure_ascii=False), flush=True)
    summary = {
        "target_date": target_date,
        "phase": phase,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "races": len(race_ids),
        **counts,
        "pages": page_summary,
        "validation": validation,
        "failed": len(failures) + int(page_summary.get("failed", 0)),
        "failures": failures,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    print("[race-detail-daily] summary=" + json.dumps(summary, ensure_ascii=False), flush=True)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=datetime.now(JST).date().isoformat())
    parser.add_argument("--phase", choices=("all", "motor"), default="all")
    parser.add_argument(
        "--motor-workers",
        type=int,
        default=int(os.getenv("BOATRACE_MOTOR_PREWARM_WORKERS", "4")),
    )
    args = parser.parse_args()
    task_name = f"render_race_detail_{args.phase}"
    record_cron_run(task_name, args.date, "running")
    try:
        summary = prewarm(args.date, phase=args.phase, motor_workers=args.motor_workers)
    except Exception as exc:
        record_cron_run(
            task_name,
            args.date,
            "failure",
            detail=f"{type(exc).__name__}: {exc}"[:1000],
        )
        raise

    validation_status = str((summary.get("validation") or {}).get("status") or "error")
    succeeded = summary["races"] > 0 and summary["failed"] == 0 and validation_status != "error"
    detail = json.dumps(
        {
            "races": summary["races"],
            "racer": summary["racer"],
            "motor": summary["motor"],
            "tags": summary["tags"],
            "pages": int((summary.get("pages") or {}).get("succeeded", 0)),
            "failed": summary["failed"],
            "validation_status": validation_status,
            "elapsed_seconds": summary["elapsed_seconds"],
        },
        ensure_ascii=False,
    )
    record_cron_run(
        task_name,
        args.date,
        "success" if succeeded else "failure",
        detail=detail,
    )
    return 0 if succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
