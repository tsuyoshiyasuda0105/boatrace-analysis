"""Generate immutable post-exhibition predictions for eligible races."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from datetime import date

from src.db.connection import connect
from src.start_prediction import StartPredictionService
from src.start_prediction.models import MODEL_VERSIONS
from src.start_prediction.repository import StartPredictionRepository


def due_race_ids(target_date: str) -> list[str]:
    with connect() as conn:
        StartPredictionRepository(conn).ensure_schema()
        try:
            rows = conn.execute(
                """SELECT r.race_id
                     FROM races r
                     JOIN race_previews p ON p.race_id=r.race_id
                     LEFT JOIN race_start_predictions sp
                       ON sp.race_id = r.race_id
                      AND sp.prediction_stage = 'post_exhibition'
                      AND sp.model_bundle_version = ?
                    WHERE r.race_date=?
                      AND sp.prediction_id IS NULL
                    GROUP BY r.race_id
                   HAVING COUNT(CASE WHEN p.exhibition_time IS NOT NULL
                                      AND p.start_timing_exhibition IS NOT NULL THEN 1 END)=6
                    ORDER BY r.race_closed_at""",
                (MODEL_VERSIONS["bundle"], target_date),
            ).fetchall()
        except Exception:
            rows = conn.execute(
                """SELECT r.race_id
                     FROM races r
                     JOIN race_previews p ON p.race_id=r.race_id
                    WHERE r.race_date=?
                    GROUP BY r.race_id
                   HAVING COUNT(CASE WHEN p.exhibition_time IS NOT NULL
                                      AND p.start_timing_exhibition IS NOT NULL THEN 1 END)=6
                    ORDER BY r.race_closed_at""",
                (target_date,),
            ).fetchall()
        return [str(row[0]) for row in rows]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=date.today().isoformat())
    args = parser.parse_args()
    service = StartPredictionService()
    done = failed = 0
    for race_id in due_race_ids(args.date):
        try:
            service.generate(race_id, "post_exhibition")
            done += 1
        except Exception as exc:
            failed += 1
            print(f"[start-prediction] failed race_id={race_id} error={type(exc).__name__}: {exc}", flush=True)
    print(f"[start-prediction] date={args.date} due={done+failed} done={done} failed={failed}", flush=True)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
