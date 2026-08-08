"""
明日分の cron / 高ROI 事前準備の readiness 確認。

目的:
  - 23:30 JST 前後で「明日データがまだ無いのは正常か」を判定
  - races / entries / predictions / start predictions / task_runs の揃い具合を確認
  - 高ROI 候補ページで必要な前提が揃っているかを簡易表示

usage:
  python scripts/check_tomorrow_readiness.py
  python scripts/check_tomorrow_readiness.py --date 2026-08-09
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.db.connection import connect as db_connect

JST = ZoneInfo("Asia/Tokyo")


@dataclass(frozen=True)
class TomorrowReadiness:
    state: str
    reason: str


def _now_jst() -> datetime:
    return datetime.now(JST)


def evaluate_tomorrow_readiness(
    *,
    now_jst: datetime,
    races: int,
    entries: int,
    predictions: int,
    start_predictions: int,
    nightly_success: bool,
) -> TomorrowReadiness:
    if races > 0 and entries >= races * 6 and predictions >= races and start_predictions >= races:
        return TomorrowReadiness("ready", "tomorrow sources and predictions are complete")
    if now_jst.hour < 23 or (now_jst.hour == 23 and now_jst.minute < 30):
        return TomorrowReadiness("pending", "nightly preload window has not started yet")
    if nightly_success:
        return TomorrowReadiness("warning", "nightly reported success but tomorrow sources are still incomplete")
    return TomorrowReadiness("blocked", "nightly preload window passed and tomorrow sources are incomplete")


def _task_success_exists(conn, task_name: str, run_date: str) -> bool:
    row = conn.execute(
        """
        SELECT success_at
          FROM task_runs
         WHERE task_name = ?
           AND run_date = ?
        """,
        (task_name, run_date),
    ).fetchone()
    return bool(row and row[0])


def _counts_for_date(conn, target_date: str) -> dict[str, int]:
    row = conn.execute(
        """
        SELECT
          (SELECT COUNT(*) FROM races WHERE race_date = ?) AS races,
          (SELECT COUNT(*) FROM race_entries e JOIN races r ON r.race_id = e.race_id WHERE r.race_date = ?) AS entries,
          (SELECT COUNT(DISTINCT p.race_id) FROM predictions p JOIN races r ON r.race_id = p.race_id WHERE r.race_date = ?) AS predictions,
          (SELECT COUNT(*) FROM roi_race_history h JOIN races r ON r.race_id = h.race_id WHERE r.race_date = ?) AS roi_history_rows
        """,
        (target_date, target_date, target_date, target_date),
    ).fetchone()
    try:
        start_prediction_row = conn.execute(
            """
            SELECT COUNT(DISTINCT p.race_id)
              FROM race_start_predictions p
              JOIN races r ON r.race_id = p.race_id
             WHERE r.race_date = ?
            """,
            (target_date,),
        ).fetchone()
        start_predictions = int(start_prediction_row[0] or 0) if start_prediction_row else 0
    except Exception:
        start_predictions = 0
    return {
        "races": int(row[0] or 0),
        "entries": int(row[1] or 0),
        "predictions": int(row[2] or 0),
        "start_predictions": start_predictions,
        "roi_history_rows": int(row[3] or 0),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="target date (default: tomorrow JST)")
    args = parser.parse_args()

    now_jst = _now_jst()
    target_date = args.date or (now_jst.date() + timedelta(days=1)).isoformat()
    today = now_jst.date().isoformat()

    with db_connect() as conn:
        counts = _counts_for_date(conn, target_date)
        nightly_success = _task_success_exists(conn, "render_nightly", today)
        morning_success = _task_success_exists(conn, "render_morning", target_date)

    readiness = evaluate_tomorrow_readiness(
        now_jst=now_jst,
        races=counts["races"],
        entries=counts["entries"],
        predictions=counts["predictions"],
        start_predictions=counts["start_predictions"],
        nightly_success=nightly_success,
    )

    print(f"now_jst={now_jst.isoformat(timespec='seconds')}")
    print(f"target_date={target_date}")
    print(
        "counts="
        f"races:{counts['races']} "
        f"entries:{counts['entries']} "
        f"predictions:{counts['predictions']} "
        f"start_predictions:{counts['start_predictions']} "
        f"roi_history_rows:{counts['roi_history_rows']}"
    )
    print(f"task_status=render_nightly_today:{nightly_success} render_morning_target:{morning_success}")
    print(f"readiness={readiness.state} reason={readiness.reason}")


if __name__ == "__main__":
    main()
