"""HTTP endpoints and admin view for start/development prediction v1."""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, render_template, request

from src.start_prediction import StartPredictionService
from src.web.auth import login_required, member_only_api

bp = Blueprint("start_prediction", __name__)
logger = logging.getLogger(__name__)


def _service() -> StartPredictionService:
    return StartPredictionService()


@bp.post("/api/predictions/races/<race_id>")
@member_only_api
def create_race_prediction(race_id: str):
    payload = request.get_json(silent=True) or {}
    stage = str(payload.get("stage") or request.args.get("stage") or "post_exhibition")
    try:
        return jsonify(_service().generate(race_id, stage))
    except (LookupError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 422
    except Exception:
        logger.exception("start prediction generation failed race_id=%s", race_id)
        return jsonify({"error": "展開予測を生成できませんでした。既存画面の利用には影響しません。"}), 500


@bp.get("/api/predictions/races/<race_id>")
@member_only_api
def get_race_prediction(race_id: str):
    result = _service().get(race_id, request.args.get("stage"))
    if not result:
        return jsonify({"error": "prediction not found"}), 404
    return jsonify(result)


@bp.post("/api/predictions/races/<race_id>/evaluate")
@member_only_api
def evaluate_race_prediction(race_id: str):
    try:
        return jsonify(_service().evaluate(race_id, request.args.get("stage")))
    except (LookupError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 422
    except Exception:
        logger.exception("start prediction evaluation failed race_id=%s", race_id)
        return jsonify({"error": "予測評価を保存できませんでした。"}), 500


@bp.get("/api/predictions/metrics")
@member_only_api
def prediction_metrics_api():
    return jsonify(_service().metrics(dict(request.args)))


@bp.get("/member/start-predictions")
@login_required
def prediction_metrics_page():
    filters = dict(request.args)
    return render_template("start_prediction_metrics.html", metrics=_service().metrics(filters), filters=filters)
