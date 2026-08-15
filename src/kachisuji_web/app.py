"""Local-only Flask application for the Step 2 ROI search engine."""

from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import re
from typing import Any, Mapping

from flask import Flask, jsonify, render_template, request

from src.search.roi_search import search_roi


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "kachisuji_search.db"
_RACER_NUMBER = re.compile(r"^\s*(\d+)(?:\s+.*)?$")


def _normalize_request(payload: Any) -> tuple[dict[str, Any], bool]:
    if not isinstance(payload, Mapping):
        raise ValueError("検索条件はJSONオブジェクトで指定してください")

    conditions = deepcopy(dict(payload))
    fast = conditions.pop("fast", False)
    if not isinstance(fast, bool):
        raise ValueError("fastは真偽値で指定してください")

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


def create_app(db_path: str | Path | None = None) -> Flask:
    """Create the standalone local application without starting a server."""

    app = Flask(__name__)
    configured_path = db_path or os.environ.get("KACHISUJI_DB") or DEFAULT_DB_PATH
    app.config["KACHISUJI_DB"] = str(configured_path)
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
            return jsonify(error=str(exc)), 400
        except Exception as exc:  # pragma: no cover - exercised with a forced failure
            app.logger.exception("kachisuji search failed")
            return jsonify(error=str(exc)), 500

    @app.get("/healthz")
    def healthz():
        return jsonify(status="ok")

    return app


__all__ = ["create_app"]
