"""Persist Render cron health in the shared ``task_runs`` table."""
from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

from src.db.connection import connect as db_connect


JST = ZoneInfo("Asia/Tokyo")


def record_cron_run(
    task_name: str,
    run_date: str,
    status: str,
    *,
    detail: str | None = None,
    trigger: str | None = None,
) -> None:
    """Record one cron attempt without counting its final status twice."""
    if status not in {"running", "success", "failure"}:
        raise ValueError(f"unsupported cron status: {status}")

    now_iso = datetime.now(JST).replace(tzinfo=None).isoformat(timespec="seconds")
    trigger_name = trigger or os.getenv("BOATRACE_TASK_TRIGGER", "render-cron")
    success_at = now_iso if status == "success" else None

    with db_connect() as conn:
        if status == "running":
            conn.execute(
                """
                INSERT INTO task_runs
                    (task_name, run_date, status, run_count, started_at, finished_at,
                     success_at, trigger, detail)
                VALUES (?, ?, 'running', 1, ?, NULL, NULL, ?, ?)
                ON CONFLICT (task_name, run_date) DO UPDATE SET
                    status = 'running',
                    run_count = task_runs.run_count + 1,
                    started_at = EXCLUDED.started_at,
                    finished_at = NULL,
                    trigger = EXCLUDED.trigger,
                    detail = EXCLUDED.detail
                """,
                (task_name, run_date, now_iso, trigger_name, detail),
            )
        else:
            conn.execute(
                """
                INSERT INTO task_runs
                    (task_name, run_date, status, run_count, started_at, finished_at,
                     success_at, trigger, detail)
                VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?)
                ON CONFLICT (task_name, run_date) DO UPDATE SET
                    status = EXCLUDED.status,
                    finished_at = EXCLUDED.finished_at,
                    success_at = COALESCE(EXCLUDED.success_at, task_runs.success_at),
                    trigger = EXCLUDED.trigger,
                    detail = EXCLUDED.detail
                """,
                (
                    task_name,
                    run_date,
                    status,
                    now_iso,
                    now_iso,
                    success_at,
                    trigger_name,
                    detail,
                ),
            )
        conn.commit()
