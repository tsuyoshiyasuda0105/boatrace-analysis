"""Paid-member kachisuji search routes for the production Web app."""

from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import re
import sqlite3
from typing import Any, Mapping
import unicodedata

from flask import Blueprint, abort, current_app, jsonify, render_template, request

from src.search.roi_search import search_roi
from src.search.strategies import (
    deactivate_strategy,
    get_strategy,
    get_strategy_performance,
    list_strategies,
    list_strategy_performances,
    match_all_strategies,
    match_races,
    save_strategy,
)
from src.web.auth import is_paid_member, login_required, member_only_api


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SLIM_DB_PATH = PROJECT_ROOT / "data" / "kachisuji_slim.db"
LEGACY_SEARCH_DB_PATH = PROJECT_ROOT / "data" / "kachisuji_search.db"
DEFAULT_STRATEGY_DB_PATH = PROJECT_ROOT / "data" / "kachisuji_strategies.db"
_RACER_NUMBER = re.compile(r"^\s*(\d+)(?:\s+.*)?$")

bp = Blueprint("kachisuji", __name__, url_prefix="/kachisuji")


def _search_db_path() -> Path:
    configured = os.environ.get("KACHISUJI_DB")
    if configured:
        return Path(configured).expanduser()
    if DEFAULT_SLIM_DB_PATH.is_file():
        return DEFAULT_SLIM_DB_PATH
    if LEGACY_SEARCH_DB_PATH.is_file():
        return LEGACY_SEARCH_DB_PATH
    return DEFAULT_SLIM_DB_PATH


def _strategy_db_path() -> Path:
    configured = os.environ.get("KACHISUJI_STRATEGY_DB")
    return Path(configured).expanduser() if configured else DEFAULT_STRATEGY_DB_PATH


def _normalize_racer_text(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKC", value or "")
    katakana = "".join(
        chr(ord(character) + 0x60) if "ぁ" <= character <= "ゖ" else character
        for character in normalized
    )
    return "".join(katakana.split()).casefold()


def _is_single_cjk_name_query(value: str) -> bool:
    return len(value) == 1 and "\u3400" <= value <= "\u9fff"


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _search_racers(db_path: str | Path, query: str, limit: int) -> list[dict[str, Any]]:
    normalized = _normalize_racer_text(query)
    if len(normalized) < 2 and not _is_single_cjk_name_query(normalized):
        return []

    pattern = f"%{_escape_like(normalized)}%"
    prefix = f"{_escape_like(normalized)}%"
    resolved = Path(db_path).resolve()
    connection = sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.create_function(
        "normalize_racer_text", 1, _normalize_racer_text, deterministic=True
    )
    try:
        connection.execute("PRAGMA query_only = ON")
        rows = connection.execute(
            """
            SELECT racer_number, name, name_kana
            FROM racers
            WHERE normalize_racer_text(name) LIKE ? ESCAPE '\\'
               OR normalize_racer_text(name_kana) LIKE ? ESCAPE '\\'
            ORDER BY CASE
                       WHEN normalize_racer_text(name) LIKE ? ESCAPE '\\' THEN 0
                       WHEN normalize_racer_text(name_kana) LIKE ? ESCAPE '\\' THEN 1
                       ELSE 2
                     END,
                     racer_number
            LIMIT ?
            """,
            (pattern, pattern, prefix, prefix, limit),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def _user_validation_message(error: ValueError) -> str:
    message = str(error)
    user_message_prefixes = (
        "検索条件は",
        "リクエストは",
        "高速集計の指定は",
        "選手名には",
        "買い目は",
        "艇間比較は",
        "オッズ条件は",
        "オッズによる絞り込みは",
        "この手法はオッズ条件を含むため",
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


def _database_unavailable_response():
    return jsonify(error="kachisuji_unavailable", message="勝ち筋サーチは準備中です"), 503


def _paid_member_api_forbidden():
    if is_paid_member():
        return None
    return jsonify(error="forbidden", message="有料会員のみ利用できます"), 403


@bp.get("")
@bp.get("/")
@login_required
def index():
    if not is_paid_member():
        abort(403)
    return render_template(
        "kachisuji_search.html",
        kachisuji_ready=_search_db_path().is_file(),
    )


@bp.get("/api/racers")
@member_only_api
def api_racers():
    if forbidden := _paid_member_api_forbidden():
        return forbidden
    db_path = _search_db_path()
    if not db_path.is_file():
        return _database_unavailable_response()
    query = request.args.get("q", "")
    try:
        requested_limit = int(request.args.get("limit", 15))
    except (TypeError, ValueError):
        requested_limit = 15
    limit = min(50, max(1, requested_limit))
    try:
        return jsonify(_search_racers(db_path, query, limit))
    except sqlite3.Error as exc:
        current_app.logger.exception("kachisuji racer search failed")
        return jsonify(error=str(exc)), 500


@bp.post("/api/search")
@member_only_api
def api_search():
    if forbidden := _paid_member_api_forbidden():
        return forbidden
    db_path = _search_db_path()
    if not db_path.is_file():
        return _database_unavailable_response()
    try:
        conditions, fast = _normalize_request(request.get_json(silent=True))
        return jsonify(search_roi(db_path, conditions, fast=fast))
    except ValueError as exc:
        return jsonify(error=_user_validation_message(exc)), 400
    except Exception as exc:  # pragma: no cover - forced-failure safety net
        current_app.logger.exception("kachisuji search failed")
        return jsonify(error=str(exc)), 500


@bp.post("/api/strategies")
@member_only_api
def api_save_strategy():
    if forbidden := _paid_member_api_forbidden():
        return forbidden
    try:
        payload = request.get_json(silent=True)
        if not isinstance(payload, Mapping):
            raise ValueError("リクエストはJSONオブジェクトで指定してください")
        conditions, _fast = _normalize_request(payload.get("conditions"))
        strategy_id = save_strategy(
            payload.get("name"),
            conditions,
            payload.get("backtest"),
            db_path=_strategy_db_path(),
        )
        return jsonify(id=strategy_id)
    except ValueError as exc:
        return jsonify(error=_user_validation_message(exc)), 400
    except Exception as exc:  # pragma: no cover - forced-failure safety net
        current_app.logger.exception("kachisuji strategy save failed")
        return jsonify(error=str(exc)), 500


@bp.get("/api/strategies")
@member_only_api
def api_list_strategies():
    if forbidden := _paid_member_api_forbidden():
        return forbidden
    try:
        return jsonify(list_strategies(db_path=_strategy_db_path()))
    except Exception as exc:  # pragma: no cover - forced-failure safety net
        current_app.logger.exception("kachisuji strategy list failed")
        return jsonify(error=str(exc)), 500


@bp.get("/api/strategies/performance")
@member_only_api
def api_list_strategy_performance():
    if forbidden := _paid_member_api_forbidden():
        return forbidden
    db_path = _search_db_path()
    if not db_path.is_file():
        return _database_unavailable_response()
    try:
        return jsonify(list_strategy_performances(db_path, _strategy_db_path()))
    except ValueError as exc:
        return jsonify(error=_user_validation_message(exc)), 400
    except Exception as exc:  # pragma: no cover - forced-failure safety net
        current_app.logger.exception("kachisuji strategy performance list failed")
        return jsonify(error=str(exc)), 500


@bp.get("/api/strategies/<int:strategy_id>/performance")
@member_only_api
def api_strategy_performance(strategy_id: int):
    if forbidden := _paid_member_api_forbidden():
        return forbidden
    db_path = _search_db_path()
    if not db_path.is_file():
        return _database_unavailable_response()
    try:
        performance = get_strategy_performance(
            strategy_id, db_path, _strategy_db_path()
        )
        if performance is None:
            return jsonify(error="strategy not found"), 404
        return jsonify(performance)
    except ValueError as exc:
        return jsonify(error=_user_validation_message(exc)), 400
    except Exception as exc:  # pragma: no cover - forced-failure safety net
        current_app.logger.exception("kachisuji strategy performance failed")
        return jsonify(error=str(exc)), 500


@bp.delete("/api/strategies/<int:strategy_id>")
@member_only_api
def api_deactivate_strategy(strategy_id: int):
    if forbidden := _paid_member_api_forbidden():
        return forbidden
    try:
        changed = deactivate_strategy(strategy_id, db_path=_strategy_db_path())
        if not changed:
            return jsonify(error="strategy not found"), 404
        return jsonify(deactivated=True)
    except Exception as exc:  # pragma: no cover - forced-failure safety net
        current_app.logger.exception("kachisuji strategy deactivation failed")
        return jsonify(error=str(exc)), 500


@bp.get("/api/strategies/<int:strategy_id>/matches")
@member_only_api
def api_strategy_matches(strategy_id: int):
    if forbidden := _paid_member_api_forbidden():
        return forbidden
    db_path = _search_db_path()
    if not db_path.is_file():
        return _database_unavailable_response()
    try:
        strategy_db_path = _strategy_db_path()
        if get_strategy(strategy_id, db_path=strategy_db_path) is None:
            return jsonify(error="strategy not found"), 404
        return jsonify(
            match_races(
                strategy_id,
                request.args.get("date"),
                db_path,
                strategy_db_path,
            )
        )
    except ValueError as exc:
        return jsonify(error=_user_validation_message(exc)), 400
    except Exception as exc:  # pragma: no cover - forced-failure safety net
        current_app.logger.exception("kachisuji strategy matching failed")
        return jsonify(error=str(exc)), 500


@bp.get("/api/matches")
@member_only_api
def api_all_matches():
    if forbidden := _paid_member_api_forbidden():
        return forbidden
    db_path = _search_db_path()
    if not db_path.is_file():
        return _database_unavailable_response()
    try:
        return jsonify(
            match_all_strategies(
                request.args.get("date"), db_path, _strategy_db_path()
            )
        )
    except ValueError as exc:
        return jsonify(error=_user_validation_message(exc)), 400
    except Exception as exc:  # pragma: no cover - forced-failure safety net
        current_app.logger.exception("kachisuji all-strategy matching failed")
        return jsonify(error=str(exc)), 500


__all__ = ["bp"]
