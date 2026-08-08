"""HTTP endpoints and admin view for start/development prediction v1."""
from __future__ import annotations

import logging
import time

from flask import Blueprint, jsonify, render_template, request

from src.start_prediction import StartPredictionService
from src.web.auth import admin_only_api, admin_required

bp = Blueprint("start_prediction", __name__)
logger = logging.getLogger(__name__)
_TIMELINE_CACHE_TTL_SECONDS = 300
_TIMELINE_CACHE: dict[str, tuple[float, dict]] = {}


def _service() -> StartPredictionService:
    return StartPredictionService()


def _clear_timeline_cache(race_id: str) -> None:
    _TIMELINE_CACHE.pop(str(race_id), None)


def _get_cached_timeline(race_id: str) -> dict | None:
    cached = _TIMELINE_CACHE.get(str(race_id))
    if not cached:
        return None
    cached_at, payload = cached
    if (time.time() - cached_at) > _TIMELINE_CACHE_TTL_SECONDS:
        _clear_timeline_cache(race_id)
        return None
    return payload


def _set_cached_timeline(race_id: str, payload: dict) -> None:
    _TIMELINE_CACHE[str(race_id)] = (time.time(), payload)


@bp.post("/api/predictions/races/<race_id>")
@admin_only_api
def create_race_prediction(race_id: str):
    payload = request.get_json(silent=True) or {}
    stage = str(payload.get("stage") or request.args.get("stage") or "post_exhibition")
    try:
        generated = _service().generate(race_id, stage)
        _clear_timeline_cache(race_id)
        return jsonify(generated)
    except (LookupError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 422
    except Exception:
        logger.exception("start prediction generation failed race_id=%s", race_id)
        return jsonify({"error": "展開予測を生成できませんでした。既存画面の利用には影響しません。"}), 500


@bp.get("/api/predictions/races/<race_id>")
@admin_only_api
def get_race_prediction(race_id: str):
    result = _service().get(race_id, request.args.get("stage"))
    if not result:
        return jsonify({"error": "prediction not found"}), 404
    return jsonify(result)


@bp.get("/api/predictions/races/<race_id>/timeline")
@admin_only_api
def get_race_prediction_timeline(race_id: str):
    cached = _get_cached_timeline(race_id)
    if cached is not None:
        return jsonify(cached)
    payload = _service().timeline(race_id)
    _set_cached_timeline(race_id, payload)
    return jsonify(payload)


@bp.post("/api/predictions/races/<race_id>/evaluate")
@admin_only_api
def evaluate_race_prediction(race_id: str):
    try:
        evaluated = _service().evaluate(race_id, request.args.get("stage"))
        _clear_timeline_cache(race_id)
        return jsonify(evaluated)
    except (LookupError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 422
    except Exception:
        logger.exception("start prediction evaluation failed race_id=%s", race_id)
        return jsonify({"error": "予測評価を保存できませんでした。"}), 500


@bp.get("/api/predictions/metrics")
@admin_only_api
def prediction_metrics_api():
    return jsonify(_service().metrics(dict(request.args)))


@bp.get("/member/start-predictions")
@admin_required
def prediction_metrics_page():
    filters = dict(request.args)
    return render_template("start_prediction_metrics.html", metrics=_service().metrics(filters), filters=filters)
