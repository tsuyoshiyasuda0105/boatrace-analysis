"""Persist Render cron health in the shared ``task_runs`` table."""
from __future__ import annotations

from src.db.connection import connect as db_connect
from src.db.cron_runtime import record_task_run


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

    with db_connect() as conn:
        record_task_run(
            conn,
            task_name,
            run_date,
            status,
            detail=detail,
            increment=status == "running",
            trigger=trigger,
        )
