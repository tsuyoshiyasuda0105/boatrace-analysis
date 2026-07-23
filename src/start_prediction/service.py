"""Application service for generation, retrieval, evaluation and metrics."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from src.db.connection import connect

from .evaluation import evaluate_prediction
from .features import PointInTimeFeatureBuilder
from .models import MODEL_VERSIONS, RuleEnsembleV1
from .repository import StartPredictionRepository, _loads

MODEL_FEATURES = [
    "course_avg_st", "derived_st_12", "derived_st_180d", "entry_avg_st",
    "exhibition_st", "exhibition_to_actual_bias", "st_std", "flying_count",
    "accident_rate", "wind_speed", "wave_height", "course_win_rate",
    "motor_asof_top2", "published_motor_top2", "exhibition_time",
    "national_top1", "local_top1", "stadium_number", "race_grade_number",
]


class StartPredictionService:
    def __init__(self, connection_factory=connect):
        self.connection_factory = connection_factory

    def generate(self, race_id: str, stage: str = "post_exhibition") -> dict[str, Any]:
        if stage not in {"pre_exhibition", "post_exhibition"}:
            raise ValueError("stage must be pre_exhibition or post_exhibition")
        with self.connection_factory() as conn:
            repo = StartPredictionRepository(conn)
            repo.ensure_schema()
            repo.register_models(MODEL_VERSIONS, MODEL_FEATURES)
            existing = repo.get(race_id, stage, MODEL_VERSIONS["bundle"])
            if existing:
                return existing
            snapshot = PointInTimeFeatureBuilder(conn).build(race_id, stage)
            output = RuleEnsembleV1().predict(snapshot.as_dict())
            return repo.save(race_id, stage, snapshot.feature_cutoff_at, snapshot.as_dict(), output)

    def get(self, race_id: str, stage: str | None = None) -> dict[str, Any] | None:
        with self.connection_factory() as conn:
            repo = StartPredictionRepository(conn)
            repo.ensure_schema()
            return repo.get_latest(race_id, stage)

    def evaluate(self, race_id: str, stage: str | None = None) -> dict[str, Any]:
        with self.connection_factory() as conn:
            repo = StartPredictionRepository(conn)
            repo.ensure_schema()
            prediction = repo.get_latest(race_id, stage)
            if not prediction:
                raise LookupError(f"prediction not found: {race_id}")
            if prediction.get("evaluation"):
                return prediction["evaluation"]
            result = evaluate_prediction(conn, prediction)
            return repo.save_evaluation(int(prediction["prediction_id"]), result)

    def timeline(self, race_id: str) -> dict[str, Any]:
        with self.connection_factory() as conn:
            repo = StartPredictionRepository(conn)
            repo.ensure_schema()
            pre = repo.get_latest(race_id, "pre_exhibition")
            post = repo.get_latest(race_id, "post_exhibition")
            actual = repo.actual_result(race_id)
        return {
            "race_id": race_id,
            "pre_exhibition": pre,
            "post_exhibition": post,
            "actual": actual,
        }

    def metrics(self, filters: dict[str, Any]) -> dict[str, Any]:
        default_from = (date.today() - timedelta(days=30)).isoformat()
        with self.connection_factory() as conn:
            repo = StartPredictionRepository(conn)
            repo.ensure_schema()
            rows = repo.metrics_rows(
                date_from=filters.get("from") or default_from,
                date_to=filters.get("to") or date.today().isoformat(),
                stadium_number=int(filters["stadium_number"]) if filters.get("stadium_number") else None,
                grade=int(filters["grade"]) if filters.get("grade") else None,
                race_number=int(filters["race_number"]) if filters.get("race_number") else None,
                model_version=filters.get("model_version"),
            )
        rows = self._post_filter(rows, filters)
        evaluated = [r for r in rows if r.get("st_mae") is not None]
        def avg(key: str):
            vals = [float(r[key]) for r in evaluated if r.get(key) is not None]
            return sum(vals) / len(vals) if vals else None
        top1_bets, top1_return = 0, 0
        top10_bets, top10_return = 0, 0
        for row in evaluated:
            actual = _loads(row.get("actual_snapshot"), {})
            actual_combo = str(actual.get("actual_combo") or "")
            payout = int(actual.get("actual_trifecta_payout") or 0)
            top1_bets += 100
            top10_bets += 1000
            if actual_combo and actual_combo == str(row.get("top_trifecta") or ""):
                top1_return += payout
            if row.get("trifecta_top10_hit"):
                top10_return += payout
        return {
            "filters": filters,
            "prediction_count": len(rows),
            "evaluated_count": len(evaluated),
            "st_mae": avg("st_mae"),
            "st_rmse": avg("st_rmse"),
            "start_top_accuracy": avg("start_top_hit"),
            "winner_accuracy": avg("winner_hit"),
            "kimarite_accuracy": avg("kimarite_hit"),
            "trifecta_top10_accuracy": avg("trifecta_top10_hit"),
            "roi_top1": (top1_return / top1_bets * 100) if top1_bets else None,
            "roi_top10_box": (top10_return / top10_bets * 100) if top10_bets else None,
            "note": "ROIは確定払戻による評価値です。Top1は1点100円、Top10は10点各100円で計算し、購入推奨ではありません。",
            "rows": rows,
        }

    @staticmethod
    def _post_filter(rows: list[dict[str, Any]], filters: dict[str, Any]) -> list[dict[str, Any]]:
        def num(name: str) -> float | None:
            try:
                return float(filters[name]) if filters.get(name) not in (None, "") else None
            except (TypeError, ValueError):
                return None

        wind_min, wind_max = num("wind_min"), num("wind_max")
        confidence_min, confidence_max = num("confidence_min"), num("confidence_max")
        odds_min, odds_max = num("odds_min"), num("odds_max")
        tide_phase = str(filters.get("tide_phase") or "").strip()
        filtered = []
        for row in rows:
            snapshot = _loads(row.get("input_snapshot"), {})
            boats = snapshot.get("boats") or []
            wind = boats[0].get("wind_speed") if boats else None
            confidence = row.get("confidence")
            tide = snapshot.get("tide") or {}
            trifectas = _loads(row.get("input_snapshot"), {}).get("market", {}).get("trifecta_odds", {})
            top_combo = row.get("top_trifecta")
            top_odds = trifectas.get(top_combo) if top_combo else None
            if wind_min is not None and (wind is None or float(wind) < wind_min): continue
            if wind_max is not None and (wind is None or float(wind) > wind_max): continue
            if confidence_min is not None and (confidence is None or float(confidence) < confidence_min): continue
            if confidence_max is not None and (confidence is None or float(confidence) > confidence_max): continue
            if tide_phase and str(tide.get("tide_phase") or "") != tide_phase: continue
            if odds_min is not None and (top_odds is None or float(top_odds) < odds_min): continue
            if odds_max is not None and (top_odds is None or float(top_odds) > odds_max): continue
            row["input_snapshot"] = snapshot
            row["error_categories"] = _loads(row.get("error_categories"), [])
            filtered.append(row)
        return filtered
