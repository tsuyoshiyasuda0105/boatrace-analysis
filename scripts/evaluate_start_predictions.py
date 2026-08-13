"""Evaluate predictions once six official result rows are available."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from src.db.connection import connect
from src.start_prediction import StartPredictionService


JST = ZoneInfo("Asia/Tokyo")


def _today_jst_iso() -> str:
    return datetime.now(JST).date().isoformat()


def _load_due_predictions(conn, date_from: str, date_to: str) -> list[tuple[str, str]]:
    rows = conn.execute(
        """SELECT p.race_id, p.prediction_stage
             FROM race_start_predictions p
             JOIN races r ON r.race_id=p.race_id
             LEFT JOIN race_start_prediction_evaluations e ON e.prediction_id=p.prediction_id
            WHERE r.race_date BETWEEN ? AND ?
              AND e.prediction_id IS NULL
              AND p.prediction_id = (
                    SELECT MAX(p2.prediction_id)
                      FROM race_start_predictions p2
                     WHERE p2.race_id=p.race_id
                       AND p2.prediction_stage=p.prediction_stage
                  )
              AND EXISTS (
                    SELECT 1
                      FROM race_results rr
                     WHERE rr.race_id=p.race_id
                     GROUP BY rr.race_id
                    HAVING COUNT(DISTINCT rr.boat_number)=6
                  )
            ORDER BY p.race_id, p.prediction_stage""",
        (date_from, date_to),
    ).fetchall()
    return [(str(row[0]), str(row[1])) for row in rows]


def due_predictions(date_from: str, date_to: str) -> list[tuple[str, str]]:
    with connect() as conn:
        return _load_due_predictions(conn, date_from, date_to)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=_today_jst_iso())
    parser.add_argument("--lookback-days", type=int, default=2)
    args = parser.parse_args()
    date_from = (date.fromisoformat(args.date) - timedelta(days=args.lookback_days)).isoformat()
    service = StartPredictionService()
    done = failed = 0
    try:
        predictions = due_predictions(date_from, args.date)
    except Exception as exc:
        if "does not exist" in str(exc) or "no such table" in str(exc):
            print("[start-evaluation] prediction schema not initialized yet", flush=True)
            return 0
        raise
    for race_id, stage in predictions:
        try:
            service.evaluate(race_id, stage)
            done += 1
        except Exception as exc:
            failed += 1
            print(
                f"[start-evaluation] failed race_id={race_id} stage={stage} "
                f"error={type(exc).__name__}: {exc}",
                flush=True,
            )
    print(f"[start-evaluation] from={date_from} to={args.date} due={done+failed} done={done} failed={failed}", flush=True)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
