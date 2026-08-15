"""Local-only Flask application for the Step 2 ROI search engine."""

from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import re
from typing import Any, Mapping

from flask import Flask, jsonify, render_template, request

from src.search.roi_search import search_roi
from src.search.strategies import (
    deactivate_strategy,
    get_strategy,
    list_strategies,
    match_all_strategies,
    match_races,
    save_strategy,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "kachisuji_search.db"
DEFAULT_STRATEGY_DB_PATH = PROJECT_ROOT / "data" / "kachisuji_strategies.db"
_RACER_NUMBER = re.compile(r"^\s*(\d+)(?:\s+.*)?$")


def _user_validation_message(error: ValueError) -> str:
    """Hide validator field paths and English implementation details from the UI."""

    message = str(error)
    user_message_prefixes = (
        "検索条件は",
        "リクエストは",
        "高速集計の指定は",
        "選手名には",
        "買い目は",
        "艇間比較は",
        "オッズ条件は",
        "バックテスト結果は",
    )
    if message.startswith(user_message_prefixes):
        return message
    if message == "name must not be empty":
        return "手法名を入力してください"
    if message == "date must be an ISO date":
        return "日付はYYYY-MM-DD形式で指定してください"
    return "入力内容に誤りがあります。各項目の値を確認してください"


def _normalize_request(payload: Any) -> tuple[dict[str, Any], bool]:
    if not isinstance(payload, Mapping):
        raise ValueError("検索条件はJSONオブジェクトで指定してください")

    conditions = deepcopy(dict(payload))
    fast = conditions.pop("fast", False)
    if not isinstance(fast, bool):
        raise ValueError("高速集計の指定はtrueまたはfalseにしてください")

    boats = conditions.get("boats")
    if boats is not None:
        if not isinstance(boats, Mapping):
            raise ValueError("boats must be an object")
        for boat_key, raw_boat in boats.items():
            if not isinstance(raw_boat, Mapping):
                continue
            racer = raw_boat.get("racer_id")
            if not isinstance(racer, str):
                continue
            match = _RACER_NUMBER.fullmatch(racer)
            if match is None:
                raise ValueError("選手名には未対応です。選手番号で指定してください")
            conditions["boats"][boat_key]["racer_id"] = int(match.group(1))

    return conditions, fast


def create_app(
    db_path: str | Path | None = None,
    strategy_db_path: str | Path | None = None,
) -> Flask:
    """Create the standalone local application without starting a server."""

    app = Flask(__name__)
    configured_path = db_path or os.environ.get("KACHISUJI_DB") or DEFAULT_DB_PATH
    configured_strategy_path = (
        strategy_db_path or os.environ.get("KACHISUJI_STRATEGY_DB") or DEFAULT_STRATEGY_DB_PATH
    )
    app.config["KACHISUJI_DB"] = str(configured_path)
    app.config["KACHISUJI_STRATEGY_DB"] = str(configured_strategy_path)
    app.json.ensure_ascii = False

    @app.get("/")
    def index() -> str:
        return render_template("search.html")

    @app.post("/api/search")
    def api_search():
        try:
            conditions, fast = _normalize_request(request.get_json(silent=True))
            return jsonify(search_roi(app.config["KACHISUJI_DB"], conditions, fast=fast))
        except ValueError as exc:
            return jsonify(error=_user_validation_message(exc)), 400
        except Exception as exc:  # pragma: no cover - exercised with a forced failure
            app.logger.exception("kachisuji search failed")
            return jsonify(error=str(exc)), 500

    @app.post("/api/strategies")
    def api_save_strategy():
        try:
            payload = request.get_json(silent=True)
            if not isinstance(payload, Mapping):
                raise ValueError("リクエストはJSONオブジェクトで指定してください")
            conditions, _fast = _normalize_request(payload.get("conditions"))
            strategy_id = save_strategy(
                payload.get("name"),
                conditions,
                payload.get("backtest"),
                db_path=app.config["KACHISUJI_STRATEGY_DB"],
            )
            return jsonify(id=strategy_id)
        except ValueError as exc:
            return jsonify(error=_user_validation_message(exc)), 400
        except Exception as exc:  # pragma: no cover - exercised with a forced failure
            app.logger.exception("kachisuji strategy save failed")
            return jsonify(error=str(exc)), 500

    @app.get("/api/strategies")
    def api_list_strategies():
        try:
            return jsonify(list_strategies(db_path=app.config["KACHISUJI_STRATEGY_DB"]))
        except Exception as exc:  # pragma: no cover - exercised with a forced failure
            app.logger.exception("kachisuji strategy list failed")
            return jsonify(error=str(exc)), 500

    @app.delete("/api/strategies/<int:strategy_id>")
    def api_deactivate_strategy(strategy_id: int):
        try:
            changed = deactivate_strategy(
                strategy_id,
                db_path=app.config["KACHISUJI_STRATEGY_DB"],
            )
            if not changed:
                return jsonify(error="strategy not found"), 404
            return jsonify(deactivated=True)
        except Exception as exc:  # pragma: no cover - exercised with a forced failure
            app.logger.exception("kachisuji strategy deactivation failed")
            return jsonify(error=str(exc)), 500

    @app.get("/api/strategies/<int:strategy_id>/matches")
    def api_strategy_matches(strategy_id: int):
        try:
            if get_strategy(strategy_id, db_path=app.config["KACHISUJI_STRATEGY_DB"]) is None:
                return jsonify(error="strategy not found"), 404
            return jsonify(
                match_races(
                    strategy_id,
                    request.args.get("date"),
                    app.config["KACHISUJI_DB"],
                    app.config["KACHISUJI_STRATEGY_DB"],
                )
            )
        except ValueError as exc:
            return jsonify(error=_user_validation_message(exc)), 400
        except Exception as exc:  # pragma: no cover - exercised with a forced failure
            app.logger.exception("kachisuji strategy matching failed")
            return jsonify(error=str(exc)), 500

    @app.get("/api/matches")
    def api_all_matches():
        try:
            return jsonify(
                match_all_strategies(
                    request.args.get("date"),
                    app.config["KACHISUJI_DB"],
                    app.config["KACHISUJI_STRATEGY_DB"],
                )
            )
        except ValueError as exc:
            return jsonify(error=_user_validation_message(exc)), 400
        except Exception as exc:  # pragma: no cover - exercised with a forced failure
            app.logger.exception("kachisuji all-strategy matching failed")
            return jsonify(error=str(exc)), 500

    @app.get("/healthz")
    def healthz():
        return jsonify(status="ok")

    return app


__all__ = ["create_app"]
