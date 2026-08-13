"""Materialize daily start-prediction metrics for fast administration views."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.db.connection import connect
from src.start_prediction import StartPredictionService
from src.start_prediction.models import MODEL_VERSIONS
from src.start_prediction.repository import StartPredictionRepository


JST = ZoneInfo("Asia/Tokyo")


def _today_jst() -> date:
    return datetime.now(JST).date()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=(_today_jst() - timedelta(days=1)).isoformat())
    args = parser.parse_args()
    metrics = StartPredictionService().metrics({"from": args.date, "to": args.date})
    with connect() as conn:
        repo = StartPredictionRepository(conn); repo.ensure_schema()
        conn.execute(
            """DELETE FROM start_prediction_metrics_daily
                WHERE metric_date=? AND model_bundle_version=?
                  AND stadium_number=0 AND race_grade_number=0""",
            (args.date, MODEL_VERSIONS["bundle"]),
        )
        conn.execute(
            """INSERT INTO start_prediction_metrics_daily
               (metric_date,model_bundle_version,stadium_number,race_grade_number,
                prediction_count,evaluated_count,st_mae,st_rmse,start_top_accuracy,
                winner_accuracy,kimarite_accuracy,trifecta_top10_accuracy,roi,payload,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
            (args.date, MODEL_VERSIONS["bundle"], 0, 0, metrics["prediction_count"],
             metrics["evaluated_count"], metrics["st_mae"], metrics["st_rmse"],
             metrics["start_top_accuracy"], metrics["winner_accuracy"], metrics["kimarite_accuracy"],
             metrics["trifecta_top10_accuracy"], metrics["roi_top1"],
             json.dumps(metrics, ensure_ascii=False, default=str)),
        )
        conn.commit()
    print(f"[start-metrics] date={args.date} predictions={metrics['prediction_count']} evaluated={metrics['evaluated_count']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
