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
from src.web.auth import can_use_backtest, login_required, member_only_api


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
    return jsonify(error="kachisuji_unavailable", message="バックテストは準備中です"), 503


def _paid_member_api_forbidden():
    if can_use_backtest():
        return None
    return jsonify(error="forbidden", message="有料会員のみ利用できます"), 403


@bp.get("")
@bp.get("/")
@login_required
def index():
    if not can_use_backtest():
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


# ------------------------------------------------------------ delta apply --
# 夜間 cron が HTTPS で叩く内部トリガー。web だけが /data の slim DB を持つため
# 適用は web プロセス内で行う。認証は共有 DATABASE_URL 由来トークン
# (src.kachisuji.delta_transport.internal_token) — 新しい秘密は増やさない。

@bp.get("/internal/disk-report")
def internal_disk_report():
    """slim DB を置くディスクの容量と中身を返す (トークン保護・読み取りのみ)。"""
    from src.kachisuji.delta_transport import disk_report, internal_token

    provided = request.headers.get("X-Internal-Token", "")
    try:
        expected = internal_token()
    except RuntimeError:
        return jsonify(error="internal token unavailable"), 503
    if not provided or provided != expected:
        return jsonify(error="forbidden"), 403
    db_path = _search_db_path()
    payload = disk_report(db_path)
    # 取込の鮮度も返す。ディスクは web にしか繋がっておらず cron からは
    # このファイルを開けないため (2026-08-23: 朝の点検が毎回
    # "unable to open database file" で失敗していた)、cron はここに聞く。
    payload["latest_race_date"] = _slim_latest_race_date(db_path)
    return jsonify(payload)


def _slim_latest_race_date(db_path: Path) -> str | None:
    """slim DB が持つ最新レース日。読めなければ None。"""
    if not db_path.is_file():
        return None
    try:
        connection = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            row = connection.execute(
                "SELECT MAX(race_date) FROM asof_race_features"
            ).fetchone()
        finally:
            connection.close()
        return str(row[0]) if row and row[0] else None
    except sqlite3.Error:
        return None


@bp.get("/internal/db-pool-report")
def internal_db_pool_report():
    """接続プールの状態を読み取り専用で返す (障害調査用)。

    2026-08-24 実障害の教訓。以前ここにあった page-cache-probe は
    os.environ["BOATRACE_TASK_TRIGGER"] を一時的に書き換えて pooled と
    cron_direct を比べる造りだった。環境変数は worker 内の全スレッドに効くので、
    調査中に届いた無関係なリクエストまで接続の取り方 (と statement_timeout) が
    変わってしまう。調査のために本番を壊しうる道具だったので撤去した。

    代わりに、何も書き換えずに今の姿を返す。gunicorn は複数 worker なので、
    どの worker が答えたかを pid で見分けられる (片方だけ不調を掴むため)。
    """
    from src.db.connection import pg_pool_report
    from src.kachisuji.delta_transport import internal_token
    from src.web import membership

    provided = request.headers.get("X-Internal-Token", "")
    try:
        expected = internal_token()
    except RuntimeError:
        return jsonify(error="internal token unavailable"), 503
    if not provided or provided != expected:
        return jsonify(error="forbidden"), 403
    report = pg_pool_report()
    # 会員だけが払う認証接続のコスト (2026-08-26「会員トップが遅い」の切り分け)
    report["auth_connect"] = {
        "last_sec": membership.LAST_AUTH_CONNECT_SEC[0],
        "max_sec": membership.AUTH_CONNECT_MAX_SEC[0],
    }
    return jsonify(report)


@bp.get("/internal/page-cache-lookup")
def internal_page_cache_lookup():
    """指定レースの詳細ページを、本番プロセスがどう見ているかを層ごとに返す。

    2026-08-24: 本番だけレース詳細が「準備しています」に落ちる一方、同じコードを
    同じ DB に向けて手元で走らせると 0.1 秒で正しく表示された。差はプロセスの
    状態にあるが、外からは仮ページしか見えず切り分けられなかった。どの層で
    None になっているのか (メモリ / prewarm コンテキスト / DB) を読み取り専用で
    覗けるようにする。書き込みも設定変更もしない。
    """
    import time as _time

    from src.db.connection import pg_pool_report
    from src.kachisuji.delta_transport import internal_token
    from src.web import app as web_app

    provided = request.headers.get("X-Internal-Token", "")
    try:
        expected = internal_token()
    except RuntimeError:
        return jsonify(error="internal token unavailable"), 503
    if not provided or provided != expected:
        return jsonify(error="forbidden"), 403

    race_id = (request.args.get("race_id") or "").strip()
    if not race_id:
        return jsonify(error="race_id required"), 400

    key = web_app._race_detail_page_cache_key(race_id)
    now = _time.time()
    out = {
        "pid": os.getpid(),
        "race_id": race_id,
        "cache_key": key,
        "cache_version": web_app.RACE_DETAIL_PAGE_CACHE_VERSION,
        "fresh_ttl_sec": web_app.RACE_DETAIL_PAGE_FRESH_SEC,
        "today_jst": web_app._today_jst_iso(),
        "pool": pg_pool_report().get("stats", {}),
    }

    mem = web_app._PAGE_HTML_MEM_CACHE.get(key)
    out["mem_cache"] = (
        {"present": True, "age_sec": round(now - float(mem[0] or 0), 1), "len": len(mem[1] or "")}
        if mem
        else {"present": False}
    )
    ctx = web_app._RACE_DETAIL_PREWARM_CONTEXT.get()
    out["prewarm_context_active"] = ctx is not None
    if ctx is not None:
        rows = ctx.get("page_cache_rows")
        out["prewarm_page_cache_rows"] = None if rows is None else len(rows)
        out["prewarm_has_this_key"] = None if rows is None else (key in rows)

    for label, fn in (
        ("fresh", lambda: web_app._read_page_html_cache(key, web_app.RACE_DETAIL_PAGE_FRESH_SEC)),
        ("stale", lambda: web_app._read_page_html_cache_stale(key)),
    ):
        started = _time.perf_counter()
        try:
            html = fn()
            out[f"read_{label}"] = {
                "len": None if html is None else len(html),
                "ms": round((_time.perf_counter() - started) * 1000, 1),
            }
        except Exception as exc:  # noqa: BLE001 - 調査目的なので必ず返す
            out[f"read_{label}"] = {
                "error": f"{type(exc).__name__}: {exc}"[:300],
                "ms": round((_time.perf_counter() - started) * 1000, 1),
            }
    return jsonify(out)


@bp.post("/internal/apply-deltas")
def internal_apply_deltas():
    from src.kachisuji.delta_transport import (
        InsufficientDiskSpaceError,
        apply_pending_to_slim,
        internal_token,
    )

    provided = request.headers.get("X-Internal-Token", "")
    try:
        expected = internal_token()
    except RuntimeError:
        return jsonify(error="internal token unavailable"), 503
    if not provided or provided != expected:
        return jsonify(error="forbidden"), 403
    db_path = _search_db_path()
    if not db_path.is_file():
        return jsonify(error="slim db missing", path=str(db_path)), 503
    try:
        summary = apply_pending_to_slim(db_path)
    except InsufficientDiskSpaceError as exc:
        # 容量不足の時こそ「何がディスクを食っているか」が要る。
        from src.kachisuji.delta_transport import disk_report

        current_app.logger.warning("kachisuji delta apply skipped: %s", exc)
        return jsonify(
            error=str(exc),
            free_bytes=exc.free_bytes,
            required_bytes=exc.required_bytes,
            disk=disk_report(db_path),
        ), 507
    except Exception as exc:  # noqa: BLE001 - report, never crash the worker
        current_app.logger.exception("kachisuji delta apply failed")
        return jsonify(error=f"{type(exc).__name__}: {exc}"[:500]), 500
    current_app.logger.info("kachisuji delta apply: %s", summary)
    return jsonify(summary)


@bp.record_once
def _schedule_startup_delta_apply(setup_state) -> None:
    """デプロイ/再起動のたびに未適用デルタを追いつかせる保険 (cron 不発対策)。"""
    if not os.environ.get("RENDER"):
        return

    import threading

    def _run() -> None:
        try:
            db_path = _search_db_path()
            if not db_path.is_file():
                return
            from src.kachisuji.delta_transport import apply_pending_to_slim

            summary = apply_pending_to_slim(db_path)
            if summary.get("applied_files"):
                setup_state.app.logger.info(
                    "kachisuji startup delta apply: %s", summary
                )
        except Exception:  # noqa: BLE001 - startup must never crash the app
            setup_state.app.logger.exception("kachisuji startup delta apply failed")

    timer = threading.Timer(30.0, _run)
    timer.daemon = True
    timer.start()


__all__ = ["bp"]
