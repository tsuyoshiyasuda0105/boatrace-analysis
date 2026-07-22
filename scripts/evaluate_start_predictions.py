"""Evaluate predictions once six official result rows are available."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from datetime import date, timedelta

from src.db.connection import connect
from src.start_prediction import StartPredictionService


def due_race_ids(date_from: str, date_to: str) -> list[str]:
    with connect() as conn:
        return [str(row[0]) for row in conn.execute(
            """SELECT p.race_id
                 FROM race_start_predictions p
                 JOIN races r ON r.race_id=p.race_id
                 JOIN race_results rr ON rr.race_id=p.race_id
                 LEFT JOIN race_start_prediction_evaluations e ON e.prediction_id=p.prediction_id
                WHERE r.race_date BETWEEN ? AND ? AND e.prediction_id IS NULL
                GROUP BY p.race_id HAVING COUNT(rr.boat_number)=6
                ORDER BY p.race_id""", (date_from, date_to)
        ).fetchall()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--lookback-days", type=int, default=2)
    args = parser.parse_args()
    date_from = (date.fromisoformat(args.date) - timedelta(days=args.lookback_days)).isoformat()
    service = StartPredictionService()
    done = failed = 0
    try:
        race_ids = due_race_ids(date_from, args.date)
    except Exception as exc:
        if "does not exist" in str(exc) or "no such table" in str(exc):
            print("[start-evaluation] prediction schema not initialized yet", flush=True)
            return 0
        raise
    for race_id in race_ids:
        try:
            service.evaluate(race_id, "post_exhibition")
            done += 1
        except Exception as exc:
            failed += 1
            print(f"[start-evaluation] failed race_id={race_id} error={type(exc).__name__}: {exc}", flush=True)
    print(f"[start-evaluation] from={date_from} to={args.date} due={done+failed} done={done} failed={failed}", flush=True)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
