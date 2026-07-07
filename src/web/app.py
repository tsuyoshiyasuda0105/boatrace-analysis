"""
Flask Web UI: 予測表示

ルート:
  GET /                    → 今日の日付にリダイレクト
  GET /races?date=YYYY-MM-DD → 指定日のレース一覧
  GET /race/<race_id>      → 1レースの予測詳細
  GET /api/race/<race_id>  → JSON
  GET /healthz             → ヘルスチェック
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import date, datetime
from typing import Optional, Any

from datetime import timedelta

# === タイムゾーン強制設定 (Asia/Tokyo) ===
# Render (Linux) は デフォルト UTC のため、date.today() / datetime.now() が
# JST より 9 時間遅れて前日扱いになる。全コードを書き換えるのは大規模なので、
# プロセス起動時に TZ 環境変数を設定 + tzset() で全 datetime API を JST 化。
# ローカル PC (Windows) では time.tzset が無いので no-op (システム JST のまま)。
os.environ.setdefault("TZ", "Asia/Tokyo")
if hasattr(time, "tzset"):
    time.tzset()

from flask import Flask, abort, jsonify, make_response, redirect, render_template, request, session, url_for

import config
from src.collectors import openapi
from src.db.connection import connect as db_connect
from src.web.auth import (
    is_member, login_required, member_only_api,
    register_auth_routes,
)
from src.web.predictor import Predictor

logger = logging.getLogger(__name__)


# ============================================================
# シンプルなインメモリ TTL キャッシュ (速度改善)
# ============================================================
_CACHE: dict[str, tuple[float, Any]] = {}
_CACHE_DEFAULT_TTL = 300  # 5分


def cached(ttl: int = _CACHE_DEFAULT_TTL, past_ttl: int = 3600):
    """Flask view 用 TTL キャッシュデコレータ。
    Args:
        ttl: 今日/未来のデータの TTL (秒)
        past_ttl: 過去日付のデータの TTL (秒、デフォルト 1 時間)
                  過去日は確定済みでデータが変わらないので長くキャッシュ可能。
    キーは Args/kwargs と request.args。
    """
    def decorator(fn):
        from functools import wraps

        @wraps(fn)
        def wrapper(*args, **kwargs):
            force_recompute = False
            filtered_qs = ""
            try:
                if request:
                    force_recompute = request.args.get("recompute") in ("1", "true", "yes", "on")
                    filtered_items = []
                    for key in sorted(request.args.keys()):
                        if key == "recompute":
                            continue
                        for value in request.args.getlist(key):
                            filtered_items.append((key, value))
                    filtered_qs = repr(filtered_items)
            except Exception:
                filtered_qs = ""
                force_recompute = False
            key = f"{fn.__name__}:{args}:{kwargs}:{filtered_qs}"
            now = time.time()
            # 過去日リクエストは長期キャッシュ
            effective_ttl = ttl
            try:
                today_iso = date.today().isoformat()
                if request:
                    req_date = request.args.get("date", "")
                    if req_date and req_date < today_iso:
                        effective_ttl = past_ttl
                    # /member/strategy 等の from/to レンジ → to が過去なら past_ttl
                    if effective_ttl == ttl:
                        to_date = request.args.get("to", "")
                        if to_date and to_date < today_iso:
                            effective_ttl = past_ttl
                # /race/<race_id> 等で race_id (例: 20260513-23-01) から
                # 日付を抽出して past_ttl を有効化
                if effective_ttl == ttl:
                    for a in args:
                        if isinstance(a, str) and len(a) >= 8 and a[:8].isdigit():
                            rid_date = f"{a[:4]}-{a[4:6]}-{a[6:8]}"
                            if rid_date < today_iso:
                                effective_ttl = past_ttl
                                break
            except Exception:
                pass
            if (not force_recompute) and key in _CACHE:
                ts, val = _CACHE[key]
                if now - ts < effective_ttl:
                    return val
            val = fn(*args, **kwargs)
            _CACHE[key] = (now, val)
            # 簡易 GC (最大 1000 エントリ)
            if len(_CACHE) > 1000:
                # 古い順に半分削除
                items = sorted(_CACHE.items(), key=lambda x: x[1][0])
                for k, _ in items[:500]:
                    _CACHE.pop(k, None)
            return val
        return wrapper
    return decorator


def invalidate_cache():
    """全キャッシュクリア (デバッグ用)"""
    _CACHE.clear()


def _ensure_page_html_cache_table() -> None:
    try:
        with db_connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS page_html_cache (
                    cache_key TEXT PRIMARY KEY,
                    html TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.commit()
    except Exception:
        logger.exception("failed to ensure page_html_cache table")


def _read_page_html_cache(cache_key: str, max_age_sec: int) -> Optional[str]:
    try:
        _ensure_page_html_cache_table()
        with db_connect() as conn:
            row = conn.execute(
                "SELECT html, updated_at FROM page_html_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
        if not row:
            return None
        html, updated_at = row[0], float(row[1] or 0)
        if time.time() - updated_at > max_age_sec:
            return None
        return html
    except Exception:
        logger.exception("failed to read page_html_cache: %s", cache_key)
        return None


def _write_page_html_cache(cache_key: str, html: str) -> None:
    try:
        _ensure_page_html_cache_table()
        now_ts = time.time()
        with db_connect() as conn:
            conn.execute(
                """
                INSERT INTO page_html_cache (cache_key, html, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    html = excluded.html,
                    updated_at = excluded.updated_at
                """,
                (cache_key, html, now_ts),
            )
            conn.commit()
    except Exception:
        logger.exception("failed to write page_html_cache: %s", cache_key)


def _read_json_cache(cache_key: str, max_age_sec: int) -> Optional[Any]:
    raw = _read_page_html_cache(cache_key, max_age_sec)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        logger.exception("failed to decode json cache: %s", cache_key)
        return None


def _write_json_cache(cache_key: str, payload: Any) -> None:
    try:
        _write_page_html_cache(
            cache_key,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        )
    except Exception:
        logger.exception("failed to encode json cache: %s", cache_key)


def _format_race_id(race_id: str) -> tuple[str, int, int]:
    """'YYYYMMDD-SS-RR' → (date_str, stadium, race_no)"""
    parts = race_id.split("-")
    return parts[0], int(parts[1]), int(parts[2])


# stadiums テーブルは静的データ (24 競艇場、変動なし) なのでプロセス
# メモリに 1 回だけロード。これで race_detail / index 等の毎回の
# JOIN stadiums を排除できる (Supabase 往復 1 回分の節約)。
_STADIUMS_CACHE: Optional[dict[int, str]] = None


def _stadium_name_map() -> dict[int, str]:
    global _STADIUMS_CACHE
    if _STADIUMS_CACHE is not None:
        return _STADIUMS_CACHE
    with db_connect() as conn:
        rows = conn.execute("SELECT stadium_number, name FROM stadiums").fetchall()
    _STADIUMS_CACHE = {n: name for n, name in rows}
    return _STADIUMS_CACHE


def _race_basic_info(race_id: str) -> Optional[dict]:
    # races テーブルのみ問い合わせ、stadium_name はメモリキャッシュから付加
    # (旧コードは毎回 JOIN stadiums していたが、stadiums は静的なので不要)
    with db_connect() as conn:
        row = conn.execute("""
            SELECT race_id, race_date, stadium_number, race_number,
                   race_grade_number, race_title, race_subtitle,
                   race_closed_at
              FROM races
             WHERE race_id = ?
        """, (race_id,)).fetchone()
    if not row:
        return None
    stadium_names = _stadium_name_map()
    keys = ["race_id", "race_date", "stadium_number", "race_number",
            "race_grade_number", "race_title", "race_subtitle",
            "race_closed_at"]
    info = dict(zip(keys, row))
    info["stadium_name"] = stadium_names.get(info["stadium_number"], "")
    return info


def _races_for_date(target_date: str) -> list[dict]:
    """N+1 クエリ問題を排除: サブクエリを LEFT JOIN + GROUP BY に置換。
    168 サブクエリ -> 1 集約クエリで 5-10 倍高速化。

    results_count は finishing_position IS NOT NULL の行のみを数える。
    upsert_results はレース前でも出走表に基づき空シェル行 (place=None) を
    6 行書き込むため、単純な COUNT だと未終了レースも「確定」と誤判定する。
    """
    with db_connect() as conn:
        rows = conn.execute("""
            SELECT r.race_id, r.stadium_number, r.race_number, r.race_closed_at,
                   s.name AS stadium_name,
                   COALESCE(SUM(CASE WHEN res.finishing_position IS NOT NULL
                                     THEN 1 ELSE 0 END), 0) AS results_count
              FROM races r
              JOIN stadiums s ON r.stadium_number = s.stadium_number
              LEFT JOIN race_results res ON r.race_id = res.race_id
             WHERE r.race_date = ?
             GROUP BY r.race_id, r.stadium_number, r.race_number, r.race_closed_at, s.name
             ORDER BY r.stadium_number, r.race_number
        """, (target_date,)).fetchall()
    keys = ["race_id", "stadium_number", "race_number", "race_closed_at",
            "stadium_name", "results_count"]
    return [dict(zip(keys, r)) for r in rows]


def _race_predictions_from_cache(race_id: str, version: str) -> Optional[list[dict]]:
    """predictions テーブルからキャッシュ済予測を取得。
    Supabase Free でも軽量に動作するため、まずキャッシュを試みる。
    """
    with db_connect() as conn:
        try:
            rows = conn.execute("""
                SELECT p.boat_number, p.prob_first, p.prob_top_2, p.prob_top_3,
                       e.racer_number, e.class_number,
                       e.national_top_2_percent, e.local_top_2_percent,
                       e.assigned_motor_number,
                       e.assigned_motor_top_2_percent,
                       NULLIF(pv.exhibition_time, 0), pv.start_timing_exhibition,
                       res.finishing_position
                FROM predictions p
                JOIN race_entries e ON p.race_id = e.race_id AND p.boat_number = e.boat_number
                LEFT JOIN race_previews pv ON p.race_id = pv.race_id AND p.boat_number = pv.boat_number
                LEFT JOIN race_results res ON p.race_id = res.race_id AND p.boat_number = res.boat_number
                WHERE p.race_id = ? AND p.model_version = ?
                ORDER BY p.prob_first DESC
            """, (race_id, version)).fetchall()
        except Exception:
            return None
    if not rows:
        return None
    keys = ["boat_number", "prob_first", "prob_top_2", "prob_top_3",
            "racer_number", "class_number",
            "national_top_2_percent", "local_top_2_percent",
            "assigned_motor_number",
            "assigned_motor_top_2_percent",
            "exhibition_time", "start_timing_exhibition",
            "finishing_position"]
    out = []
    for i, row in enumerate(rows, 1):
        d = dict(zip(keys, row))
        d["pred_rank"] = i
        out.append(d)
    return out


def _race_predictions(predictor: Predictor, race_id: str) -> list[dict]:
    # まずキャッシュを試す (Supabase Free 対策)
    cached = _race_predictions_from_cache(race_id, predictor.version)
    if cached:
        return cached

    # Render 等の本番環境ではライブ計算を行わない (OOM/timeout 防止)。
    # キャッシュが無いレースは predictions テーブルへ事前投入が必要。
    # ローカル開発 (DATABASE_URL 未設定 or RENDER 未設定) ではフォールバック計算する。
    if os.environ.get("RENDER") or os.environ.get("DISABLE_LIVE_PREDICT"):
        logger.warning(
            "live predict skipped on production for race_id=%s "
            "(populate predictions table via scripts/cache_predictions.py)",
            race_id,
        )
        return []

    # キャッシュが無ければライブ計算 (ローカル開発のみ)
    target_date = race_id[:8]
    target_date_iso = f"{target_date[:4]}-{target_date[4:6]}-{target_date[6:8]}"
    df = predictor.predict_date(target_date_iso)
    if df.empty:
        return []
    sub = df[df["race_id"] == race_id].copy()
    if sub.empty:
        return []
    sub = sub.sort_values("prob_first", ascending=False)
    sub["pred_rank"] = range(1, len(sub) + 1)
    cols = [
        "boat_number", "racer_number",
        "class_number", "national_top_2_percent", "local_top_2_percent",
        "assigned_motor_number", "assigned_motor_top_2_percent",
        "exhibition_time", "start_timing_exhibition",
        "prob_first", "prob_top_2", "prob_top_3", "raw_score", "pred_rank",
        "finishing_position",
    ]
    available = [c for c in cols if c in sub.columns]
    return sub[available].to_dict(orient="records")


def _racer_names(race_id: str) -> dict[int, str]:
    with db_connect() as conn:
        rows = conn.execute("""
            SELECT boat_number, racer_name FROM race_entries WHERE race_id = ?
        """, (race_id,)).fetchall()
    return {bn: name for bn, name in rows}


def _kimarite_skill_tags_for_race(race_id: str) -> dict[int, dict]:
    """Return pre-race kimarite skill tags by boat number."""
    info = _race_basic_info(race_id)
    if not info:
        return {}
    with db_connect() as conn:
        rows = conn.execute("""
            WITH current_entries AS (
                SELECT boat_number, racer_number
                  FROM race_entries
                 WHERE race_id = ?
            )
            SELECT c.boat_number, rr.kimarite, COUNT(*) AS wins
              FROM current_entries c
              JOIN race_entries e
                ON e.racer_number = c.racer_number
               AND e.boat_number = c.boat_number
              JOIN races r
                ON r.race_id = e.race_id
               AND r.race_date < ?
              JOIN race_results rr
                ON rr.race_id = e.race_id
               AND rr.boat_number = e.boat_number
             WHERE rr.finishing_position = 1
               AND rr.kimarite IS NOT NULL
               AND rr.kimarite <> ''
             GROUP BY c.boat_number, rr.kimarite
             ORDER BY c.boat_number, wins DESC, rr.kimarite
        """, (race_id, info["race_date"])).fetchall()

    grouped: dict[int, list[tuple[str, int]]] = {}
    for boat_number, kimarite, wins in rows:
        grouped.setdefault(int(boat_number), []).append((str(kimarite), int(wins or 0)))

    tags: dict[int, dict] = {}
    for boat_number, items in grouped.items():
        top_kimarite, top_wins = items[0]
        threshold = 8 if boat_number == 1 and top_kimarite == "逃げ" else 3
        if top_wins < threshold:
            continue
        label = f"{top_kimarite}{top_wins}勝"
        if boat_number == 1 and top_kimarite == "逃げ" and top_wins >= 8:
            label = f"逃げ{top_wins}勝+"
        tags[boat_number] = {
            "kimarite": top_kimarite,
            "wins": top_wins,
            "label": label,
            "is_strong_escape": boat_number == 1 and top_kimarite == "逃げ" and top_wins >= 8,
        }
    return tags


def _attach_kimarite_skill_tags(race_id: str, preds: list[dict]) -> None:
    tags = _kimarite_skill_tags_for_race(race_id)
    if not tags:
        return
    for p in preds:
        try:
            boat_number = int(p.get("boat_number"))
        except (TypeError, ValueError):
            continue
        tag = tags.get(boat_number)
        if tag:
            p["kimarite_skill"] = tag


# Bootstrap CI で確実マイナスと検証された会場 (v0.2 検証)
_LOSING_VENUES = {2: "戸田", 7: "蒲郡", 10: "三国", 21: "芦屋"}
_QUESTIONABLE_VENUES = {4: "平和島", 8: "常滑", 19: "下関", 24: "大村"}

# 会場別のモーター入替月。履歴表示では現行モーター期だけを表示する。
_MOTOR_REPLACEMENT_MONTH = {
    1: 3, 2: 5, 3: 11, 4: 3, 5: 4, 6: 4, 7: 6, 8: 7,
    9: 8, 10: 3, 11: 10, 12: 5, 13: 7, 14: 6, 15: 9, 16: 4,
    17: 3, 18: 10, 19: 6, 20: 4, 21: 11, 22: 2, 23: 12, 24: 7,
}


def _motor_cycle_start(race_date_iso: str, stadium_number: int) -> Optional[str]:
    """Return YYYY-MM-01 for the current motor cycle at the venue."""
    replacement_month = _MOTOR_REPLACEMENT_MONTH.get(stadium_number)
    if not replacement_month:
        return None
    try:
        if isinstance(race_date_iso, datetime):
            d = race_date_iso.date()
        elif isinstance(race_date_iso, date):
            d = race_date_iso
        else:
            d = datetime.fromisoformat(str(race_date_iso)).date()
    except Exception:
        return None
    year = d.year if d.month >= replacement_month else d.year - 1
    return f"{year:04d}-{replacement_month:02d}-01"




def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _mean_or_none(values: list[float]) -> Optional[float]:
    return (sum(values) / len(values)) if values else None


def _finish_score(position: Optional[int]) -> Optional[float]:
    if position is None:
        return None
    table = {1: 100.0, 2: 82.0, 3: 66.0, 4: 44.0, 5: 24.0, 6: 10.0}
    return table.get(int(position), 0.0)


def _motor_profile_from_history(history: list[dict[str, Any]]) -> dict[str, Any]:
    if not history:
        return {
            "condition_label": "データ不足",
            "condition_tone": "flat",
            "condition_score": None,
            "style_label": "判定保留",
            "dash_score": None,
            "stretch_score": None,
            "turn_score": None,
            "recent_scores": [],
            "note": "展示タイム・展示ST・着順効率からの推定です。",
        }

    ex_vals = [float(r["exhibition_time"]) for r in history if r.get("exhibition_time") is not None]
    ex_st_vals = [float(r["start_timing_exhibition"]) for r in history if r.get("start_timing_exhibition") is not None]
    st_vals = [float(r["start_timing"]) for r in history if r.get("start_timing") is not None]
    avg_ex = _mean_or_none(ex_vals)
    avg_ex_st = _mean_or_none(ex_st_vals)
    avg_st = _mean_or_none(st_vals)

    turn_kimarite = {"差し", "まくり差し", "抜き", "恵まれ"}
    recent_scores: list[dict[str, Any]] = []
    dash_values: list[float] = []
    stretch_values: list[float] = []
    turn_values: list[float] = []
    overall_values: list[float] = []

    for row in reversed(history[:5]):
        stretch = 50.0
        if row.get("exhibition_time") is not None and avg_ex is not None:
            stretch += _clamp((avg_ex - float(row["exhibition_time"])) / 0.10 * 28.0, -22.0, 22.0)
        pos = row.get("finishing_position")
        if pos == 1:
            stretch += 10.0
        elif pos == 2:
            stretch += 5.0
        elif pos and pos >= 5:
            stretch -= 8.0
        stretch = _clamp(stretch, 0.0, 100.0)

        dash = 50.0
        if row.get("start_timing_exhibition") is not None and avg_ex_st is not None:
            dash += _clamp((avg_ex_st - float(row["start_timing_exhibition"])) / 0.08 * 18.0, -18.0, 18.0)
        if row.get("start_timing") is not None and avg_st is not None:
            dash += _clamp((avg_st - float(row["start_timing"])) / 0.08 * 18.0, -18.0, 18.0)
        if pos == 1:
            dash += 6.0
        dash = _clamp(dash, 0.0, 100.0)

        turn = 46.0
        finish_score = _finish_score(pos)
        if finish_score is not None:
            turn += (finish_score - 50.0) * 0.22
        course = row.get("course_number")
        if course is not None and pos is not None:
            turn += _clamp((float(course) - float(pos)) * 6.0, -18.0, 18.0)
        kimarite = str(row.get("kimarite") or "")
        if kimarite in turn_kimarite:
            turn += 10.0
        elif kimarite == "まくり":
            turn += 7.0
        elif kimarite == "逃げ":
            turn += 4.0
        turn = _clamp(turn, 0.0, 100.0)

        overall = _clamp(dash * 0.30 + stretch * 0.35 + turn * 0.35, 0.0, 100.0)
        dash_values.append(dash)
        stretch_values.append(stretch)
        turn_values.append(turn)
        overall_values.append(overall)

        if overall >= 62:
            trend_label = "上向き"
        elif overall <= 44:
            trend_label = "弱め"
        else:
            trend_label = "普通"

        recent_scores.append(
            {
                "race_date": row.get("race_date"),
                "race_number": row.get("race_number"),
                "score": round(overall, 1),
                "label": trend_label,
            }
        )

    dash_score = round(_mean_or_none(dash_values) or 0.0, 1) if dash_values else None
    stretch_score = round(_mean_or_none(stretch_values) or 0.0, 1) if stretch_values else None
    turn_score = round(_mean_or_none(turn_values) or 0.0, 1) if turn_values else None
    condition_score = round(_mean_or_none(overall_values) or 0.0, 1) if overall_values else None

    score_items = []
    if dash_score is not None:
        score_items.append(("出足", dash_score))
    if stretch_score is not None:
        score_items.append(("行き足・伸び", stretch_score))
    if turn_score is not None:
        score_items.append(("回り足", turn_score))
    score_items.sort(key=lambda item: item[1], reverse=True)

    if len(score_items) >= 2 and (score_items[0][1] - score_items[1][1]) < 6.0:
        style_label = "バランス型"
    else:
        style_map = {
            "出足": "出足型",
            "行き足・伸び": "行き足・伸び型",
            "回り足": "回り足型",
        }
        style_label = style_map.get(score_items[0][0], "判定保留") if score_items else "判定保留"

    if condition_score is None:
        condition_label = "判定保留"
        condition_tone = "flat"
    elif condition_score >= 62:
        condition_label = "上向き"
        condition_tone = "up"
    elif condition_score <= 44:
        condition_label = "弱め"
        condition_tone = "down"
    else:
        condition_label = "普通"
        condition_tone = "flat"

    return {
        "condition_label": condition_label,
        "condition_tone": condition_tone,
        "condition_score": condition_score,
        "style_label": style_label,
        "dash_score": dash_score,
        "stretch_score": stretch_score,
        "turn_score": turn_score,
        "recent_scores": recent_scores,
        "note": "展示タイム・展示ST・着順効率からの推定です。",
    }


def _racer_lift_profile(history: list[dict[str, Any]], racer_baseline: dict[str, Any]) -> dict[str, Any]:
    default = {
        "label": "判定保留",
        "tone": "flat",
        "value": None,
        "score": None,
        "sample_size": int(racer_baseline.get("starts") or 0),
        "exhibition_delta": None,
        "exhibition_st_delta": None,
        "start_delta": None,
        "finish_delta": None,
        "note": "選手の過去平均との差から、モーターをどれだけ引き出しているかを見ます。",
    }
    if not history:
        return default

    avg_ex = _mean_or_none([float(r["exhibition_time"]) for r in history if r.get("exhibition_time") is not None])
    avg_ex_st = _mean_or_none([float(r["start_timing_exhibition"]) for r in history if r.get("start_timing_exhibition") is not None])
    avg_st = _mean_or_none([float(r["start_timing"]) for r in history if r.get("start_timing") is not None])
    avg_finish = _mean_or_none([float(r["finishing_position"]) for r in history if r.get("finishing_position") is not None])

    base_ex = racer_baseline.get("avg_exhibition_time")
    base_ex_st = racer_baseline.get("avg_start_timing_exhibition")
    base_st = racer_baseline.get("avg_start_timing")
    base_finish = racer_baseline.get("avg_finish_position")

    ex_delta = (base_ex - avg_ex) if (base_ex is not None and avg_ex is not None) else None
    ex_st_delta = (base_ex_st - avg_ex_st) if (base_ex_st is not None and avg_ex_st is not None) else None
    st_delta = (base_st - avg_st) if (base_st is not None and avg_st is not None) else None
    finish_delta = (base_finish - avg_finish) if (base_finish is not None and avg_finish is not None) else None

    lift_value = (
        (ex_delta or 0.0) * 0.55
        + (ex_st_delta or 0.0) * 0.20
        + (st_delta or 0.0) * 0.15
        + ((finish_delta or 0.0) * 0.02) * 0.10
    )

    if lift_value >= 0.03:
        label = "上振れ"
        tone = "up"
    elif lift_value <= -0.03:
        label = "下振れ"
        tone = "down"
    else:
        label = "平均並み"
        tone = "flat"

    default.update(
        {
            "label": label,
            "tone": tone,
            "value": round(lift_value, 2),
            "score": round(_clamp(50.0 + lift_value * 400.0, 0.0, 100.0), 1),
            "exhibition_delta": round(ex_delta, 2) if ex_delta is not None else None,
            "exhibition_st_delta": round(ex_st_delta, 2) if ex_st_delta is not None else None,
            "start_delta": round(st_delta, 2) if st_delta is not None else None,
            "finish_delta": round(finish_delta, 2) if finish_delta is not None else None,
        }
    )
    return default


def _motor_history_live_signal(
    current_preview: dict[str, Any],
    history: list[dict[str, Any]],
    racer_baseline: dict[str, Any],
) -> dict[str, Any]:
    signal = {
        "trend_label": "flat",
        "trend_tone": "flat",
        "trend_value": None,
        "trend_score": None,
        "lift_label": "standard",
        "lift_tone": "flat",
        "lift_value": None,
        "lift_score": None,
    }
    if not history:
        return signal

    current_ex = current_preview.get("exhibition_time")
    current_ex_st = current_preview.get("start_timing_exhibition")
    if current_ex is None:
        return signal

    motor_avg_ex = _mean_or_none([float(r["exhibition_time"]) for r in history if r.get("exhibition_time") is not None])
    motor_avg_ex_st = _mean_or_none([float(r["start_timing_exhibition"]) for r in history if r.get("start_timing_exhibition") is not None])
    racer_avg_ex = racer_baseline.get("avg_exhibition_time")
    racer_avg_ex_st = racer_baseline.get("avg_start_timing_exhibition")

    trend_value = (motor_avg_ex - float(current_ex)) if motor_avg_ex is not None else None
    if trend_value is not None:
        if trend_value >= 0.08:
            signal["trend_label"] = "rise_strong"
            signal["trend_tone"] = "up"
        elif trend_value >= 0.03:
            signal["trend_label"] = "rise"
            signal["trend_tone"] = "up"
        elif trend_value <= -0.05:
            signal["trend_label"] = "down"
            signal["trend_tone"] = "down"
        else:
            signal["trend_label"] = "flat"
            signal["trend_tone"] = "flat"
        signal["trend_value"] = round(trend_value, 2)
        signal["trend_score"] = round(_clamp(50.0 + trend_value * 320.0, 0.0, 100.0), 1)

    lift_value = None
    if racer_avg_ex is not None:
        lift_value = (racer_avg_ex - float(current_ex)) * 0.75
    if current_ex_st is not None and racer_avg_ex_st is not None:
        lift_value = (lift_value or 0.0) + ((racer_avg_ex_st - float(current_ex_st)) * 0.25)
    if lift_value is not None:
        if lift_value >= 0.08:
            signal["lift_label"] = "A"
            signal["lift_tone"] = "up"
        elif lift_value >= 0.03:
            signal["lift_label"] = "B"
            signal["lift_tone"] = "up"
        elif lift_value <= -0.04:
            signal["lift_label"] = "D"
            signal["lift_tone"] = "down"
        else:
            signal["lift_label"] = "C"
            signal["lift_tone"] = "flat"
        signal["lift_value"] = round(lift_value, 2)
        signal["lift_score"] = round(_clamp(50.0 + lift_value * 360.0, 0.0, 100.0), 1)

    return signal


def _detect_market_inefficiency(
    race_id: str,
    preds: list[dict],
    info: Optional[dict] = None,
) -> Optional[dict]:
    """
    「市場非効率レース」検出。
    検証結果 (2026年データ):
      - 三連単1番人気 500-1000円帯 + 1号艇単勝 → ROI +27.41% (P>0=100%)
      - 三連単1番人気 <500円帯 → ROI +18.45%
      - 三連単1番人気 1000-2000円帯 → ROI +17.92%

    重ね掛け強化 (検証2026):
      - 節後半 (5日目以降) + 500-1000帯 → ROI +28.36% (n=1,905)
      - 一般戦 B1 1号艇 + 500-1000帯 → ROI +35.39% (n=738)
      - 3連単 1-2-3 (同条件) → ROI +44.23% (n=3,465)

    Returns:
      {
        "favorite_trifecta_payout": int or None,
        "tier": "ultra_confident" | "confident" | "moderate" | "split" | "wild" | None,
        "expected_roi": float,
        "title": str,
        "msg": str,
        "extras": [list of additional +EV refinements]
      } or None
    """
    # 三連単の最小払戻 = 一番人気の払戻
    with db_connect() as conn:
        cur = conn.execute(
            "SELECT MIN(payout) FROM race_payouts WHERE race_id = ? AND bet_type = 'trifecta'",
            (race_id,),
        )
        row = cur.fetchone()
    min_payout = row[0] if row and row[0] else None

    # 事後判定 (race_payouts は確定後しか入らない)
    if min_payout is not None:
        result = None
        if min_payout < 500:
            result = {
                "favorite_trifecta_payout": min_payout,
                "tier": "ultra_confident",
                "expected_roi": 0.1845,
                "title": "超本命レース",
                "msg": f"三連単1番人気 ¥{min_payout:,} (<500円帯)",
            }
        elif min_payout < 1000:
            result = {
                "favorite_trifecta_payout": min_payout,
                "tier": "confident",
                "expected_roi": 0.2741,
                "title": "完全 +EV レース",
                "msg": f"三連単1番人気 ¥{min_payout:,} (500-1000円帯)",
            }
        elif min_payout < 2000:
            result = {
                "favorite_trifecta_payout": min_payout,
                "tier": "moderate",
                "expected_roi": 0.1792,
                "title": "やや本命 +EV",
                "msg": f"三連単1番人気 ¥{min_payout:,} (1k-2k帯)。2026検証 ROI +17.92%",
            }
        elif min_payout < 5000:
            result = {
                "favorite_trifecta_payout": min_payout,
                "tier": "split",
                "expected_roi": -0.0859,
                "title": "拮抗レース",
                "msg": f"三連単1番人気 ¥{min_payout:,} (拮抗)。2026検証 ROI -8.59% (買い控え推奨)",
            }
        elif min_payout < 10000:
            result = {
                "favorite_trifecta_payout": min_payout,
                "tier": "wild",
                "expected_roi": -0.4310,
                "title": "荒れ寄り",
                "msg": f"三連単1番人気 ¥{min_payout:,}。2026検証 ROI -43.10% (1号艇単勝非推奨)",
            }
        else:
            result = {
                "favorite_trifecta_payout": min_payout,
                "tier": "chaos",
                "expected_roi": -0.7354,
                "title": "波乱レース",
                "msg": f"三連単1番人気 ¥{min_payout:,} (波乱)。2026検証 ROI -73.54% (本命非推奨)",
            }

        # 重ね掛け強化シグナルを extras に追加
        extras = []
        if min_payout < 2000 and preds:
            boat1 = next((p for p in preds if p.get("boat_number") == 1), None)
            if boat1:
                cls = boat1.get("class_number")
                stadium = info.get("stadium_number") if info else None
                grade = info.get("race_grade_number") if info else None
                exclude_b = stadium not in (_LOSING_VENUES | _QUESTIONABLE_VENUES.keys()) if stadium else False
                in_500_1000 = 500 <= min_payout < 1000

                # 一般戦 + B1 1号艇 + 本命500-1k = ROI +35.39%
                if grade == 5 and cls == 3 and in_500_1000:
                    extras.append({
                        "label": "🔥 一般戦+B1+本命",
                        "msg": "一般戦 + B1 1号艇 + 本命500-1k は 検証 ROI +35.39% (CI +28.3%~+42.6%, n=738)",
                    })
                # SG/G1 + A1 1号艇 + 本命500-1k = ROI +27.03%
                if grade in (1, 2) and cls == 1 and in_500_1000:
                    extras.append({
                        "label": "🔥 SG/G1+A1+本命",
                        "msg": "SG/G1 + A1 1号艇 + 本命500-1k は 検証 ROI +27.03% (CI +22.6%~+32.4%, n=209)",
                    })

                # ===== L4 戦略マーク (3連単1-2-3 + B除外 + 1号艇A1 + 500-1000帯) =====
                # 通算回収率 160.8% (CI 148.9-173.7, n=2,210)
                if in_500_1000 and exclude_b and cls == 1:
                    # 強化版: G1 で 242.8% (CI 196.8-292.1, n=227)
                    if grade == 2:
                        extras.append({
                            "label": "👑 L4★G1 (3連単1-2-3 推奨)",
                            "msg": "G1 + 1号艇A1 + B除外 + 本命500-1k で 3連単 1-2-3 = 検証 回収率 242.8% (CI 196.8%-292.1%, n=227, HIT 31.3%)",
                            "bet": "3連単 1-2-3 を 100円",
                            "expected_roi": 1.428,
                        })
                    # 強化版: SG で 258.2% (CI 141.0-393.0, n=40)
                    elif grade == 1:
                        extras.append({
                            "label": "👑 L4★SG (3連単1-2-3 超推奨)",
                            "msg": "SG + 1号艇A1 + B除外 + 本命500-1k で 3連単 1-2-3 = 検証 回収率 258.2% (CI 141.0%-393.0%, n=40, HIT 32.5%)",
                            "bet": "3連単 1-2-3 を 100円",
                            "expected_roi": 1.582,
                        })
                    # 強化版: G2 で 242.7% (n=30、小サンプル)
                    elif grade == 3:
                        extras.append({
                            "label": "👑 L4★G2 (3連単1-2-3 推奨)",
                            "msg": "G2 + 1号艇A1 + B除外 + 本命500-1k で 3連単 1-2-3 = 検証 回収率 242.7% (CI 126.0%-375.3%, n=30, HIT 33.3%)",
                            "bet": "3連単 1-2-3 を 100円",
                            "expected_roi": 1.427,
                        })
                    # 一般戦 (大多数): 147.7% (CI 134.0-160.2, n=1,776)
                    elif grade == 5:
                        extras.append({
                            "label": "🎯 L4 一般戦 (3連単1-2-3 推奨)",
                            "msg": "一般戦 + 1号艇A1 + B除外 + 本命500-1k で 3連単 1-2-3 = 検証 回収率 147.7% (CI 134.0%-160.2%, n=1,776, HIT 21.4%)",
                            "bet": "3連単 1-2-3 を 100円",
                            "expected_roi": 1.477,
                        })
                    # それ以外 (G3 等)
                    else:
                        extras.append({
                            "label": "🎯 L4 (3連単1-2-3 推奨)",
                            "msg": "1号艇A1 + B除外 + 本命500-1k で 3連単 1-2-3 = 検証 通算回収率 160.8% (CI 148.9%-173.7%, n=2,210)",
                            "bet": "3連単 1-2-3 を 100円",
                            "expected_roi": 1.608,
                        })

                # A2 派生 / L2 派生は L4 戦略対象外なので extras に追加しない

        if extras:
            result["extras"] = extras
        return result

    # ===== 事前判定 (朝の段階、final odds が出る前のモデル予測ベース) =====
    # 朝の出走表 + 予測のみで判定可能なシグナルを生成
    boat1_pred = next((p for p in (preds or []) if p.get("boat_number") == 1), None)
    if boat1_pred:
        p1 = boat1_pred.get("prob_first") or 0
        cls = boat1_pred.get("class_number")
        stadium = info.get("stadium_number") if info else None
        grade = info.get("race_grade_number") if info else None
        b_excluded = stadium not in (_LOSING_VENUES.keys() | _QUESTIONABLE_VENUES.keys()) if stadium else False

        # 【朝L4候補】 prob_first 0.65-0.85 (≒ 三連単本命500-2000円帯相当)
        # + 1号艇A1 + B除外 → L4 戦略の有力候補
        # データ検証: prob_first 0.65-0.85 帯のうち約 30-38% が実際に500-1000円帯に着地
        if 0.65 <= p1 < 0.85 and cls == 1 and b_excluded:
            morning_l4 = {
                "tier": "morning_l4",
                "expected_roi": 0.50,  # 期待値中央値 (G1なら高、一般戦なら低)
                "title": "🌅 朝L4 候補",
                "msg": (f"モデル予測 1号艇A1 1着率 {p1*100:.1f}% + B除外。"
                        f"三連単本命が500-1000円帯になる確率 ~38%。"
                        f"確定したら L4 戦略 (3連単1-2-3) を実行"),
                "is_morning": True,
                "favorite_trifecta_payout": None,
            }
            # グレード強化
            if grade in (1, 2, 3):  # SG/G1/G2
                grade_label = {1: "SG", 2: "G1", 3: "G2"}.get(grade)
                morning_l4["title"] = f"🌅👑 朝L4★{grade_label} 強候補"
                morning_l4["expected_roi"] = 1.5  # 過去実績 242-258%
                morning_l4["msg"] = (
                    f"{grade_label} + 1号艇A1 + B除外 + 予測 {p1*100:.1f}%。"
                    f"L4 強化版 ({grade_label}×A1) は検証 回収率 242-258%。"
                    f"確定オッズが500-1000円帯になれば 3連単1-2-3 を厚めに"
                )
            elif grade == 5:  # 一般戦
                morning_l4["title"] = "🌅 朝L4 一般戦候補"
                morning_l4["msg"] = (
                    f"一般戦 + 1号艇A1 + B除外 + 予測 {p1*100:.1f}%。"
                    f"L4 一般戦版 は検証 回収率 147.7% (CI 134-160%)。"
                    f"確定後 500-1000円帯なら 3連単1-2-3 を実行"
                )
            return morning_l4

        # A2 派生 / 旧 predicted_* は L4 戦略対象外なので表示しない
    return None


def _detect_niche_signals(preds: list[dict], conditions: dict) -> list[dict]:
    """
    検証済の「ニッチ大穴シグナル」を検出する。
    Returns: 該当艇のシグナル情報リスト
    """
    signals = []
    cls_map = {1: "A1", 2: "A2", 3: "B1", 4: "B2"}

    # 艇番→予測情報のマップ
    by_boat = {p.get("boat_number"): p for p in (preds or [])}
    # 艇番→直前情報 (tilt含む) のマップ
    boats_cond = (conditions or {}).get("boats", {})

    for boat_num in [1, 2, 3, 4, 5, 6]:
        p = by_boat.get(boat_num)
        bc = boats_cond.get(boat_num) or boats_cond.get(str(boat_num)) or {}
        if not p:
            continue
        tilt = bc.get("tilt_adjustment")
        cls = p.get("class_number")
        cls_label = cls_map.get(cls, "?")

        if tilt is None:
            continue

        # 検証済シグナル: 艇5 + tilt=3.0 + A2選手 (P>0=95%)
        if boat_num == 5 and tilt == 3.0 and cls == 2:
            signals.append({
                "level": "ultra",
                "boat_number": 5,
                "tilt": tilt,
                "class_label": cls_label,
                "title": "🔥🔥🔥 ニッチ大穴チャンス",
                "desc": f"艇5 + チルト3.0 + A2選手の組合せ。Backtest ROI +118.29% (n=41, CI [-13%, +290%], P(ROI>0)=95.0%)",
                "recommend": "三連単 5-X-Y 上位10通り買い推奨",
                "warning": "n=41 のサンプル。実運用は要慎重",
            })
        # 検証済シグナル: 艇5 + tilt=3.0 + A1+A2 (P>0=66-71%)
        elif boat_num == 5 and tilt == 3.0 and cls in (1, 2):
            signals.append({
                "level": "high",
                "boat_number": 5,
                "tilt": tilt,
                "class_label": cls_label,
                "title": "🔥🔥 大まくり勝負賭け",
                "desc": f"艇5 + チルト3.0 + {cls_label}選手。Backtest ROI +22.60% (n=73, P(ROI>0)=65.5%)",
                "recommend": "三連単 5-X-Y 上位10通り買い検討",
                "warning": "サンプル小、慎重に",
            })
        # 検証済シグナル: 艇4 + tilt 0.5-1.5 (まくり狙い、最も n 大)
        elif boat_num == 4 and tilt is not None and 0.5 <= tilt <= 1.5:
            signals.append({
                "level": "mid",
                "boat_number": 4,
                "tilt": tilt,
                "class_label": cls_label,
                "title": "🔥 4号艇まくり狙い",
                "desc": f"艇4 + チルト{tilt:+.1f}。Backtest ROI -11.09% (n=2,202, P(ROI>0)=10.0%)",
                "recommend": "通常買いより 4 絡みの妙味あり",
                "warning": "+EV ではないが通常戦略より優位",
            })
        # 検証済シグナル: 艇5 tilt>=1.5 (大まくり狙い、A2絡みでない場合)
        elif boat_num == 5 and tilt is not None and tilt >= 1.5 and cls not in (1, 2):
            signals.append({
                "level": "low",
                "boat_number": 5,
                "tilt": tilt,
                "class_label": cls_label,
                "title": "⚙️ 5号艇 伸びセッティング",
                "desc": f"艇5 + チルト{tilt:+.1f} + {cls_label}選手 (上位級ではない)",
                "recommend": "効果限定的、参考情報",
                "warning": "級別が低くまくり成立率低い",
            })
        # 一般的なプラスチルト警告 (情報)
        elif tilt is not None and tilt >= 1.0 and boat_num >= 3:
            signals.append({
                "level": "info",
                "boat_number": boat_num,
                "tilt": tilt,
                "class_label": cls_label,
                "title": f"⚙️ 艇{boat_num} プラスチルト",
                "desc": f"チルト{tilt:+.1f} = 伸び/まくり狙いの可能性",
                "recommend": "参考情報",
                "warning": None,
            })

    return signals


def _race_actual_result(race_id: str) -> Optional[dict]:
    """確定結果と各券種の払戻を取得。結果が無ければ None."""
    with db_connect() as conn:
        # 着順
        rows = conn.execute("""
            SELECT boat_number, finishing_position, course_number, start_timing, kimarite
              FROM race_results
             WHERE race_id = ?
             ORDER BY finishing_position
        """, (race_id,)).fetchall()
        if not rows:
            return None
        finishers = []
        positions: dict[int, int] = {}
        for bn, pos, course, st, kim in rows:
            if pos is None:
                continue
            try:
                pos_int = int(pos)
            except (TypeError, ValueError):
                continue
            positions[int(bn)] = pos_int
            finishers.append({
                "boat_number": int(bn),
                "position": pos_int,
                "course_number": int(course) if course is not None else None,
                "start_timing": float(st) if st is not None else None,
                "kimarite": kim,
            })
        # 払戻
        payout_rows = conn.execute("""
            SELECT bet_type, combination, payout
              FROM race_payouts WHERE race_id = ?
        """, (race_id,)).fetchall()
    if not finishers:
        return None
    finishers.sort(key=lambda f: f["position"])
    payouts: dict[str, list[dict]] = {}
    for bt, comb, p in payout_rows:
        payouts.setdefault(bt, []).append({"combination": comb, "payout": int(p)})

    # 1着-2着-3着 の組合せ
    by_pos = {f["position"]: f["boat_number"] for f in finishers}
    trifecta_combo = None
    if 1 in by_pos and 2 in by_pos and 3 in by_pos:
        trifecta_combo = f"{by_pos[1]}-{by_pos[2]}-{by_pos[3]}"
    return {
        "finishers": finishers,
        "by_position": by_pos,
        "trifecta_combo": trifecta_combo,
        "payouts": payouts,
    }


def _race_current_conditions(race_id: str) -> dict:
    """
    race_previews から現状のレース条件と艇別の展示値を取得。
    What-if シミュレーターの初期値プリフィル用。
    """
    out = {
        "wind_speed": None, "wave_height": None, "temperature": None, "water_temperature": None,
        "wind_direction_number": None, "weather_number": None,
        "boats": {},   # {boat_number: {exhibition_time, start_timing_exhibition, course_number, tilt_adjustment, weight_adjustment, lap_time, turn_time, straight_time, original_rank}}
    }
    with db_connect() as conn:
        rows = conn.execute("""
            SELECT boat_number, weather_number, wind_speed, wind_direction_number,
                   wave_height, temperature, water_temperature,
                   course_number, NULLIF(exhibition_time, 0), start_timing_exhibition,
                   weight_adjustment, tilt_adjustment
              FROM race_previews
             WHERE race_id = ?
             ORDER BY boat_number
        """, (race_id,)).fetchall()
    keys = ["boat_number", "weather_number", "wind_speed", "wind_direction_number",
            "wave_height", "temperature", "water_temperature",
            "course_number", "exhibition_time", "start_timing_exhibition",
            "weight_adjustment", "tilt_adjustment"]
    for row in rows:
        d = dict(zip(keys, row))
        # レース全体の値 (どの行でも同じはずなので最初の値で上書き)
        for k in ("weather_number", "wind_speed", "wind_direction_number",
                  "wave_height", "temperature", "water_temperature"):
            if out[k] is None and d.get(k) is not None:
                out[k] = d[k]
        # 艇別
        out["boats"][int(d["boat_number"])] = {
            "course_number": d.get("course_number"),
            "exhibition_time": d.get("exhibition_time"),
            "start_timing_exhibition": d.get("start_timing_exhibition"),
            "weight_adjustment": d.get("weight_adjustment"),
            "tilt_adjustment": d.get("tilt_adjustment"),
        }
    try:
        with db_connect() as conn:
            original_rows = conn.execute(
                """
                SELECT boat_number, lap_time, turn_time, straight_time, original_rank
                  FROM race_original_exhibitions
                 WHERE race_id = ?
                 ORDER BY boat_number, collected_at DESC, source_name
                """,
                (race_id,),
            ).fetchall()
    except Exception:
        original_rows = []
    seen_original: set[int] = set()
    for boat_number, lap_time, turn_time, straight_time, original_rank in original_rows:
        bn = int(boat_number)
        if bn in seen_original:
            continue
        seen_original.add(bn)
        boat = out["boats"].setdefault(bn, {})
        boat["lap_time"] = lap_time
        boat["turn_time"] = turn_time
        boat["straight_time"] = straight_time
        boat["original_rank"] = original_rank
    return out


_WATER_LABELS = {
    "fresh": "淡水",
    "brackish": "汽水",
    "sea": "海水",
}

_WEATHER_LABELS = {
    1: "晴れ",
    2: "曇り",
    3: "雨",
    4: "雪",
}

_COURSE_TYPE_LABELS = {
    "逃げ": "逃げ型",
    "差し": "差し巧者",
    "まくり": "まくり型",
    "まくり差し": "まくり差し型",
    "抜き": "さばき型",
    "恵まれ": "展開待ち",
}


def _safe_float(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _safe_int(value):
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _branch_label(branch_number: Any) -> str:
    try:
        n = int(branch_number)
    except Exception:
        return "-"
    return f"支部{n}"


def _class_label(class_number: Any) -> str:
    return {1: "A1", 2: "A2", 3: "B1", 4: "B2"}.get(class_number, "-")


def _diff_tone(value: Optional[float]) -> str:
    if value is None:
        return "flat"
    if value >= 6.0:
        return "up"
    if value <= -6.0:
        return "down"
    return "flat"


def _signed_text(value: Optional[float], digits: int = 1, suffix: str = "") -> str:
    if value is None:
        return "-"
    return f"{value:+.{digits}f}{suffix}"


def _pair_cell_tone(wins: int, losses: int) -> str:
    diff = wins - losses
    if diff >= 4:
        return "up"
    if diff <= -4:
        return "down"
    return "flat"


def _water_difficulty_profile(
    stadium_number: int,
    stadium_water: Optional[str],
    tide_effect: Optional[str],
    conditions: dict[str, Any],
    tide_row: dict[str, Any],
) -> dict[str, Any]:
    wind_speed = _safe_float(conditions.get("wind_speed")) or 0.0
    wave_height = _safe_float(conditions.get("wave_height")) or 0.0
    weather_number = _safe_int(conditions.get("weather_number"))
    score = 36.0
    if stadium_water == "sea":
        score += 8.0
    elif stadium_water == "brackish":
        score += 6.0
    if tide_effect == "high":
        score += 5.0
    elif tide_effect == "mid":
        score += 2.0
    if wind_speed >= 7.0:
        score += 16.0
    elif wind_speed >= 5.0:
        score += 11.0
    elif wind_speed >= 3.0:
        score += 6.0
    if wave_height >= 10.0:
        score += 12.0
    elif wave_height >= 5.0:
        score += 6.0
    if weather_number in (3, 4):
        score += 6.0
    if int(tide_row.get("is_high_tide_zone") or 0) == 1 or int(tide_row.get("is_low_tide_zone") or 0) == 1:
        score += 5.0
    if stadium_number in {2, 3, 4, 14}:
        score += 10.0
    elif stadium_number in {19, 21, 24}:
        score -= 4.0
    score = _clamp(score, 0.0, 100.0)
    if score < 38:
        grade = "S"
        label = "かなり穏やか"
    elif score < 50:
        grade = "A"
        label = "穏やか"
    elif score < 62:
        grade = "B"
        label = "やや荒れ気味"
    elif score < 74:
        grade = "C"
        label = "荒れ気味"
    elif score < 86:
        grade = "D"
        label = "難水面"
    else:
        grade = "E"
        label = "かなり難しい"
    return {
        "score": round(score, 1),
        "grade": grade,
        "label": label,
    }


def _build_type_label(top_kimarite: Optional[str], current_course: int, course_diff_points: Optional[float]) -> str:
    if top_kimarite:
        if top_kimarite == "逃げ" and current_course == 1:
            return "堅実: 1C逃げ型"
        if top_kimarite in ("まくり", "まくり差し") and current_course >= 4:
            return f"尖り: {current_course}C攻め型"
        mapped = _COURSE_TYPE_LABELS.get(top_kimarite)
        if mapped:
            return mapped
    if course_diff_points is not None and course_diff_points >= 10:
        return "得意コース"
    if course_diff_points is not None and course_diff_points <= -10:
        return "割引コース"
    return "標準型"


def _motor_history_payload(race_id: str, boat_number: int, info: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
    if boat_number < 1 or boat_number > 6:
        return None
    info = info or _race_basic_info(race_id)
    if not info:
        return None

    with db_connect() as conn:
        current_row = conn.execute(
            """
            SELECT assigned_motor_number, assigned_motor_top_2_percent,
                   assigned_motor_top_3_percent, racer_name, racer_number,
                   NULLIF(pv.exhibition_time, 0) AS exhibition_time,
                   pv.start_timing_exhibition
              FROM race_entries
              LEFT JOIN race_previews pv
                ON pv.race_id = race_entries.race_id
               AND pv.boat_number = race_entries.boat_number
             WHERE race_entries.race_id = ? AND race_entries.boat_number = ?
            """,
            (race_id, boat_number),
        ).fetchone()

    if not current_row:
        return None

    motor_no, motor_top2, motor_top3, current_racer, current_racer_number, current_ex_time, current_ex_st = current_row
    cycle_start = _motor_cycle_start(info["race_date"], info["stadium_number"])
    current = {
        "race_id": race_id,
        "race_date": info["race_date"],
        "race_number": info["race_number"],
        "boat_number": boat_number,
        "racer_name": current_racer,
        "racer_number": current_racer_number,
        "stadium_number": info["stadium_number"],
        "stadium_name": info.get("stadium_name", ""),
        "motor_number": motor_no,
        "motor_top_2_percent": motor_top2,
        "motor_top_3_percent": motor_top3,
        "motor_cycle_start": cycle_start,
        "motor_replacement_month": _MOTOR_REPLACEMENT_MONTH.get(info["stadium_number"]),
        "exhibition_time": current_ex_time,
        "start_timing_exhibition": current_ex_st,
    }
    if motor_no is None:
        return {
            "current": current,
            "summary": {},
            "profile": _motor_profile_from_history([]),
            "lift": _racer_lift_profile([], {}),
            "live_signal": _motor_history_live_signal(current, [], {}),
            "history": [],
        }

    cycle_filter_sql = "AND r.race_date >= ?" if cycle_start else ""
    params: list[Any] = [
        info["stadium_number"],
        motor_no,
        info["race_date"],
        info["race_date"],
        info["race_number"],
    ]
    if cycle_start:
        params.append(cycle_start)

    with db_connect() as conn:
        rows = conn.execute(
            f"""
            WITH hist AS (
                SELECT r.race_id, r.race_date, r.race_number,
                       e.boat_number, e.racer_name, e.racer_number,
                       e.assigned_motor_top_2_percent,
                       e.assigned_motor_top_3_percent,
                       NULLIF(pv.exhibition_time, 0) AS exhibition_time,
                       pv.start_timing_exhibition,
                       rr.finishing_position, rr.course_number,
                       rr.start_timing, NULLIF(rr.kimarite, '') AS boat_kimarite
                  FROM races r
                  JOIN race_entries e
                    ON e.race_id = r.race_id
                  JOIN race_results rr
                    ON rr.race_id = e.race_id
                   AND rr.boat_number = e.boat_number
                  LEFT JOIN race_previews pv
                    ON pv.race_id = e.race_id
                   AND pv.boat_number = e.boat_number
                 WHERE r.stadium_number = ?
                   AND e.assigned_motor_number = ?
                   AND (r.race_date < ? OR (r.race_date = ? AND r.race_number < ?))
                   {cycle_filter_sql}
                   AND rr.finishing_position IS NOT NULL
                 ORDER BY r.race_date DESC, r.race_number DESC
                 LIMIT 10
            )
            SELECT h.race_id, h.race_date, h.race_number,
                   h.boat_number, h.racer_name, h.racer_number,
                   h.assigned_motor_top_2_percent,
                   h.assigned_motor_top_3_percent,
                   h.exhibition_time, h.start_timing_exhibition,
                   h.finishing_position, h.course_number,
                   h.start_timing,
                   COALESCE(
                       h.boat_kimarite,
                       (
                           SELECT MAX(NULLIF(kimarite, ''))
                             FROM race_results kr
                            WHERE kr.race_id = h.race_id
                              AND kr.kimarite IS NOT NULL
                              AND kr.kimarite <> ''
                       )
                   ) AS kimarite
              FROM hist h
             ORDER BY h.race_date DESC, h.race_number DESC
            """,
            tuple(params),
        ).fetchall()

    keys = [
        "race_id", "race_date", "race_number", "boat_number",
        "racer_name", "racer_number", "motor_top_2_percent",
        "motor_top_3_percent", "exhibition_time",
        "start_timing_exhibition", "finishing_position",
        "course_number", "start_timing", "kimarite",
    ]
    history = [dict(zip(keys, row)) for row in rows]

    starts = len(history)
    wins = sum(1 for r in history if r.get("finishing_position") == 1)
    top2 = sum(1 for r in history if (r.get("finishing_position") or 99) <= 2)
    top3 = sum(1 for r in history if (r.get("finishing_position") or 99) <= 3)
    st_vals = [r["start_timing"] for r in history if r.get("start_timing") is not None]
    ex_vals = [r["exhibition_time"] for r in history if r.get("exhibition_time") is not None]
    ex_st_vals = [r["start_timing_exhibition"] for r in history if r.get("start_timing_exhibition") is not None]
    summary = {
        "starts": starts,
        "wins": wins,
        "top2": top2,
        "top3": top3,
        "win_rate": (wins / starts * 100.0) if starts else None,
        "top2_rate": (top2 / starts * 100.0) if starts else None,
        "top3_rate": (top3 / starts * 100.0) if starts else None,
        "avg_start_timing": _mean_or_none([float(v) for v in st_vals]),
        "avg_exhibition_time": _mean_or_none([float(v) for v in ex_vals]),
        "avg_start_timing_exhibition": _mean_or_none([float(v) for v in ex_st_vals]),
    }

    with db_connect() as conn:
        racer_rows = conn.execute(
            """
            SELECT NULLIF(pv.exhibition_time, 0) AS exhibition_time,
                   pv.start_timing_exhibition,
                   rr.start_timing,
                   rr.finishing_position
              FROM races r
              JOIN race_entries e
                ON e.race_id = r.race_id
              JOIN race_results rr
                ON rr.race_id = e.race_id
               AND rr.boat_number = e.boat_number
              LEFT JOIN race_previews pv
                ON pv.race_id = e.race_id
               AND pv.boat_number = e.boat_number
             WHERE e.racer_number = ?
               AND (r.race_date < ? OR (r.race_date = ? AND r.race_number < ?))
               AND rr.finishing_position IS NOT NULL
             ORDER BY r.race_date DESC, r.race_number DESC
             LIMIT 20
            """,
            (current_racer_number, info["race_date"], info["race_date"], info["race_number"]),
        ).fetchall()

    racer_baseline = {
        "starts": len(racer_rows),
        "avg_exhibition_time": _mean_or_none([float(r[0]) for r in racer_rows if r[0] is not None]),
        "avg_start_timing_exhibition": _mean_or_none([float(r[1]) for r in racer_rows if r[1] is not None]),
        "avg_start_timing": _mean_or_none([float(r[2]) for r in racer_rows if r[2] is not None]),
        "avg_finish_position": _mean_or_none([float(r[3]) for r in racer_rows if r[3] is not None]),
    }

    profile = _motor_profile_from_history(history)
    lift = _racer_lift_profile(history, racer_baseline)
    live_signal = _motor_history_live_signal(current, history, racer_baseline)
    return {
        "current": current,
        "summary": summary,
        "profile": profile,
        "lift": lift,
        "live_signal": live_signal,
        "history": history,
    }


def _build_race_compat_analysis(race_id: str, info: dict[str, Any], preds: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    conditions = _race_current_conditions(race_id)
    target_date = info["race_date"]
    target_race_no = info["race_number"]

    with db_connect() as conn:
        current_rows = conn.execute(
            """
            SELECT e.boat_number, e.racer_number, e.racer_name, e.class_number, e.branch_number,
                   e.assigned_motor_number, e.assigned_motor_top_2_percent,
                   e.national_top_1_percent, e.local_top_1_percent,
                   COALESCE(pv.course_number, e.boat_number) AS current_course,
                   NULLIF(pv.exhibition_time, 0) AS exhibition_time,
                   pv.start_timing_exhibition
              FROM race_entries e
              LEFT JOIN race_previews pv
                ON pv.race_id = e.race_id
               AND pv.boat_number = e.boat_number
             WHERE e.race_id = ?
             ORDER BY e.boat_number
            """,
            (race_id,),
        ).fetchall()
        if not current_rows:
            return None

        stadium_row = conn.execute(
            """
            SELECT s.water, s.in_strength, s.tide_effect,
                   COALESCE(rt.tide_phase, '') AS tide_phase,
                   rt.tide_height_cm, rt.tide_delta_60m_cm, rt.tide_range_cm,
                   COALESCE(rt.is_high_tide_zone, 0) AS is_high_tide_zone,
                   COALESCE(rt.is_low_tide_zone, 0) AS is_low_tide_zone,
                   rt.minutes_from_high, rt.minutes_from_low
              FROM races r
              JOIN stadiums s ON s.stadium_number = r.stadium_number
              LEFT JOIN race_tides rt ON rt.race_id = r.race_id
             WHERE r.race_id = ?
            """,
            (race_id,),
        ).fetchone()

        entries = []
        racer_numbers: list[int] = []
        for row in current_rows:
            entries.append(
                {
                    "boat_number": int(row[0]),
                    "racer_number": int(row[1]) if row[1] is not None else None,
                    "racer_name": row[2],
                    "class_number": row[3],
                    "branch_number": row[4],
                    "assigned_motor_number": row[5],
                    "assigned_motor_top_2_percent": row[6],
                    "national_top_1_percent": row[7],
                    "local_top_1_percent": row[8],
                    "current_course": int(row[9]) if row[9] is not None else int(row[0]),
                    "exhibition_time": _safe_float(row[10]),
                    "start_timing_exhibition": _safe_float(row[11]),
                }
            )
            if row[1] is not None:
                racer_numbers.append(int(row[1]))
        if not racer_numbers:
            return None

        placeholders = ",".join("?" for _ in racer_numbers)
        cutoff_params = [target_date, target_date, target_race_no]

        course_rows = conn.execute(
            f"""
            SELECT e.racer_number, rr.course_number,
                   COUNT(*) AS n,
                   SUM(CASE WHEN rr.finishing_position = 1 THEN 1 ELSE 0 END) AS wins
              FROM races r
              JOIN race_entries e ON e.race_id = r.race_id
              JOIN race_results rr
                ON rr.race_id = e.race_id
               AND rr.boat_number = e.boat_number
             WHERE e.racer_number IN ({placeholders})
               AND (r.race_date < ? OR (r.race_date = ? AND r.race_number < ?))
               AND rr.course_number IS NOT NULL
               AND rr.finishing_position IS NOT NULL
             GROUP BY e.racer_number, rr.course_number
            """,
            (*racer_numbers, *cutoff_params),
        ).fetchall()
        course_stats = {
            (int(racer_no), int(course_no)): {
                "n": int(n or 0),
                "wins": int(wins or 0),
                "rate": (float(wins or 0) / float(n or 1) * 100.0) if n else None,
            }
            for racer_no, course_no, n, wins in course_rows
        }

        venue_rows = conn.execute(
            """
            SELECT rr.course_number,
                   COUNT(*) AS n,
                   SUM(CASE WHEN rr.finishing_position = 1 THEN 1 ELSE 0 END) AS wins
              FROM races r
              JOIN race_results rr ON rr.race_id = r.race_id
             WHERE r.stadium_number = ?
               AND (r.race_date < ? OR (r.race_date = ? AND r.race_number < ?))
               AND rr.course_number IS NOT NULL
               AND rr.finishing_position IS NOT NULL
             GROUP BY rr.course_number
            """,
            (info["stadium_number"], *cutoff_params),
        ).fetchall()
        venue_course_rates = {
            int(course_no): (float(wins or 0) / float(n or 1) * 100.0) if n else None
            for course_no, n, wins in venue_rows
        }

        kim_rows = conn.execute(
            f"""
            SELECT e.racer_number, NULLIF(rr.kimarite, '') AS kimarite, COUNT(*) AS n
              FROM races r
              JOIN race_entries e ON e.race_id = r.race_id
              JOIN race_results rr
                ON rr.race_id = e.race_id
               AND rr.boat_number = e.boat_number
             WHERE e.racer_number IN ({placeholders})
               AND (r.race_date < ? OR (r.race_date = ? AND r.race_number < ?))
               AND rr.finishing_position = 1
               AND rr.kimarite IS NOT NULL
               AND rr.kimarite <> ''
             GROUP BY e.racer_number, rr.kimarite
             ORDER BY e.racer_number, n DESC, rr.kimarite
            """,
            (*racer_numbers, *cutoff_params),
        ).fetchall()
        top_kimarite_by_racer: dict[int, str] = {}
        for racer_no, kimarite, _n in kim_rows:
            racer_no = int(racer_no)
            if racer_no not in top_kimarite_by_racer and kimarite:
                top_kimarite_by_racer[racer_no] = str(kimarite)

        pair_rows = conn.execute(
            f"""
            SELECT e1.racer_number, e2.racer_number,
                   rr1.finishing_position, rr2.finishing_position
              FROM races r
              JOIN race_entries e1 ON e1.race_id = r.race_id
              JOIN race_entries e2
                ON e2.race_id = r.race_id
               AND e2.racer_number <> e1.racer_number
              JOIN race_results rr1
                ON rr1.race_id = e1.race_id
               AND rr1.boat_number = e1.boat_number
              JOIN race_results rr2
                ON rr2.race_id = e2.race_id
               AND rr2.boat_number = e2.boat_number
             WHERE e1.racer_number IN ({placeholders})
               AND e2.racer_number IN ({placeholders})
               AND (r.race_date < ? OR (r.race_date = ? AND r.race_number < ?))
               AND rr1.finishing_position IS NOT NULL
               AND rr2.finishing_position IS NOT NULL
            """,
            (*racer_numbers, *racer_numbers, *cutoff_params),
        ).fetchall()
        pair_stats: dict[tuple[int, int], dict[str, int]] = {}
        for r1, r2, p1, p2 in pair_rows:
            key = (int(r1), int(r2))
            rec = pair_stats.setdefault(key, {"meetings": 0, "wins": 0})
            rec["meetings"] += 1
            if int(p1) < int(p2):
                rec["wins"] += 1

    tide_row = {}
    if stadium_row:
        tide_keys = [
            "water", "in_strength", "tide_effect", "tide_phase", "tide_height_cm",
            "tide_delta_60m_cm", "tide_range_cm", "is_high_tide_zone", "is_low_tide_zone",
            "minutes_from_high", "minutes_from_low",
        ]
        tide_row = dict(zip(tide_keys, stadium_row))

    pred_rank_map = {int(p.get("boat_number")): idx + 1 for idx, p in enumerate(preds)}
    built_entries = []
    motor_rows = []
    best_rise_boat = None
    best_rise_score = -1.0
    for entry in entries:
        racer_no = entry["racer_number"]
        current_course = entry["current_course"]
        course_stat = course_stats.get((racer_no, current_course), {})
        course_rate = course_stat.get("rate")
        venue_avg = venue_course_rates.get(current_course)
        diff_points = (course_rate - venue_avg) if (course_rate is not None and venue_avg is not None) else None
        type_label = _build_type_label(top_kimarite_by_racer.get(racer_no), current_course, diff_points)
        built_entries.append(
            {
                **entry,
                "class_label": _class_label(entry["class_number"]),
                "branch_label": _branch_label(entry["branch_number"]),
                "pred_rank": pred_rank_map.get(entry["boat_number"]),
                "course_win_rate": round(course_rate, 1) if course_rate is not None else None,
                "course_diff_points": round(diff_points, 1) if diff_points is not None else None,
                "course_diff_tone": _diff_tone(diff_points),
                "type_label": type_label,
                "top_kimarite": top_kimarite_by_racer.get(racer_no),
            }
        )
        payload = _motor_history_payload(race_id, entry["boat_number"], info=info)
        if not payload:
            continue
        profile = payload.get("profile") or {}
        live_signal = payload.get("live_signal") or {}
        trend_value = _safe_float(live_signal.get("trend_value")) or 0.0
        lift_value = _safe_float(live_signal.get("lift_value")) or 0.0
        dash_delta = trend_value * 0.60 + lift_value * 0.40
        carry_delta = trend_value * 0.45 + lift_value * 0.35
        stretch_delta = trend_value * 0.85 + lift_value * 0.15
        turn_delta = trend_value * 0.25 + lift_value * 0.75
        rise_score = _clamp(
            ((_safe_float(live_signal.get("trend_score")) or 50.0) * 0.55)
            + ((_safe_float(live_signal.get("lift_score")) or 50.0) * 0.45),
            0.0,
            100.0,
        )
        motor_eval = "中堅"
        if rise_score >= 72 and (_safe_float(profile.get("stretch_score")) or 0.0) >= (_safe_float(profile.get("dash_score")) or 0.0) + 6.0:
            motor_eval = "伸び特化"
        elif rise_score >= 68 and (_safe_float(profile.get("dash_score")) or 0.0) >= (_safe_float(profile.get("stretch_score")) or 0.0) + 6.0:
            motor_eval = "出足上位"
        elif (_safe_float(profile.get("condition_score")) or 0.0) >= 62.0:
            motor_eval = "中堅上位"
        elif (_safe_float(profile.get("condition_score")) or 50.0) <= 44.0:
            motor_eval = "劣勢"
        row = {
            "boat_number": entry["boat_number"],
            "racer_name": entry["racer_name"],
            "motor_number": entry["assigned_motor_number"],
            "motor_top_2_percent": entry["assigned_motor_top_2_percent"],
            "motor_eval": motor_eval,
            "motor_eval_tone": live_signal.get("trend_tone") or profile.get("condition_tone") or "flat",
            "dash_score": round(_safe_float(profile.get("dash_score")) or 50.0, 1),
            "carry_score": round(_clamp(((_safe_float(profile.get("dash_score")) or 50.0) * 0.55) + ((_safe_float(profile.get("stretch_score")) or 50.0) * 0.20) + ((_safe_float(live_signal.get("trend_score")) or 50.0) * 0.25), 0.0, 100.0), 1),
            "stretch_score": round(_safe_float(profile.get("stretch_score")) or 50.0, 1),
            "turn_score": round(_safe_float(profile.get("turn_score")) or 50.0, 1),
            "dash_delta": round(dash_delta, 2),
            "carry_delta": round(carry_delta, 2),
            "stretch_delta": round(stretch_delta, 2),
            "turn_delta": round(turn_delta, 2),
            "exhibition_time": _safe_float(entry["exhibition_time"]),
            "exhibition_st": _safe_float(entry["start_timing_exhibition"]),
            "rise_score": round(rise_score, 1),
            "rise_label": "上昇" if rise_score >= 60.0 else ("割引" if rise_score <= 44.0 else "標準"),
        }
        if row["rise_score"] > best_rise_score:
            best_rise_score = row["rise_score"]
            best_rise_boat = row["boat_number"]
        motor_rows.append(row)

    matrix_rows = []
    boat_order = [entry["boat_number"] for entry in built_entries]
    racer_by_boat = {entry["boat_number"]: entry["racer_number"] for entry in built_entries}
    for row_boat in boat_order:
        row_racer = racer_by_boat.get(row_boat)
        cells = []
        for col_boat in boat_order:
            if row_boat == col_boat:
                cells.append({"text": "—", "tone": "self", "meetings": 0})
                continue
            stat = pair_stats.get((row_racer, racer_by_boat.get(col_boat)), {"meetings": 0, "wins": 0})
            wins = int(stat.get("wins") or 0)
            meetings = int(stat.get("meetings") or 0)
            losses = max(meetings - wins, 0)
            cells.append(
                {
                    "text": f"{wins}-{losses}" if meetings else "0-0",
                    "tone": _pair_cell_tone(wins, losses),
                    "meetings": meetings,
                    "wins": wins,
                    "losses": losses,
                }
            )
        matrix_rows.append({"boat_number": row_boat, "cells": cells})

    difficulty = _water_difficulty_profile(
        info["stadium_number"],
        tide_row.get("water"),
        tide_row.get("tide_effect"),
        conditions,
        tide_row,
    )
    return {
        "entries": built_entries,
        "matrix_rows": matrix_rows,
        "water": {
            "water_label": _WATER_LABELS.get(tide_row.get("water"), "-"),
            "wind_label": f"{int((_safe_float(conditions.get('wind_speed')) or 0))}m" if conditions.get("wind_speed") is not None else "-",
            "wave_label": f"{int((_safe_float(conditions.get('wave_height')) or 0))}cm" if conditions.get("wave_height") is not None else "-",
            "weather_label": _WEATHER_LABELS.get(_safe_int(conditions.get("weather_number")), "-"),
            "tide_label": tide_row.get("tide_phase") or "-",
            "tide_height_cm": _safe_float(tide_row.get("tide_height_cm")),
            "difficulty_grade": difficulty["grade"],
            "difficulty_label": difficulty["label"],
            "difficulty_score": difficulty["score"],
        },
        "motor_rows": motor_rows,
        "best_rise_boat": best_rise_boat,
    }


# ============================================================
# Flask アプリ
# ============================================================

def _ensure_db_initialized() -> None:
    """
    DB ファイルとスキーマを必要に応じて初期化。Render など空ディスクに
    マウントされた環境で初回起動時に実行される。
    """
    import json
    from pathlib import Path

    db_path = Path(config.DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    schema_path = Path(__file__).resolve().parents[1] / "db" / "schema.sql"
    if not schema_path.exists():
        return

    with db_connect() as conn:
        # races テーブルがあるか試して、無ければ schema.sql を実行
        try:
            conn.execute("SELECT 1 FROM races LIMIT 1").fetchone()
        except Exception:
            logger.info("initializing DB at %s from schema.sql", db_path)
            with open(schema_path, "r", encoding="utf-8") as f:
                conn.executescript(f.read())

        # stadium マスタが空なら投入
        cnt = conn.execute("SELECT COUNT(*) FROM stadiums").fetchone()[0]
        if cnt == 0:
            stadium_path = config.MASTER_DIR / "stadiums.json"
            if stadium_path.exists():
                with open(stadium_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                rows = []
                for k, v in data.items():
                    if k.startswith("_"):
                        continue
                    rows.append((
                        int(k), v["name"], v["water"],
                        1 if v["is_night"] else 0,
                        v["in_strength"], v["tide_effect"],
                        1 if v.get("altitude_high") else 0,
                        v.get("notes"),
                    ))
                conn.executemany("""
                    INSERT OR REPLACE INTO stadiums
                        (stadium_number, name, water, is_night, in_strength,
                         tide_effect, altitude_high, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, rows)
                logger.info("loaded %d stadiums into DB", len(rows))


def create_app(version: str = config.DEFAULT_MODEL_VERSION) -> Flask:
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.config["SECRET_KEY"] = config.WEB_SESSION_SECRET
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=12)

    # ===== セキュリティ設定: Cookie 保護 =====
    # 本番 (RENDER) では HTTPS 強制、開発時は HTTP 許可
    is_production = bool(os.environ.get("RENDER"))
    app.config["SESSION_COOKIE_SECURE"] = is_production       # HTTPS のみ送信
    app.config["SESSION_COOKIE_HTTPONLY"] = True              # JS からアクセス不可
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"             # CSRF 緩和
    app.config["SESSION_COOKIE_NAME"] = "boatrace_session"    # デフォルト名を変更
    app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024         # POST body 1MB 上限

    # WEB_SESSION_SECRET が本番でデフォルトのままだと警告
    # noqa: S105 は「デフォルト値リテラルとの比較」であり実パスワードではない
    _DEFAULT_SECRET = "dev-only-do-not-use-in-prod"  # noqa: S105
    _DEFAULT_MEMBER = "dev-member"  # noqa: S105
    if is_production and config.WEB_SESSION_SECRET == _DEFAULT_SECRET:
        logger.critical(
            "SECURITY: WEB_SESSION_SECRET is using DEFAULT value in production. "
            "Set BOATRACE_WEB_SECRET environment variable to a long random string."
        )
    if is_production and config.WEB_MEMBER_PASSWORD == _DEFAULT_MEMBER:
        logger.critical(
            "SECURITY: BOATRACE_MEMBER_PASSWORD is using DEFAULT value in production. "
            "Set this env var to a strong password (16+ chars)."
        )

    # ===== gzip 圧縮 (速度改善: HTML を 70-80% 圧縮) =====
    @app.after_request
    def compress_response(response):
        import gzip
        # gzip 対応クライアントだけ圧縮
        accept = request.headers.get("Accept-Encoding", "") if request else ""
        if "gzip" not in accept.lower():
            return response
        # ステータス成功 + 一定サイズ以上 + テキスト系
        if response.status_code < 200 or response.status_code >= 300:
            return response
        if response.direct_passthrough:
            return response
        ctype = response.content_type or ""
        if not any(t in ctype for t in ("text/", "application/json",
                                          "application/javascript",
                                          "application/xml")):
            return response
        # 既に圧縮済 or 小さすぎ
        if response.headers.get("Content-Encoding"):
            return response
        data = response.get_data()
        if len(data) < 500:
            return response
        compressed = gzip.compress(data, compresslevel=6)
        response.set_data(compressed)
        response.headers["Content-Encoding"] = "gzip"
        response.headers["Content-Length"] = str(len(compressed))
        # CDN/プロキシは Accept-Encoding ごとにキャッシュ
        vary = response.headers.get("Vary", "")
        if "Accept-Encoding" not in vary:
            response.headers["Vary"] = (vary + ", Accept-Encoding").lstrip(", ")
        return response

    # ===== セキュリティ HTTP ヘッダ =====
    @app.after_request
    def add_security_headers(response):
        # XSS / clickjacking / sniffing 対策
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
            "magnetometer=(), microphone=(), payment=(), usb=()"
        )
        # 本番のみ HSTS (HTTPS 強制)
        if is_production:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        # 簡易 CSP (テンプレ内 inline script を許可)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "form-action 'self'; "
            "base-uri 'self';"
        )
        # ログインや会員ページは絶対キャッシュさせない (機密情報漏洩防止)
        path = request.path if request else ""
        if path in ("/login", "/logout") or "/api/" in path:
            # API は短時間 private キャッシュ (ブラウザのみ、CDN 経由しない)
            if "/api/" in path and not path.startswith("/login"):
                # 過去日 API は長く、今日のは短く
                try:
                    req_date = request.args.get("date", "")
                    if req_date and req_date < date.today().isoformat():
                        response.headers["Cache-Control"] = "private, max-age=600"
                    else:
                        response.headers["Cache-Control"] = "private, max-age=60"
                except Exception:
                    response.headers["Cache-Control"] = "private, max-age=60"
            else:
                response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
                response.headers["Pragma"] = "no-cache"
                response.headers["Expires"] = "0"
        elif path.startswith("/static/"):
            # 静的ファイル: バージョン query (?v=xxx) 付きならキャッシュ可、無ければ再検証
            # immutable は外して、HTML 側で ?v= 付与によるキャッシュ破壊を有効化
            if request.args.get("v"):
                response.headers["Cache-Control"] = "public, max-age=86400, immutable"
            else:
                response.headers["Cache-Control"] = "public, max-age=300, must-revalidate"
        elif path == "/races" or path.startswith("/race/"):
            # HTML ページ: 過去日は長く、今日は短く
            try:
                req_date = request.args.get("date", "")
                # /race/<id> は race_id から日付抽出 (path の数字 8 桁)
                if not req_date and path.startswith("/race/"):
                    rid = path.split("/")[-1]
                    if len(rid) >= 8 and rid[:8].isdigit():
                        req_date = f"{rid[:4]}-{rid[4:6]}-{rid[6:8]}"
                if req_date and req_date < date.today().isoformat():
                    # 過去日 HTML: 5 分キャッシュ (L4 マーク更新を反映するため)
                    response.headers["Cache-Control"] = "public, max-age=300, must-revalidate"
                else:
                    # 今日 HTML: 30 秒のみ
                    response.headers["Cache-Control"] = "public, max-age=30, must-revalidate"
            except Exception:
                response.headers["Cache-Control"] = "public, max-age=60"
        return response

    # robots.txt と sitemap.xml は最低限のレスポンスを返す
    # (ZAP の robots.txt パッシブスキャンで CSP/HSTS が無いと言われないように)
    @app.route("/robots.txt")
    def robots_txt():
        return ("User-agent: *\nDisallow: /login\nDisallow: /api/\n",
                200, {"Content-Type": "text/plain"})

    app.jinja_env.auto_reload = True
    register_auth_routes(app)
    # メール購読 UI
    try:
        from src.web.subscriber_views import register_subscriber_routes
        register_subscriber_routes(app)
    except Exception as e:
        logger.warning("subscriber routes not registered: %s", e)

    # グローバルエラーハンドラ (500 を親切メッセージへ)
    @app.errorhandler(500)
    def handle_500(err):
        logger.exception("500 error: %s", err)
        err_str = str(err.original_exception) if hasattr(err, "original_exception") and err.original_exception else str(err)
        if "No space left on device" in err_str:
            return render_template(
                "race.html",
                info={"race_id": "", "race_date": "", "stadium_number": 0,
                      "race_number": 0, "stadium_name": "Error"},
                error="サーバー容量制限のためご利用いただけません (Supabase Free 制約)。少し時間をおいてから再度お試しください。",
                preds=[], racer_names={}, trifecta_pw=[], trifecta_unified=[],
                conditions={},
            ), 500
        if "connection" in err_str.lower() or "timeout" in err_str.lower():
            return render_template(
                "race.html",
                info={"race_id": "", "race_date": "", "stadium_number": 0,
                      "race_number": 0, "stadium_name": "Error"},
                error="DB 接続エラー。30秒後に再度アクセスしてください。",
                preds=[], racer_names={}, trifecta_pw=[], trifecta_unified=[],
                conditions={},
            ), 500
        return render_template(
            "race.html",
            info={"race_id": "", "race_date": "", "stadium_number": 0,
                  "race_number": 0, "stadium_name": "Error"},
            error=f"サーバーエラー: {err_str[:200]}",
            preds=[], racer_names={}, trifecta_pw=[], trifecta_unified=[],
            conditions={},
        ), 500

    # テンプレートから is_member() を呼べるように
    app.jinja_env.globals["is_member"] = is_member

    # 静的ファイル cache busting 用バージョン
    # CSS/JS が変更されたら自動的に新規取得されるよう、
    # ファイル更新時刻ベースのハッシュを付ける
    import hashlib
    from pathlib import Path
    static_files = [
        Path(__file__).parent / "static" / "style.css",
    ]
    h = hashlib.md5()
    for f in static_files:
        try:
            h.update(str(int(f.stat().st_mtime)).encode())
        except Exception:
            h.update(b"0")
    app.jinja_env.globals["static_version"] = h.hexdigest()[:8]

    # Jinja2 カスタムフィルタ: カンマ区切り符号付き整数 (Python %-format は ',' 非対応)
    def _signed_comma(value):
        try:
            return f"{int(value):+,d}"
        except (TypeError, ValueError):
            return "-"
    def _comma(value):
        try:
            return f"{int(value):,d}"
        except (TypeError, ValueError):
            return "-"
    app.jinja_env.filters["signed_comma"] = _signed_comma
    app.jinja_env.filters["comma"] = _comma

    # backlog item 6: ERROR レベル以上の logger をメール通知に投げる
    # 環境変数 BOATRACE_ERROR_NOTIFY_TO が設定されていれば自動有効化
    try:
        from src.notifications.error_handler import install_error_notifier
        installed_app = install_error_notifier(app.logger)
        installed_root = install_error_notifier(logging.getLogger())
        if installed_app or installed_root:
            logger.info("Error email notifier installed (BOATRACE_ERROR_NOTIFY_TO set)")
    except Exception as e:
        logger.warning("install_error_notifier failed: %s", e)

    # 全テンプレートに today_iso を自動注入 (BOATRACE WEB リンク等で使用)
    @app.context_processor
    def _inject_today():
        return {"today_iso_global": date.today().isoformat()}

    # データ品質警告バナー用 (backlog item 3)
    @app.context_processor
    def _inject_system_status():
        """全テンプレートで {{ system_warnings }} が使えるように。
        今日と昨日の system_status から warning/error を集める (TTL 5 分、内部キャッシュ)。
        """
        import time as _t
        cache_key = "_system_status_cache"
        cache_ttl = 300  # 5 分
        cache = getattr(app, cache_key, None)
        now_ts = _t.time()
        if cache and (now_ts - cache.get("ts", 0)) < cache_ttl:
            return {"system_warnings": cache["warnings"]}
        warnings_list: list[dict] = []
        try:
            today_iso = date.today().isoformat()
            with db_connect() as conn:
                cur = conn.execute(
                    "SELECT check_name, status, message, checked_at "
                    "FROM system_status "
                    "WHERE check_date = ? AND status IN ('warning', 'error') "
                    "ORDER BY status DESC, check_name",
                    (today_iso,)
                )
                for cn, st, msg, at in cur.fetchall():
                    warnings_list.append({
                        "check_name": cn, "status": st,
                        "message": msg, "checked_at": at,
                    })
        except Exception as e:
            # system_status テーブル未作成等は無視 (banner 出さないだけ)
            logger.debug("system_status lookup skipped: %s", e)
        setattr(app, cache_key, {"ts": now_ts, "warnings": warnings_list})
        return {"system_warnings": warnings_list}

    # DB 初期化 (空ディスクへの初回デプロイで必要)
    try:
        _ensure_db_initialized()
    except Exception as e:
        logger.warning("DB init skipped: %s", e)

    predictor = Predictor(version=version)

    # 起動時にモデルを先読み (失敗してもアプリは動かす)
    # Render 等の本番環境ではキャッシュ専用モードのためモデルロードを skip
    # → LightGBM/cascade/per_winner で 200-300MB 節約 (Render Free 512MB 対策)
    if os.environ.get("RENDER") or os.environ.get("DISABLE_LIVE_PREDICT"):
        logger.info(
            "production mode: skipping predictor.load() to conserve memory. "
            "predictions table must be populated via scripts/cache_predictions.py"
        )
    else:
        try:
            predictor.load()
        except FileNotFoundError as e:
            logger.warning("model not loaded: %s. UI will show error until model is trained.", e)

        # backlog item 3: /api/ev-races 廃止に伴い、起動後 bg-warm も削除済
        # (warm_trifecta_cache は per-race の race_detail で必要時のみ実行)

    @app.route("/healthz")
    def healthz():
        """外部死活監視 (UptimeRobot / Render health check) 用エンドポイント。

        判定 (HTTP ステータスコード):
          200 OK   : サービス起動可能 (DB ping OK)
          503      : DB 接続失敗のみ (= サービス完全停止扱い)

        データ品質 (system_status の error/warning) は JSON ボディに
        含めるが HTTP コードには影響させない。理由:
          - Render の health check は 200 でないとデプロイ失敗
          - データ品質 error はサービス自体は動いている (運用問題)

        UptimeRobot 側は別の判定手段:
          (a) HTTP 200 のみ判定: サービス生存だけ気にする (推奨)
          (b) Keyword Monitoring で "status":"ok" 必須にする (シビア)
        """
        status_info = {
            "status": "ok",
            "model_loaded": predictor.artifact is not None,
            "checks": {},
        }
        http_status = 200
        # DB ping (サービス完全停止と区別)

        try:
            with db_connect() as conn:
                conn.execute("SELECT 1").fetchone()
            status_info["checks"]["db"] = "ok"
        except Exception as e:
            status_info["checks"]["db"] = f"error: {e}"
            status_info["status"] = "error"
            http_status = 503
            return status_info, http_status

        # データ品質 (今日の system_status 集計) は 200 のまま JSON のみ
        try:
            today_iso = date.today().isoformat()
            with db_connect() as conn:
                cur = conn.execute(
                    "SELECT status, COUNT(*) FROM system_status "
                    "WHERE check_date = ? GROUP BY status",
                    (today_iso,),
                )
                counts = {row[0]: row[1] for row in cur.fetchall()}
            n_err = counts.get("error", 0)
            n_warn = counts.get("warning", 0)
            status_info["checks"]["data_quality_errors"] = n_err
            status_info["checks"]["data_quality_warnings"] = n_warn
            if n_err > 0:
                status_info["status"] = "degraded"
            elif n_warn > 0:
                status_info["status"] = "warning"
        except Exception as e:
            status_info["checks"]["data_quality"] = f"unknown: {e}"
        return status_info, http_status

    @app.route("/")
    @login_required
    def index():
        target = request.args.get("date") or date.today().isoformat()
        resp = redirect(url_for("races", date=target))
        # ブラウザがリダイレクト先を日付ごとキャッシュしないように no-store を明示
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        return resp

    @app.route("/races")
    @login_required
    @cached(ttl=120, past_ttl=3600)  # 今日120秒/過去日1時間キャッシュ
    # backlog item 11: 旧 60s → 120s。レース予定の動的要素は results_count のみで
    # poll_results が 5分間隔なので 120s 化しても表示遅延ほぼ無し。Cloudflare
    # CDN ヒット率が大幅向上し、ユーザ体感速度が改善する。
    def races():
        target_date = request.args.get("date") or date.today().isoformat()
        races_list = _races_for_date(target_date)
        if not races_list:
            today_iso = date.today().isoformat()
            should_self_heal = (
                os.environ.get("RENDER")
                and target_date == today_iso
                and request.headers.get("X-Purpose") != "prefetch"
            )
            if should_self_heal:
                try:
                    summary = openapi.collect_all(date.fromisoformat(target_date))
                    logger.warning("self-heal collect_all for %s: %s", target_date, summary)
                    races_list = _races_for_date(target_date)
                except Exception as exc:
                    logger.exception("self-heal collect_all failed for %s: %s", target_date, exc)
            if not races_list:
                return render_template(
                    "index.html",
                    target_date=target_date,
                    today_iso=today_iso,
                    stadium_groups=[],
                    empty=True,
                )

        # 会場別にグループ化
        stadium_groups: dict[int, dict] = {}
        for r in races_list:
            sn = r["stadium_number"]
            if sn not in stadium_groups:
                stadium_groups[sn] = {
                    "stadium_number": sn,
                    "stadium_name": r["stadium_name"],
                    "races": [],
                }
            stadium_groups[sn]["races"].append(r)

        resp = make_response(render_template(
            "index.html",
            target_date=target_date,
            today_iso=date.today().isoformat(),
            stadium_groups=sorted(stadium_groups.values(),
                                  key=lambda g: g["stadium_number"]),
            empty=False,
        ))
        # backlog item 11: market-signals を HTTP/2 preload で先取り
        # ブラウザは HTML パース前に /api/market-signals に並列リクエストを
        # 飛ばすので、JS が呼ぶ頃には返答がキャッシュ済 → 体感速度向上
        resp.headers["Link"] = (
            f'</api/market-signals?date={target_date}>; rel=preload; as=fetch; crossorigin'
        )
        return resp

    @app.route("/race/<race_id>")
    @login_required
    @cached(ttl=180, past_ttl=7200)
    def race_detail(race_id: str):
        # 過去レース (race_date が今日より前) は 1時間キャッシュ、当日は 60秒
        # cached デコレータは request.args["date"] を見るが、/race/<id> には
        # クエリ無しなので race_id から日付を取り出して past_ttl を有効化する
        # (cached 内部の effective_ttl をオーバーライドできないため、副作用なし)
        info = _race_basic_info(race_id)
        if not info:
            abort(404)

        today_iso = date.today().isoformat()
        page_cache_key = f"race_detail:{race_id}"
        cached_html = _read_page_html_cache(
            page_cache_key,
            180 if info["race_date"] >= today_iso else 21600,
        )
        if cached_html:
            return cached_html

        try:
            preds = _race_predictions(predictor, race_id)
            _attach_kimarite_skill_tags(race_id, preds)
        except Exception as e:
            logger.exception("prediction failed: %s", race_id)
            # エラーをユーザー向けメッセージに変換
            err_str = str(e)
            if "No space left on device" in err_str:
                user_msg = "サーバー容量制限により予測計算ができません。管理者にお知らせください。(Supabase Free tmp 制限)"
            elif "no such table" in err_str or "relation" in err_str and "does not exist" in err_str:
                user_msg = "予測データが未投入のためご利用いただけません。管理者がデータ投入を完了するまでお待ちください。"
            elif "artifact not found" in err_str:
                user_msg = "モデル未配置のため予測できません。"
            elif "timeout" in err_str.lower() or "connection" in err_str.lower():
                user_msg = "データベース接続エラー。少し時間をおいてから再度アクセスしてください。"
            else:
                user_msg = f"予測エラー: {err_str[:200]}"
            html = render_template(
                "race.html",
                info=info,
                preds=[],
                error=user_msg,
                racer_names={},
                trifecta_pw=[],
                trifecta_unified=[],
                conditions={},
            )
            _write_page_html_cache(page_cache_key, html)
            return html

        names = _racer_names(race_id)
        target_date = info["race_date"]
        conditions = _race_current_conditions(race_id)
        actual_result = _race_actual_result(race_id)

        # 戦略タグ判定
        sn = info["stadium_number"]
        venue_warning = None
        if sn in _LOSING_VENUES:
            venue_warning = {
                "level": "danger",
                "venue": _LOSING_VENUES[sn],
                "msg": "Bootstrap CI で確実マイナスと検証済の会場。単勝固定買い非推奨",
            }
        elif sn in _QUESTIONABLE_VENUES:
            venue_warning = {
                "level": "caution",
                "venue": _QUESTIONABLE_VENUES[sn],
                "msg": "ROI 弱マイナス会場 (CI 一部正側、慎重)",
            }

        # 鉄板狙い判定 (1号艇 prob 70%+ かつ非マイナス会場)
        sweet_spot = False
        if preds and preds[0].get("boat_number") == 1 and preds[0].get("prob_first", 0) >= 0.70:
            if sn not in _LOSING_VENUES:
                sweet_spot = True

        # ニッチ大穴シグナル検出
        niche_signals = _detect_niche_signals(preds, conditions)

        # 市場非効率レース検出 (三連単1番人気の払戻に基づく +EV ゾーン)
        market_signal = _detect_market_inefficiency(race_id, preds, info=info)
        # レース詳細の選手間相関マップ/相関集計は負荷が高く、
        # 現行運用では費用対効果が低いため停止する。
        compat_analysis = None

        # 三連単予測 (本番 Render では heavy compute をスキップして OOM/timeout 防止)
        tri_pw = []
        tri_uni = []
        if not (os.environ.get("RENDER") or os.environ.get("DISABLE_LIVE_PREDICT")):
            try:
                pw = predictor.predict_trifecta(target_date, race_id, mode="per_winner")
                if pw:
                    tri_pw = pw[:10]  # top 10
            except Exception as e:
                logger.warning("per-winner trifecta failed for %s: %s", race_id, e)
            try:
                uni = predictor.predict_trifecta(target_date, race_id, mode="unified")
                if uni:
                    tri_uni = uni[:10]
            except Exception as e:
                logger.warning("unified trifecta failed for %s: %s", race_id, e)

        html = render_template(
            "race.html",
            info=info,
            preds=preds,
            racer_names=names,
            trifecta_pw=tri_pw,
            trifecta_unified=tri_uni,
            conditions=conditions,
            venue_warning=venue_warning,
            sweet_spot=sweet_spot,
            actual_result=actual_result,
            niche_signals=niche_signals,
            market_signal=market_signal,
            compat_analysis=compat_analysis,
            error=None,
        )
        _write_page_html_cache(page_cache_key, html)
        return html

    @app.route("/api/race/<race_id>/value-bets")
    @member_only_api
    # 2026-05-30: キャッシュ追加 (旧実装はキャッシュなしで毎回 DB 計算)
    #   ttl=60: 当日 race のリアルタイム性 (オッズ snapshot 反映)
    #   past_ttl=3600: 過去日 race は確定済なので 1 時間キャッシュ
    @cached(ttl=60, past_ttl=3600)
    def race_value_bets(race_id: str):
        info = _race_basic_info(race_id)
        if not info:
            return jsonify({"error": "not found"}), 404
        snapshot = request.args.get("snapshot", "T-5min")
        profile = request.args.get("profile", "")
        if profile == "practical":
            thr = float(request.args.get("ev", config.PRACTICAL_TRIFECTA_EV_THRESHOLD))
            min_prob = float(request.args.get("min_prob", config.PRACTICAL_TRIFECTA_MIN_PROB))
            min_odds = float(request.args.get("min_odds", config.PRACTICAL_TRIFECTA_MIN_ODDS))
            max_odds = float(request.args.get("max_odds", config.PRACTICAL_TRIFECTA_MAX_ODDS))
            max_results = int(request.args.get("max_results", config.PRACTICAL_TRIFECTA_MAX_BETS_PER_RACE))
        else:
            thr = float(request.args.get("ev", "0.0"))
            min_prob = float(request.args.get("min_prob", "0.005"))
            min_odds = float(request.args.get("min_odds", "1.0"))
            max_odds = float(request.args.get("max_odds", "500.0"))
            max_results_raw = request.args.get("max_results")
            max_results = int(max_results_raw) if max_results_raw else None
        try:
            r = predictor.find_value_bets_for_race(
                info["race_date"], race_id,
                snapshot_label=snapshot,
                ev_threshold=thr,
                min_prob=min_prob,
                min_odds=min_odds,
                max_odds=max_odds,
                max_results=max_results,
            )
        except Exception as e:
            logger.exception("value bet failed: %s", race_id)
            return jsonify({"error": str(e)}), 500
        if r is None:
            return jsonify({"race_id": race_id, "snapshot_label": snapshot,
                            "n_value_bets": 0, "value_bets": [],
                            "best_ev": None, "best_combo": None,
                            "warning": "no model prediction or no odds snapshot"})
        return jsonify(r)

    # backlog item 3: /api/ev-races は EV+ 自動判定機能と一緒に廃止
    # (本画面では呼ばれず、L4 戦略マーク = market-signals に一本化)

    @app.route("/api/race/<race_id>")
    @member_only_api
    @cached(ttl=300)  # 5分キャッシュ
    def race_api(race_id: str):
        info = _race_basic_info(race_id)
        if not info:
            return jsonify({"error": "not found"}), 404
        try:
            preds = _race_predictions(predictor, race_id)
            _attach_kimarite_skill_tags(race_id, preds)
        except Exception as e:
            return jsonify({"error": str(e)}), 500
        return jsonify({
            "info": info,
            "predictions": preds,
            "conditions": _race_current_conditions(race_id),
        })

    @app.route("/api/race/<race_id>/motor-history/<int:boat_number>")
    @member_only_api
    @cached(ttl=1800, past_ttl=86400)
    def race_motor_history(race_id: str, boat_number: int):
        if boat_number < 1 or boat_number > 6:
            return jsonify({"error": "invalid boat_number"}), 400
        info = _race_basic_info(race_id)
        if not info:
            return jsonify({"error": "entry not found"}), 404
        today_iso = date.today().isoformat()
        cache_key = f"motor_history:{race_id}:{boat_number}"
        cached_payload = _read_json_cache(
            cache_key,
            1800 if info["race_date"] >= today_iso else 86400,
        )
        if cached_payload is not None:
            return jsonify(cached_payload)
        payload = _motor_history_payload(race_id, boat_number, info=info)
        if payload is None:
            return jsonify({"error": "entry not found"}), 404
        _write_json_cache(cache_key, payload)
        return jsonify(payload)


    def _safe_float(value):
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _safe_int(value):
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _build_tsu_suminoe_history_seed(conn, cutoff_date_iso: str) -> dict:
        seed = {
            "racer_global": {},
            "racer_venue": {},
            "venue123": {9: [0, 0], 12: [0, 0]},
        }
        try:
            cur = conn.execute(
                """
                SELECT r.stadium_number,
                       e.boat_number,
                       e.racer_number,
                       rr.finishing_position
                  FROM races r
                  JOIN race_entries e
                    ON e.race_id = r.race_id
                   AND e.boat_number IN (2, 3)
                  JOIN race_results rr
                    ON rr.race_id = e.race_id
                   AND rr.boat_number = e.boat_number
                 WHERE r.race_date < ?
                   AND r.stadium_number IN (9, 12)
                   AND rr.finishing_position IS NOT NULL
                """,
                (cutoff_date_iso,),
            )
            for stadium, boat_no, racer_no, finish_pos in cur.fetchall():
                if racer_no is None or boat_no not in (2, 3):
                    continue
                hit_pos = 2 if boat_no == 2 else 3
                gkey = (boat_no, racer_no)
                vkey = (stadium, boat_no, racer_no)
                grec = seed["racer_global"].setdefault(gkey, [0, 0])
                vrec = seed["racer_venue"].setdefault(vkey, [0, 0])
                grec[0] += 1
                vrec[0] += 1
                if finish_pos == hit_pos:
                    grec[1] += 1
                    vrec[1] += 1
        except Exception:
            pass
        try:
            cur = conn.execute(
                """
                SELECT r.stadium_number,
                       res1.boat_number,
                       res2.boat_number,
                       res3.boat_number
                  FROM races r
                  JOIN race_results res1
                    ON res1.race_id = r.race_id
                   AND res1.finishing_position = 1
                  JOIN race_results res2
                    ON res2.race_id = r.race_id
                   AND res2.finishing_position = 2
                  JOIN race_results res3
                    ON res3.race_id = r.race_id
                   AND res3.finishing_position = 3
                 WHERE r.race_date < ?
                   AND r.stadium_number IN (9, 12)
                """,
                (cutoff_date_iso,),
            )
            for stadium, w1, w2, w3 in cur.fetchall():
                rec = seed["venue123"].setdefault(stadium, [0, 0])
                rec[0] += 1
                if (w1, w2, w3) == (1, 2, 3):
                    rec[1] += 1
        except Exception:
            pass
        return seed

    def _build_tsu_suminoe_ctx(stadium: int, info: dict | None, seed: dict | None) -> dict | None:
        if stadium not in (9, 12) or not info or not seed:
            return None
        boat2_racer = info.get("boat2_racer")
        boat3_racer = info.get("boat3_racer")
        if not boat2_racer or not boat3_racer:
            return None

        rg = seed.get("racer_global", {})
        rv = seed.get("racer_venue", {})
        v123 = seed.get("venue123", {})

        b2g_n, b2g_h = rg.get((2, boat2_racer), [0, 0])
        b2v_n, b2v_h = rv.get((stadium, 2, boat2_racer), [0, 0])
        b3g_n, b3g_h = rg.get((3, boat3_racer), [0, 0])
        b3v_n, b3v_h = rv.get((stadium, 3, boat3_racer), [0, 0])
        v_n, v_h = v123.get(stadium, [0, 0])

        return {
            "stadium": stadium,
            "race_number": info.get("race_number"),
            "class": info.get("class"),
            "natl_1": info.get("natl_1"),
            "local_1": info.get("local_1"),
            "avg_st": info.get("avg_st"),
            "boat2_top2": info.get("boat2_top2"),
            "boat2_motor_top2": info.get("boat2_motor_top2"),
            "boat3_top2": info.get("boat3_top2"),
            "boat3_motor_top2": info.get("boat3_motor_top2"),
            "boat1_ex_st": info.get("boat1_ex_st", info.get("ex_st")),
            "boat1_exhibition_time": info.get("boat1_exhibition_time"),
            "boat2_exhibition_time": info.get("boat2_exhibition_time"),
            "boat3_exhibition_time": info.get("boat3_exhibition_time"),
            "boat4_exhibition_time": info.get("boat4_exhibition_time"),
            "boat5_exhibition_time": info.get("boat5_exhibition_time"),
            "boat6_exhibition_time": info.get("boat6_exhibition_time"),
            "boat2_global_n": b2g_n,
            "boat2_global_rate": (b2g_h / b2g_n * 100.0) if b2g_n else None,
            "boat2_venue_n": b2v_n,
            "boat2_venue_rate": (b2v_h / b2v_n * 100.0) if b2v_n else None,
            "boat3_global_n": b3g_n,
            "boat3_global_rate": (b3g_h / b3g_n * 100.0) if b3g_n else None,
            "boat3_venue_n": b3v_n,
            "boat3_venue_rate": (b3v_h / b3v_n * 100.0) if b3v_n else None,
            "venue123_n": v_n,
            "venue123_rate": (v_h / v_n * 100.0) if v_n else None,
        }

    def _evaluate_tsu_suminoe_123_signal(ctx: dict | None):
        if not ctx:
            return None
        stadium = _safe_int(ctx.get("stadium"))
        race_no = _safe_int(ctx.get("race_number"))
        cls = _safe_int(ctx.get("class"))
        natl_1 = _safe_float(ctx.get("natl_1"))
        local_1 = _safe_float(ctx.get("local_1"))
        avg_st = _safe_float(ctx.get("avg_st"))
        boat2_top2 = _safe_float(ctx.get("boat2_top2"))
        boat2_motor = _safe_float(ctx.get("boat2_motor_top2"))
        boat3_top2 = _safe_float(ctx.get("boat3_top2"))
        boat3_motor = _safe_float(ctx.get("boat3_motor_top2"))
        boat1_ex_st = _safe_float(ctx.get("boat1_ex_st", ctx.get("ex_st")))
        boat1_ex_time = _safe_float(ctx.get("boat1_exhibition_time"))
        ex_times = {
            boat_no: _safe_float(ctx.get(f"boat{boat_no}_exhibition_time"))
            for boat_no in range(1, 7)
        }
        b2g_n = _safe_int(ctx.get("boat2_global_n")) or 0
        b2g_rate = _safe_float(ctx.get("boat2_global_rate"))
        b2v_n = _safe_int(ctx.get("boat2_venue_n")) or 0
        b2v_rate = _safe_float(ctx.get("boat2_venue_rate"))
        b3g_n = _safe_int(ctx.get("boat3_global_n")) or 0
        b3g_rate = _safe_float(ctx.get("boat3_global_rate"))
        b3v_n = _safe_int(ctx.get("boat3_venue_n")) or 0
        b3v_rate = _safe_float(ctx.get("boat3_venue_rate"))
        v123_rate = _safe_float(ctx.get("venue123_rate"))

        if stadium not in (9, 12) or cls != 1:
            return None
        if natl_1 is None or natl_1 < 7.0:
            return None
        if avg_st is None or avg_st > 0.16:
            return None
        if boat1_ex_st is None or boat1_ex_st > 0.15:
            return None
        valid = [v for v in ex_times.values() if v is not None and v > 0]
        if boat1_ex_time is None or boat1_ex_time <= 0 or not valid:
            return None

        def _ex_rank(boat_no: int) -> int | None:
            target = ex_times.get(boat_no)
            if target is None or target <= 0:
                return None
            return 1 + sum(1 for v in valid if v < target)

        if (_ex_rank(1) or 99) > 2 or (_ex_rank(2) or 99) > 2:
            return None

        if stadium == 9:
            if boat2_motor is None or boat2_motor > 35.0:
                return None
            if boat2_top2 is None or boat2_top2 < 35.0:
                return None
            return {
                "level": "tsu_123_tri",
                "label": "津 1-2-3",
                "bet": "3連単 1-2-3",
                "rank": "trifecta_niche",
                "rank_label": "3連単ニッチ",
                "recovery": 305.0,
                "n": 20,
                "hit_rate": 30.0,
                "tetsuban_score": 6,
                "tetsuban_label": "津123",
                "is_trifecta_niche": True,
                "trifecta_niche_name": "津123",
                "trifecta_niche_tag": "A1 + 1号艇全国1着率>=7 + 展示1/2<=2位 + 展示ST<=0.15 + 2号艇モーター<=35",
                "trifecta_niche_hit_rate": 30.0,
                "trifecta_niche_recovery": 305.0,
                "watch_strategy_bets": ["3連単 1-2-3"],
            }

        if local_1 is None or local_1 < 6.0:
            return None
        if boat2_motor is None or boat2_motor > 35.0:
            return None
        if boat2_top2 is None or boat2_top2 < 35.0:
            return None
        return {
            "level": "suminoe_123_tri",
            "label": "住之江 1-2-3",
            "bet": "3連単 1-2-3",
            "rank": "trifecta_niche",
            "rank_label": "3連単ニッチ",
            "recovery": 212.9,
            "n": 24,
            "hit_rate": 29.2,
            "tetsuban_score": 7,
            "tetsuban_label": "住之江123",
            "is_trifecta_niche": True,
            "trifecta_niche_name": "住之江123",
            "trifecta_niche_tag": "A1 + 当地1着率>=6 + 展示1/2<=2位 + 展示ST<=0.15 + 2号艇モーター<=35",
            "trifecta_niche_hit_rate": 29.2,
            "trifecta_niche_recovery": 212.9,
            "watch_strategy_bets": ["3連単 1-2-3"],
        }

    def _evaluate_shimonoseki_123_signal(ctx: dict | None):
        if not ctx:
            return None

        def _to_float(value):
            try:
                return float(value) if value is not None else None
            except (TypeError, ValueError):
                return None

        def _to_int(value):
            try:
                return int(value) if value is not None else None
            except (TypeError, ValueError):
                return None

        stadium = _to_int(ctx.get("stadium"))
        race_no = _to_int(ctx.get("race_number"))
        cls = _to_int(ctx.get("class"))
        natl_1 = _to_float(ctx.get("natl_1"))
        local_1 = _to_float(ctx.get("local_1"))
        boat2_motor = _to_float(ctx.get("boat2_motor_top2"))
        boat1_ex_st = _to_float(ctx.get("boat1_ex_st", ctx.get("ex_st")))
        ex_times = {
            boat_no: _to_float(ctx.get(f"boat{boat_no}_exhibition_time"))
            for boat_no in range(1, 7)
        }
        if stadium != 19 or cls != 1 or race_no is None or not (10 <= race_no <= 12):
            return None
        if natl_1 is None or natl_1 < 7.0 or local_1 is None or local_1 < 6.0:
            return None
        if boat2_motor is None or boat2_motor > 40.0:
            return None
        if boat1_ex_st is None or boat1_ex_st > 0.15:
            return None
        valid = [v for v in ex_times.values() if v is not None and v > 0]
        if not valid:
            return None

        def _ex_rank(boat_no: int) -> int | None:
            target = ex_times.get(boat_no)
            if target is None or target <= 0:
                return None
            return 1 + sum(1 for v in valid if v < target)

        if (_ex_rank(1) or 99) > 2 or (_ex_rank(2) or 99) > 2:
            return None
        return {
            "level": "shimonoseki_123_tri",
            "label": "下関 1-2-3",
            "bet": "3連単 1-2-3",
            "rank": "trifecta_niche",
            "rank_label": "3連単ニッチ",
            "recovery": 157.3,
            "n": 22,
            "hit_rate": 31.8,
            "tetsuban_score": 6,
            "tetsuban_label": "下関123",
            "is_trifecta_niche": True,
            "trifecta_niche_name": "下関123",
            "trifecta_niche_tag": "下関10-12R + A1 + 展示1/2<=2位 + 展示ST<=0.15 + 2号艇モーター<=40",
            "trifecta_niche_hit_rate": 31.8,
            "trifecta_niche_recovery": 157.3,
            "watch_strategy_bets": ["3連単 1-2-3"],
        }

    def _update_tsu_suminoe_history(seed: dict | None, stadium: int, boat2_racer, boat3_racer, w1, w2, w3):
        if not seed or stadium not in (9, 12):
            return
        rg = seed.setdefault("racer_global", {})
        rv = seed.setdefault("racer_venue", {})
        v123 = seed.setdefault("venue123", {})
        if boat2_racer:
            g2 = rg.setdefault((2, boat2_racer), [0, 0])
            v2 = rv.setdefault((stadium, 2, boat2_racer), [0, 0])
            g2[0] += 1
            v2[0] += 1
            if w2 == 2:
                g2[1] += 1
                v2[1] += 1
        if boat3_racer:
            g3 = rg.setdefault((3, boat3_racer), [0, 0])
            v3 = rv.setdefault((stadium, 3, boat3_racer), [0, 0])
            g3[0] += 1
            v3[0] += 1
            if w3 == 3:
                g3[1] += 1
                v3[1] += 1
        rec = v123.setdefault(stadium, [0, 0])
        rec[0] += 1
        if (w1, w2, w3) == (1, 2, 3):
            rec[1] += 1

    @app.route("/api/odds-123-timeline")
    @member_only_api
    @cached(ttl=8)  # 8秒キャッシュ (JS 側 20秒ポーリング)
    def odds_123_timeline():
        """指定日の各レースの '1-2-3' 三連単オッズ推移を返す。
        odds_scheduler が T-5min..T-1min で毎分スナップショットを残す前提。
        本日お金を入れる候補レース欄で締切までのオッズ変動を可視化するため、
        ブラウザ側で 20 秒ごとに再取得して描画する。
        8 秒キャッシュ: 同時アクセスで Supabase に同じクエリが集中するのを防ぐ。
        2026-05-21: ttl 20s→8s, polling 60s→20s に短縮。
          理由: 旧設定では T-5 目標時刻から UI 表示まで中央値 56s/最悪 113s かかっていた。
          新設定: 中央値 ~12s, 最悪 ~50s。BAN リスクなし(自社 API のみ短縮)。
        """
        target_date = request.args.get("date") or date.today().isoformat()
        today_iso = date.today().isoformat()
        cache_ttl = 8 if target_date >= today_iso else 3600
        cache_key = f"odds_123_timeline:{target_date}"
        cached_payload = _read_json_cache(cache_key, cache_ttl)
        if cached_payload is not None:
            return jsonify(cached_payload)
        result: dict[str, dict] = {}
        try:
            with db_connect() as conn:
                cur = conn.execute(
                    """
                    SELECT r.race_id, o.snapshot_label, o.odds, o.recorded_at
                      FROM races r
                      JOIN odds_trifecta o ON r.race_id = o.race_id
                     WHERE r.race_date = ?
                       AND o.combination = '1-2-3'
                       AND o.snapshot_label IS NOT NULL
                    """,
                    (target_date,),
                )
                for rid, label, odds, recorded_at in cur.fetchall():
                    try:
                        odds_val = float(odds)
                    except (TypeError, ValueError):
                        continue
                    bucket = result.setdefault(rid, {})
                    # 同じ snapshot_label が複数行ある場合は recorded_at 新しい方を採用
                    rec_str = str(recorded_at) if recorded_at is not None else ""
                    prev = bucket.get(label)
                    if prev is None or rec_str >= prev.get("recorded_at", ""):
                        bucket[label] = {"odds": odds_val, "recorded_at": rec_str}
        except Exception as e:
            # Supabase 側で snapshot_label 列未マイグレなど → 空で返す
            logger.warning("odds-123-timeline failed: %s", e)
            try:
                # Postgres は失敗トランザクションを ABORT 状態にするので rollback
                with db_connect() as conn:
                    conn.rollback()
            except Exception:
                pass
        payload = {"date": target_date, "odds": result}
        _write_json_cache(cache_key, payload)
        return jsonify(payload)

    @app.route("/api/market-signals")
    @member_only_api
    # 2026-05-30: キャッシュ強化
    #   ttl=60: 当日のリアルタイム性 (旧 300秒 → 60秒に短縮、同時アクセス時の
    #           負荷集中を防ぎつつ、 オッズ更新 (T-5min/T-1min snapshot 等) も反映)
    #   past_ttl=3600: 過去日リクエストは確定済データなので 1 時間キャッシュ
    #   bulk fetch (top-pick/exhibition) との組合せで、 当日ピーク時の処理時間が
    #   さらに削減される (44.8x 高速化 with bulk + キャッシュ命中時はほぼ 0ms).
    @cached(ttl=60, past_ttl=3600)
    def market_signals_for_date():
        """指定日のレース一覧で「市場非効率ベース +EV」シグナルを返す。
        判定優先度 (L4 戦略の定義に合わせ T-X 1-2-3 オッズを最優先):
          1. T-1min / T-2min / T-3min / T-4min / T-5min / T-15min 1-2-3 オッズ × 100
             (= 朝賭けた時点の本命金額。1-2-3 ハズレ後の race_payouts より優先)
          2. final 払戻 MIN (上記オッズが無い過去日のフォールバック)
        各レースのトリフェクタ1番人気の払戻を見て +EV/-EV ゾーンを判定
        """
        target_date = request.args.get("date") or date.today().isoformat()
        today_iso = date.today().isoformat()
        cache_ttl = 60 if target_date >= today_iso else 3600
        cache_key = f"market_signals:{target_date}"
        cached_payload = _read_json_cache(cache_key, cache_ttl)
        if cached_payload is not None:
            return jsonify(cached_payload)

        # ★パフォーマンス最適化 (backlog item 11):
        # 旧実装は 8 個の SQL × 3 個の db_connect() で Supabase 往復が
        # 重く 9.6 秒かかっていた。下記で単一接続 + クエリ統合化。
        #   - 6 個の snapshot_label 別ループ → IN 句 1 クエリ
        #   - all_race_info / morning_pred / final_payout / course1_stats
        #     を同じ conn で実行
        from datetime import datetime as _dt, timedelta as _td
        from src.evaluation.l4_strategy import (
            l4_rank as _l4_rank_shared,
            RANK_PLUS_PLUS_RECOVERY,
            RANK_PLUS_RECOVERY,
            COURSE1_WINDOW_DAYS,
            COURSE1_MIN_STARTS,
            COURSE1_THRESHOLD,
            L4_1C80_RECOVERY,
            is_1c80,
            L4_PRO_RECOVERY,
            is_l4_pro,
        )
        try:
            _td_dt = _dt.fromisoformat(target_date).date()
            cutoff_date_iso = (_td_dt - _td(days=COURSE1_WINDOW_DAYS)).isoformat()
        except Exception:
            cutoff_date_iso = "1900-01-01"

        results: dict[str, dict] = {}
        mid132_results: dict[str, int] = {}
        all_race_info: dict[str, dict] = {}
        morning_pred: dict[str, float] = {}
        course1_stats: dict[str, tuple[float, int]] = {}
        exacta_pair_affinity: dict[str, dict] = {}
        ashiya_boat4_lift: dict[str, dict] = {}
        ashiya_4head_flow_ctx: dict[str, dict] = {}
        toda_42_flow_ctx: dict[str, dict] = {}
        miyajima_boat4_watch: dict[str, dict] = {}
        tsu_suminoe_live: dict[str, dict] = {}

        # T-X snapshot の優先度 (小さいほど優先)
        # T-5min を最優先 (実運用での投票タイミング = レース 5 分前)
        # T-1min は締切間際で人気化に揺れやすいため非優先
        _SNAP_PRIORITY = {
            "T-5min": 1, "T-4min": 2, "T-3min": 3, "T-2min": 4,
            "T-1min": 5, "T-15min": 6,
        }

        try:
            with db_connect() as conn:
                # === 1. T-X 1-2-3 オッズ (IN 句 1 クエリ統合) ===
                # backlog item: 「いずれかの T-X snapshot が L4 帯 (500-1000円) なら
                # 候補」とする OR ロジックに変更 (2026-05-18 ユーザ指摘:
                # 「T-5 で 500-1000、T-1 で 500-1000 のいずれでも資金投入対象」)。
                # 旧実装: T-1min を最優先で 1 snapshot 採用 → 直前で人気化した
                #         レース (T-5=¥510→T-1=¥470) が L4 から漏れる
                # 新実装: 6 snapshot 中いずれかが L4 帯なら採用、その L4 帯 odds
                #         を min_payout に。表示用は T-5min を優先。
                try:
                    cur = conn.execute("""
                        SELECT r.race_id, o.snapshot_label, o.odds * 100 AS min_payout
                          FROM races r
                          JOIN odds_trifecta o ON r.race_id = o.race_id
                         WHERE r.race_date = ?
                           AND o.combination = '1-2-3'
                           AND o.snapshot_label IN ('T-1min','T-2min','T-3min','T-4min','T-5min','T-15min')
                    """, (target_date,))
                    # まず全 snapshot を race_id 別に収集
                    rid_snaps: dict[str, list[tuple[str, int]]] = {}
                    for rid, label, mp in cur.fetchall():
                        if not mp:
                            continue
                        rid_snaps.setdefault(rid, []).append((label, int(mp)))
                    # 各 race について: L4 帯 (500-1000) snapshot を抽出し、
                    # その中で T-5min を最優先 (実運用での投票タイミング)
                    for rid, snaps in rid_snaps.items():
                        l4_snaps = [(lbl, mp) for lbl, mp in snaps if 500 <= mp < 1000]
                        if l4_snaps:
                            # L4 帯にある snapshot のうち、表示用優先度に従って 1 つ選ぶ
                            preferred = sorted(l4_snaps, key=lambda x: _SNAP_PRIORITY.get(x[0], 99))
                            label, mp = preferred[0]
                            results[rid] = {"min_payout": mp, "source": label,
                                            "any_l4_in_window": True}
                        # else: L4 帯 snapshot 無し。オッズは締切ジャストまで動くため、
                        #   締切前は帯外を確定させない (ユーザ要望 2026-05-25
                        #   「締切ジャストまで待ちたい」。びわこ4R は T-5 17.3倍 →
                        #   T-1 15.6倍 と最後まで変動)。results に登録せず朝候補のまま
                        #   表示し、オッズセルの現在値を判断材料とする。締切後は下の
                        #   final 払戻フォールバックが帯外を確定し、締切超過レースは
                        #   フロントの .is-closed (時刻ベース) で自動グレー化される。
                except Exception as e:
                    err = str(e).lower()
                    if "snapshot_label" in err or "undefinedcolumn" in err or "column" in err:
                        try:
                            conn.rollback()
                        except Exception:
                            pass
                    else:
                        raise

                # Tier A/B is a 1-3-2 strategy, so keep its odds separate from
                # the 1-2-3 favorite odds used by the core L4 strategy.
                try:
                    cur = conn.execute("""
                        SELECT r.race_id, o.snapshot_label, o.odds * 100 AS payout_132
                          FROM races r
                          JOIN odds_trifecta o ON r.race_id = o.race_id
                         WHERE r.race_date = ?
                           AND o.combination = '1-3-2'
                           AND o.snapshot_label IN ('T-1min','T-2min','T-3min','T-4min','T-5min','T-15min')
                    """, (target_date,))
                    rid_snaps_132: dict[str, list[tuple[str, int]]] = {}
                    for rid, label, mp in cur.fetchall():
                        if not mp:
                            continue
                        rid_snaps_132.setdefault(rid, []).append((label, int(mp)))
                    for rid, snaps in rid_snaps_132.items():
                        mid_snaps = [(lbl, mp) for lbl, mp in snaps if 1000 <= mp < 2000]
                        if mid_snaps:
                            label, mp = sorted(mid_snaps, key=lambda x: _SNAP_PRIORITY.get(x[0], 99))[0]
                            mid132_results[rid] = mp
                except Exception as e:
                    logger.warning("1-3-2 odds query failed: %s", e)
                    try:
                        conn.rollback()
                    except Exception:
                        pass

                # === 2. final 払戻 (T-X 無い過去日フォールバック) ===
                try:
                    cur = conn.execute("""
                        SELECT r.race_id, MIN(pp.payout) AS min_payout
                          FROM races r
                          JOIN race_payouts pp ON r.race_id = pp.race_id AND pp.bet_type = 'trifecta'
                         WHERE r.race_date = ?
                         GROUP BY r.race_id
                    """, (target_date,))
                    for rid, mp in cur.fetchall():
                        if mp and rid not in results:
                            results[rid] = {"min_payout": mp, "source": "final"}
                except Exception as e:
                    logger.warning("final payout query failed: %s", e)
                    try:
                        conn.rollback()
                    except Exception:
                        pass

                # === 3. all_race_info (races + race_entries + race_previews) ===
                # 2号艇の national_top_2_percent も取得 (一般戦 F1 判定用)
                try:
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS race_program_tags (
                          race_id TEXT PRIMARY KEY,
                          program_type TEXT,
                          program_name TEXT,
                          is_fixed_entry INTEGER DEFAULT 0
                        )
                    """)
                    cur = conn.execute("""
                        SELECT r.race_id, r.stadium_number, r.race_grade_number,
                               r.race_number, r.race_closed_at,
                               e.class_number,
                               e.national_top_1_percent, e.local_top_1_percent,
                               e.avg_start_timing, e.age,
                               e.racer_number AS boat1_racer,
                               e.assigned_motor_top_2_percent AS boat1_motor_top2,
                               pv.weather_number, pv.start_timing_exhibition,
                               pv.wind_speed,
                               NULLIF(pv.exhibition_time, 0) AS boat1_exhibition_time,
                               e2.racer_number AS boat2_racer,
                               e2.national_top_2_percent AS boat2_top2,
                               e2.assigned_motor_top_2_percent AS boat2_motor_top2,
                               NULLIF(pv2.exhibition_time, 0) AS boat2_exhibition_time,
                               (
                                   SELECT 1 + COUNT(*)
                                     FROM race_previews rpst
                                    WHERE rpst.race_id = r.race_id
                                      AND NULLIF(rpst.start_timing_exhibition, 0) IS NOT NULL
                                      AND NULLIF(pv2.start_timing_exhibition, 0) IS NOT NULL
                                      AND NULLIF(rpst.start_timing_exhibition, 0) < NULLIF(pv2.start_timing_exhibition, 0)
                               ) AS boat2_ex_st_rank,
                               e3.racer_number AS boat3_racer,
                               e3.national_top_2_percent AS boat3_top2,
                               e3.national_top_1_percent AS boat3_natl_1,
                               e3.assigned_motor_top_2_percent AS boat3_motor_top2,
                               NULLIF(pv3.exhibition_time, 0) AS boat3_exhibition_time,
                               pv3.start_timing_exhibition AS boat3_ex_st,
                               p1.prob_top_2 AS boat1_pred_top2,
                               p3.prob_top_2 AS boat3_pred_top2,
                               p4.prob_top_2 AS boat4_pred_top2,
                               e4.class_number AS boat4_class,
                               e4.racer_number AS boat4_racer,
                               e4.assigned_motor_number AS boat4_motor_number,
                               e4.local_top_1_percent AS boat4_local_1,
                               e4.national_top_1_percent AS boat4_natl_1,
                               e4.assigned_motor_top_2_percent AS boat4_motor_top2,
                               e5.assigned_motor_top_2_percent AS boat5_motor_top2,
                               NULLIF(pv5.exhibition_time, 0) AS boat5_exhibition_time,
                               NULLIF(pv6.exhibition_time, 0) AS boat6_exhibition_time,
                               pv.wave_height AS wave_height,
                               pv4.course_number AS boat4_ex_course,
                               NULLIF(pv4.exhibition_time, 0) AS boat4_exhibition_time,
                               pv4.start_timing_exhibition AS boat4_ex_st,
                               exall.best_exhibition_time,
                               (
                                   SELECT 1 + COUNT(*)
                                     FROM race_previews rpex
                                    WHERE rpex.race_id = r.race_id
                                      AND NULLIF(rpex.exhibition_time, 0) IS NOT NULL
                                      AND NULLIF(pv.exhibition_time, 0) IS NOT NULL
                                      AND NULLIF(rpex.exhibition_time, 0) < NULLIF(pv.exhibition_time, 0)
                               ) AS boat1_ex_rank,
                               exall.n_exhibition_times,
                               COALESCE(fem.n_female, 0) AS n_female,
                               rt.tide_delta_60m_cm,
                               rt.tide_range_cm,
                               COALESCE(rt.is_high_tide_zone, 0) AS is_high_tide_zone,
                               COALESCE(rt.is_low_tide_zone, 0) AS is_low_tide_zone,
                               COALESCE(pt.program_type, '') AS program_type,
                               COALESCE(pt.program_name, '') AS program_name,
                               COALESCE(pt.is_fixed_entry, 0) AS is_fixed_entry
                        FROM races r
                        LEFT JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
                        LEFT JOIN race_entries e2 ON e2.race_id = r.race_id AND e2.boat_number = 2
                        LEFT JOIN race_entries e3 ON e3.race_id = r.race_id AND e3.boat_number = 3
                        LEFT JOIN race_entries e4 ON e4.race_id = r.race_id AND e4.boat_number = 4
                        LEFT JOIN race_entries e5 ON e5.race_id = r.race_id AND e5.boat_number = 5
                        LEFT JOIN predictions p1 ON p1.race_id = r.race_id AND p1.boat_number = 1
                        LEFT JOIN predictions p3 ON p3.race_id = r.race_id AND p3.boat_number = 3
                        LEFT JOIN predictions p4 ON p4.race_id = r.race_id AND p4.boat_number = 4
                        LEFT JOIN race_previews pv ON pv.race_id = r.race_id AND pv.boat_number = 1
                        LEFT JOIN race_previews pv2 ON pv2.race_id = r.race_id AND pv2.boat_number = 2
                        LEFT JOIN race_previews pv3 ON pv3.race_id = r.race_id AND pv3.boat_number = 3
                        LEFT JOIN race_previews pv4 ON pv4.race_id = r.race_id AND pv4.boat_number = 4
                        LEFT JOIN race_previews pv5 ON pv5.race_id = r.race_id AND pv5.boat_number = 5
                        LEFT JOIN race_previews pv6 ON pv6.race_id = r.race_id AND pv6.boat_number = 6
                        LEFT JOIN race_program_tags pt ON pt.race_id = r.race_id
                        LEFT JOIN race_tides rt ON rt.race_id = r.race_id
                        LEFT JOIN (
                            SELECT race_id,
                                   MIN(NULLIF(exhibition_time, 0)) AS best_exhibition_time,
                                   COUNT(NULLIF(exhibition_time, 0)) AS n_exhibition_times
                              FROM race_previews
                             GROUP BY race_id
                        ) exall ON exall.race_id = r.race_id
                        LEFT JOIN (
                            SELECT ef.race_id, COUNT(*) AS n_female
                              FROM race_entries ef
                              JOIN racers rc ON rc.racer_number = ef.racer_number
                             WHERE rc.gender = 2
                             GROUP BY ef.race_id
                        ) fem ON fem.race_id = r.race_id
                        WHERE r.race_date = ?
                    """, (target_date,))
                    for (rid, stadium, grade, race_no, race_closed_at, cls, natl1, loc1,
                         avg_st, age, boat1_racer, boat1_motor_top2,
                         weather, ex_st, wind_speed, boat1_exhibition_time,
                         boat2_racer, boat2_top2, boat2_motor_top2, boat2_exhibition_time, boat2_ex_st_rank,
                         boat3_racer, boat3_top2, boat3_natl_1, boat3_motor_top2, boat3_exhibition_time, boat3_ex_st,
                         boat1_pred_top2, boat3_pred_top2, boat4_pred_top2, boat4_class,
                         boat4_racer, boat4_motor_number, boat4_local_1, boat4_natl_1, boat4_motor_top2, boat5_motor_top2,
                         boat5_exhibition_time, boat6_exhibition_time, wave_height,
                         boat4_ex_course, boat4_exhibition_time, boat4_ex_st,
                         best_exhibition_time, boat1_ex_rank, n_exhibition_times,
                         n_female, tide_delta_60m_cm, tide_range_cm, is_high_tide_zone, is_low_tide_zone,
                         program_type, program_name, is_fixed_entry) in cur.fetchall():
                        all_race_info[rid] = {
                            "stadium": stadium, "grade": grade,
                            "race_number": race_no,
                            "race_closed_at": race_closed_at,
                            "class": cls,
                            "natl_1": natl1, "local_1": loc1,
                            "avg_st": avg_st, "age": age,
                            "boat1_racer": boat1_racer,
                            "boat1_motor_top2": boat1_motor_top2,
                            "weather": weather, "ex_st": ex_st,
                            "wind_speed": wind_speed,
                            "boat1_exhibition_time": boat1_exhibition_time,
                            "boat2_racer": boat2_racer,
                            "boat2_top2": boat2_top2,
                            "boat2_motor_top2": boat2_motor_top2,
                            "boat2_exhibition_time": boat2_exhibition_time,
                            "boat2_ex_st_rank": boat2_ex_st_rank,
                            "boat3_racer": boat3_racer,
                            "boat3_top2": boat3_top2,
                            "boat3_natl_1": boat3_natl_1,
                            "boat3_motor_top2": boat3_motor_top2,
                            "boat3_exhibition_time": boat3_exhibition_time,
                            "boat3_ex_st": boat3_ex_st,
                            "boat1_pred_top2": boat1_pred_top2,
                            "boat3_pred_top2": boat3_pred_top2,
                            "boat4_pred_top2": boat4_pred_top2,
                            "boat4_class": boat4_class,
                            "boat4_racer": boat4_racer,
                            "boat4_motor_number": boat4_motor_number,
                            "boat4_local_1": boat4_local_1,
                            "boat4_natl_1": boat4_natl_1,
                            "boat4_motor_top2": boat4_motor_top2,
                            "boat5_motor_top2": boat5_motor_top2,
                            "boat5_exhibition_time": boat5_exhibition_time,
                            "boat6_exhibition_time": boat6_exhibition_time,
                            "wave_height": wave_height,
                            "boat4_ex_course": boat4_ex_course,
                            "boat4_exhibition_time": boat4_exhibition_time,
                            "boat4_ex_st": boat4_ex_st,
                            "best_exhibition_time": best_exhibition_time,
                            "boat1_ex_rank": boat1_ex_rank,
                            "n_exhibition_times": n_exhibition_times,
                            "n_female": n_female,
                            "tide_delta_60m_cm": tide_delta_60m_cm,
                            "tide_range_cm": tide_range_cm,
                            "is_high_tide_zone": is_high_tide_zone,
                            "is_low_tide_zone": is_low_tide_zone,
                            "program_type": program_type,
                            "program_name": program_name,
                            "is_fixed_entry": is_fixed_entry,
                        }
                except Exception as e:
                    logger.warning("all_race_info query failed: %s", e)
                    try:
                        conn.rollback()
                    except Exception:
                        pass

                # === 4. predictions (1号艇 prob_first) ===
                try:
                    cur = conn.execute("""
                        SELECT p.race_id, p.prob_first
                        FROM predictions p
                        JOIN races r ON p.race_id = r.race_id
                        WHERE r.race_date = ? AND p.boat_number = 1
                    """, (target_date,))
                    for rid, p1 in cur.fetchall():
                        if p1 is not None:
                            morning_pred[rid] = p1
                except Exception as e:
                    logger.warning("morning_pred query failed: %s", e)
                    try:
                        conn.rollback()
                    except Exception:
                        pass

                # === 5. course1_stats (L4+1c80 用、過去 180 日 1コース成績) ===
                # 2026-05-30: 事前集計テーブル course1_stats_cache を優先使用.
                #   旧実装は WITH 句 + 多段 LEFT JOIN で重く 数百ms かかっていた.
                #   cache table を使えば 1 SQL の SELECT 結合 1 回で取得可能.
                #   cache がない (= バッチ未実行 / 当日分なし) ときは旧 SQL に fallback.
                boat3_signal_ctx: dict[str, dict] = {}
                try:
                    boat3_same_day_roll: dict[int, dict[str, float]] = {}
                    boat3_venue_avg: dict[int, float] = {}
                    cur = conn.execute("""
                        SELECT r.stadium_number,
                               AVG(NULLIF(pv3.exhibition_time, 0)) AS avg_ex
                          FROM races r
                          JOIN race_previews pv3
                            ON pv3.race_id = r.race_id
                           AND pv3.boat_number = 3
                         WHERE r.race_date < ?
                           AND NULLIF(pv3.exhibition_time, 0) IS NOT NULL
                         GROUP BY r.stadium_number
                    """, (target_date,))
                    for stadium_no, avg_ex in cur.fetchall():
                        if avg_ex is not None:
                            boat3_venue_avg[int(stadium_no)] = float(avg_ex)

                    ordered_races = sorted(
                        all_race_info.items(),
                        key=lambda kv: (
                            int(kv[1].get("stadium") or 0),
                            int(kv[1].get("race_number") or 0),
                            kv[0],
                        ),
                    )
                    for race_id, info in ordered_races:
                        stadium_no = info.get("stadium")
                        boat3_ex = info.get("boat3_exhibition_time")
                        same_day_avg = None
                        if stadium_no is not None and boat3_ex is not None:
                            stat = boat3_same_day_roll.get(int(stadium_no))
                            if stat and stat["n"] > 0:
                                same_day_avg = stat["sum"] / stat["n"]
                        boat3_signal_ctx[race_id] = {
                            "race_id": race_id,
                            "stadium": stadium_no,
                            "race_number": info.get("race_number"),
                            "boat3_motor_top2": info.get("boat3_motor_top2"),
                            "boat3_ex_st": info.get("boat3_ex_st"),
                            "boat3_exhibition_time": boat3_ex,
                            "best_exhibition_time": info.get("best_exhibition_time"),
                            "same_day_boat3_avg_ex": same_day_avg,
                            "venue_boat3_avg_ex": boat3_venue_avg.get(int(stadium_no)) if stadium_no is not None else None,
                        }
                        if stadium_no is not None and boat3_ex is not None:
                            stat = boat3_same_day_roll.setdefault(int(stadium_no), {"n": 0, "sum": 0.0})
                            stat["n"] += 1
                            stat["sum"] += float(boat3_ex)
                except Exception as e:
                    logger.warning("boat3 niche context build failed: %s", e)
                    try:
                        conn.rollback()
                    except Exception:
                        pass

                tokoname_12_signal_ctx: dict[str, dict] = {}
                try:
                    tokoname_targets = {
                        rid: info for rid, info in all_race_info.items()
                        if info.get("stadium") == 8
                    }
                    if tokoname_targets:
                        boat2_venue_avg: dict[int, float] = {}
                        cur = conn.execute("""
                            SELECT r.stadium_number,
                                   AVG(NULLIF(pv2.exhibition_time, 0)) AS avg_ex
                              FROM races r
                              JOIN race_previews pv2
                                ON pv2.race_id = r.race_id
                               AND pv2.boat_number = 2
                             WHERE r.race_date < ?
                               AND NULLIF(pv2.exhibition_time, 0) IS NOT NULL
                             GROUP BY r.stadium_number
                        """, (target_date,))
                        for stadium_no, avg_ex in cur.fetchall():
                            if avg_ex is not None:
                                boat2_venue_avg[int(stadium_no)] = float(avg_ex)

                        target_racers = sorted({
                            info.get("boat1_racer") for info in tokoname_targets.values()
                            if info.get("boat1_racer")
                        })
                        boat1_course1_stats: dict[int, tuple[float | None, float | None]] = {}
                        if target_racers:
                            racer_placeholders = ",".join("?" for _ in target_racers)
                            cur = conn.execute(f"""
                                SELECT e.racer_number,
                                       COUNT(*) AS starts,
                                       SUM(CASE WHEN rr.finishing_position = 1 THEN 1 ELSE 0 END) AS wins,
                                       SUM(CASE WHEN rr.finishing_position = 1 AND rr.kimarite = '逃げ' THEN 1 ELSE 0 END) AS escape_wins
                                  FROM race_entries e
                                  JOIN races rh ON rh.race_id = e.race_id
                                  JOIN race_results rr
                                    ON rr.race_id = e.race_id
                                   AND rr.boat_number = e.boat_number
                                 WHERE rh.race_date < ?
                                   AND e.boat_number = 1
                                   AND e.racer_number IN ({racer_placeholders})
                                 GROUP BY e.racer_number
                            """, [target_date, *target_racers])
                            for racer_no, starts, wins, escape_wins in cur.fetchall():
                                starts_v = int(starts or 0)
                                if starts_v <= 0:
                                    continue
                                boat1_course1_stats[int(racer_no)] = (
                                    float(wins or 0) / starts_v,
                                    float(escape_wins or 0) / starts_v,
                                )

                        ordered_races = sorted(
                            tokoname_targets.items(),
                            key=lambda kv: (
                                int(kv[1].get("stadium") or 0),
                                int(kv[1].get("race_number") or 0),
                                kv[0],
                            ),
                        )
                        boat2_same_day_roll: dict[int, dict[str, float]] = {}
                        for race_id, info in ordered_races:
                            stadium_no = info.get("stadium")
                            boat2_ex = info.get("boat2_exhibition_time")
                            same_day_avg = None
                            if stadium_no is not None and boat2_ex is not None:
                                stat = boat2_same_day_roll.get(int(stadium_no))
                                if stat and stat["n"] > 0:
                                    same_day_avg = stat["sum"] / stat["n"]
                            course1_win_rate, escape_share = boat1_course1_stats.get(
                                int(info.get("boat1_racer") or 0),
                                (None, None),
                            )
                            tokoname_12_signal_ctx[race_id] = {
                                **info,
                                "boat2_same_day_avg_ex": same_day_avg,
                                "boat2_venue_avg_ex": boat2_venue_avg.get(int(stadium_no)) if stadium_no is not None else None,
                                "boat1_course1_win_rate": course1_win_rate,
                                "boat1_escape_share": escape_share,
                            }
                            if stadium_no is not None and boat2_ex is not None:
                                stat = boat2_same_day_roll.setdefault(int(stadium_no), {"n": 0, "sum": 0.0})
                                stat["n"] += 1
                                stat["sum"] += float(boat2_ex)
                except Exception as e:
                    logger.warning("tokoname 1-2 context build failed: %s", e)
                    try:
                        conn.rollback()
                    except Exception:
                        pass

                used_cache = False
                try:
                    cur = conn.execute("""
                        SELECT e.race_id, c.starts, c.wins
                          FROM course1_stats_cache c
                          JOIN race_entries e
                            ON e.racer_number = c.racer_number
                           AND e.boat_number = 1
                          JOIN races r ON r.race_id = e.race_id
                         WHERE c.as_of_date = ?
                           AND r.race_date = ?
                           AND c.starts >= ?
                    """, (target_date, target_date, COURSE1_MIN_STARTS))
                    rows = cur.fetchall()
                    if rows:
                        used_cache = True
                        for rid, starts, wins in rows:
                            course1_stats[rid] = (wins / starts, starts)
                except Exception as e:
                    # cache table が無い (= migration 前) なら fallback へ
                    logger.debug("course1 cache lookup failed: %s", e)

                if not used_cache:
                    # Fallback: 旧 SQL で計算 (cache 未生成 / 当日分なしの場合)
                    try:
                        cur = conn.execute("""
                            WITH target_races AS (
                                SELECT r.race_id, r.race_date, e.racer_number
                                FROM races r
                                JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
                                WHERE r.race_date = ?
                            )
                            SELECT t.race_id,
                                   COUNT(res.race_id) AS starts,
                                   SUM(CASE WHEN res.finishing_position = 1 THEN 1 ELSE 0 END) AS wins
                              FROM target_races t
                              LEFT JOIN race_entries e2 ON e2.racer_number = t.racer_number AND e2.boat_number = 1
                              LEFT JOIN races r2 ON r2.race_id = e2.race_id
                              LEFT JOIN race_results res ON res.race_id = e2.race_id AND res.boat_number = 1
                              WHERE r2.race_date < t.race_date
                                AND r2.race_date >= ?
                                AND res.finishing_position IS NOT NULL
                              GROUP BY t.race_id
                        """, (target_date, cutoff_date_iso))
                        for rid, starts, wins in cur.fetchall():
                            if starts and starts >= COURSE1_MIN_STARTS:
                                course1_stats[rid] = (wins / starts, starts)
                    except Exception as e:
                        logger.warning("course1 stats fetch failed: %s", e)
                        try:
                            conn.rollback()
                        except Exception:
                            pass


                # === 6. exacta niche pair affinity (current boat1 racer vs boat2 racer) ===
                # === 6a. Ashiya exacta 4-1 lift context (future-free, before target date only) ===
                try:
                    ashiya_targets = {
                        rid: info for rid, info in all_race_info.items()
                        if info.get("stadium") == 21
                           and info.get("boat4_racer")
                           and info.get("boat4_motor_number") is not None
                    }
                    if ashiya_targets:
                        try:
                            ashiya_cutoff_iso = (
                                _dt.fromisoformat(target_date).date() - _td(days=730)
                            ).isoformat()
                        except Exception:
                            ashiya_cutoff_iso = "1900-01-01"

                        target_racers = sorted({info.get("boat4_racer") for info in ashiya_targets.values()})
                        racer_placeholders = ",".join("?" for _ in target_racers)

                        racer_course4: dict[int, dict] = {}
                        cur = conn.execute(f"""
                            SELECT e.racer_number,
                                   rr.start_timing,
                                   rr.finishing_position
                              FROM race_entries e
                              JOIN races rh ON rh.race_id = e.race_id
                              JOIN race_results rr
                                ON rr.race_id = e.race_id
                               AND rr.boat_number = e.boat_number
                             WHERE rh.race_date < ?
                               AND rh.race_date >= ?
                               AND e.racer_number IN ({racer_placeholders})
                               AND rr.course_number = 4
                               AND rr.start_timing IS NOT NULL
                               AND rr.finishing_position IS NOT NULL
                        """, [target_date, ashiya_cutoff_iso, *target_racers])
                        for racer, st, pos in cur.fetchall():
                            rec = racer_course4.setdefault(racer, {"n": 0, "st_sum": 0.0, "wins": 0})
                            rec["n"] += 1
                            rec["st_sum"] += float(st)
                            if int(pos) == 1:
                                rec["wins"] += 1

                        racer_ex_rows: dict[int, list[float]] = {}
                        cur = conn.execute(f"""
                            SELECT e.racer_number,
                                   pv.exhibition_time
                              FROM race_entries e
                              JOIN races rh ON rh.race_id = e.race_id
                              JOIN race_previews pv
                                ON pv.race_id = e.race_id
                               AND pv.boat_number = e.boat_number
                             WHERE rh.race_date < ?
                               AND rh.race_date >= ?
                               AND e.racer_number IN ({racer_placeholders})
                               AND pv.exhibition_time IS NOT NULL
                               AND pv.exhibition_time > 0
                             ORDER BY e.racer_number, rh.race_date DESC, rh.race_number DESC
                        """, [target_date, ashiya_cutoff_iso, *target_racers])
                        for racer, ex_time in cur.fetchall():
                            rows = racer_ex_rows.setdefault(racer, [])
                            if len(rows) < 20:
                                rows.append(float(ex_time))

                        target_motor_pairs = {
                            (str(info.get("stadium")), str(info.get("boat4_motor_number")))
                            for info in ashiya_targets.values()
                        }
                        target_motors = sorted({pair[1] for pair in target_motor_pairs})
                        motor_placeholders = ",".join("?" for _ in target_motors)
                        motor_ex_rows: dict[tuple[str, str], list[float]] = {}
                        cur = conn.execute(f"""
                            SELECT rh.stadium_number,
                                   e.assigned_motor_number,
                                   pv.exhibition_time
                              FROM race_entries e
                              JOIN races rh ON rh.race_id = e.race_id
                              JOIN race_previews pv
                                ON pv.race_id = e.race_id
                               AND pv.boat_number = e.boat_number
                             WHERE rh.race_date < ?
                               AND rh.race_date >= ?
                               AND rh.stadium_number = 21
                               AND e.assigned_motor_number IN ({motor_placeholders})
                               AND pv.exhibition_time IS NOT NULL
                               AND pv.exhibition_time > 0
                             ORDER BY rh.stadium_number, e.assigned_motor_number,
                                      rh.race_date DESC, rh.race_number DESC
                        """, [target_date, ashiya_cutoff_iso, *target_motors])
                        for stadium_no, motor_no, ex_time in cur.fetchall():
                            key = (str(stadium_no), str(motor_no))
                            if key not in target_motor_pairs:
                                continue
                            rows = motor_ex_rows.setdefault(key, [])
                            if len(rows) < 12:
                                rows.append(float(ex_time))

                        for rid, info in ashiya_targets.items():
                            racer = info.get("boat4_racer")
                            racer_stats = racer_course4.get(racer, {})
                            racer_ex = racer_ex_rows.get(racer, [])
                            motor_key = (str(info.get("stadium")), str(info.get("boat4_motor_number")))
                            motor_ex = motor_ex_rows.get(motor_key, [])
                            r4_n = int(racer_stats.get("n") or 0)
                            r4_avg_st = (
                                float(racer_stats["st_sum"]) / r4_n
                                if r4_n else None
                            )
                            r4_win_rate = (
                                float(racer_stats["wins"]) / r4_n
                                if r4_n else None
                            )
                            racer_avg_ex = (sum(racer_ex) / len(racer_ex)) if racer_ex else None
                            motor_avg_ex = (sum(motor_ex) / len(motor_ex)) if motor_ex else None
                            ashiya_boat4_lift[rid] = {
                                **info,
                                "boat4_course4_n": r4_n,
                                "boat4_course4_avg_st": r4_avg_st,
                                "boat4_course4_win_rate": r4_win_rate,
                                "boat4_racer_ex_n": len(racer_ex),
                                "boat4_racer_avg_ex": racer_avg_ex,
                                "boat4_motor_ex_n": len(motor_ex),
                                "boat4_motor_avg_ex": motor_avg_ex,
                            }
                except Exception as e:
                    logger.warning("ashiya boat4 lift context failed: %s", e)
                    try:
                        conn.rollback()
                    except Exception:
                        pass

                # === 6aa. Toda 4-2-all flow context (future-free, before target date only) ===
                try:
                    toda_targets = {
                        rid: info for rid, info in all_race_info.items()
                        if info.get("stadium") == 2 and info.get("boat4_racer")
                    }
                    if toda_targets:
                        try:
                            toda_cutoff_iso = (_dt.fromisoformat(target_date).date() - _td(days=730)).isoformat()
                        except Exception:
                            toda_cutoff_iso = "1900-01-01"
                        target_racers = sorted({info.get("boat4_racer") for info in toda_targets.values()})
                        racer_placeholders = ",".join("?" for _ in target_racers)
                        racer_course4_rows: dict[int, list[tuple[float | None, int, str | None]]] = {}
                        cur = conn.execute(f"""
                            SELECT e.racer_number, rr.start_timing, rr.finishing_position, NULLIF(rr.kimarite, '') AS kimarite
                              FROM race_entries e
                              JOIN races rh ON rh.race_id = e.race_id
                              JOIN race_results rr ON rr.race_id = e.race_id AND rr.boat_number = e.boat_number
                             WHERE rh.stadium_number = 2
                               AND rh.race_date < ?
                               AND rh.race_date >= ?
                               AND e.racer_number IN ({racer_placeholders})
                               AND rr.course_number = 4
                             ORDER BY e.racer_number, rh.race_date DESC, rh.race_number DESC
                        """, [target_date, toda_cutoff_iso, *target_racers])
                        for racer, st, pos, kim in cur.fetchall():
                            rows = racer_course4_rows.setdefault(racer, [])
                            if len(rows) < 128:
                                try:
                                    st_v = float(st) if st is not None else None
                                except Exception:
                                    st_v = None
                                rows.append((st_v, int(pos or 0), str(kim) if kim is not None else None))
                        makuri_words = {"???", "?????", "??", "????"}
                        for rid, info in toda_targets.items():
                            racer = info.get("boat4_racer")
                            rows = racer_course4_rows.get(racer, [])
                            n180 = len(rows)
                            wins = sum(1 for _st, pos, _kim in rows if int(pos) == 1)
                            st_vals = [st for st, _pos, _kim in rows if st is not None]
                            makuri_wins = sum(1 for _st, pos, kim in rows if int(pos) == 1 and kim in makuri_words)
                            toda_42_flow_ctx[rid] = {
                                **info,
                                "boat4_course4_n180": n180,
                                "boat4_course4_avg_st_180": (sum(st_vals) / len(st_vals)) if st_vals else None,
                                "boat4_course4_win_rate_180": (wins / n180) if n180 else None,
                                "boat4_course4_makuri_rate_180": (makuri_wins / n180) if n180 else None,
                            }
                except Exception as e:
                    logger.warning("toda 4-2-all context failed: %s", e)
                    try:
                        conn.rollback()
                    except Exception:
                        pass

                # === 6aa. Ashiya 4-head flow context (future-free, before target date only) ===
                try:
                    ashiya_targets = {
                        rid: info for rid, info in all_race_info.items()
                        if info.get("stadium") == 21
                           and info.get("boat4_racer")
                           and info.get("boat4_motor_number") is not None
                    }
                    if ashiya_targets:
                        try:
                            racer_cutoff_iso = (_dt.fromisoformat(target_date).date() - _td(days=730)).isoformat()
                            motor_cutoff_iso = (_dt.fromisoformat(target_date).date() - _td(days=180)).isoformat()
                        except Exception:
                            racer_cutoff_iso = "1900-01-01"
                            motor_cutoff_iso = "1900-01-01"

                        target_racers = sorted({info.get("boat4_racer") for info in ashiya_targets.values()})
                        racer_placeholders = ",".join("?" for _ in target_racers)
                        racer_course4_rows: dict[int, list[tuple[float | None, int]]] = {}
                        cur = conn.execute(f"""
                            SELECT e.racer_number, rr.start_timing, rr.finishing_position
                              FROM race_entries e
                              JOIN races rh ON rh.race_id = e.race_id
                              JOIN race_results rr ON rr.race_id = e.race_id AND rr.boat_number = e.boat_number
                             WHERE rh.stadium_number = 21
                               AND rh.race_date < ?
                               AND rh.race_date >= ?
                               AND e.racer_number IN ({racer_placeholders})
                               AND rr.course_number = 4
                             ORDER BY e.racer_number, rh.race_date DESC, rh.race_number DESC
                        """, [target_date, racer_cutoff_iso, *target_racers])
                        for racer, st, pos in cur.fetchall():
                            rows = racer_course4_rows.setdefault(int(racer), [])
                            if len(rows) < 128:
                                try:
                                    st_v = float(st) if st is not None else None
                                except Exception:
                                    st_v = None
                                rows.append((st_v, int(pos or 0)))

                        target_motors = sorted({info.get("boat4_motor_number") for info in ashiya_targets.values()})
                        motor_placeholders = ",".join("?" for _ in target_motors)
                        motor_course4_rows: dict[int, list[int]] = {}
                        cur = conn.execute(f"""
                            SELECT e.assigned_motor_number, rr.finishing_position
                              FROM race_entries e
                              JOIN races rh ON rh.race_id = e.race_id
                              JOIN race_results rr ON rr.race_id = e.race_id AND rr.boat_number = e.boat_number
                             WHERE rh.stadium_number = 21
                               AND rh.race_date < ?
                               AND rh.race_date >= ?
                               AND e.assigned_motor_number IN ({motor_placeholders})
                               AND rr.course_number = 4
                             ORDER BY e.assigned_motor_number, rh.race_date DESC, rh.race_number DESC
                        """, [target_date, motor_cutoff_iso, *target_motors])
                        for motor_no, pos in cur.fetchall():
                            rows = motor_course4_rows.setdefault(int(motor_no), [])
                            if len(rows) < 128:
                                rows.append(int(pos or 0))

                        for rid, info in ashiya_targets.items():
                            racer_rows = racer_course4_rows.get(int(info.get("boat4_racer")), [])
                            motor_rows = motor_course4_rows.get(int(info.get("boat4_motor_number")), [])
                            n180 = len(racer_rows)
                            wins = sum(1 for _st, pos in racer_rows if pos == 1)
                            st_vals = [st for st, _pos in racer_rows if st is not None]
                            mn = len(motor_rows)
                            mwins = sum(1 for pos in motor_rows if pos == 1)
                            ashiya_4head_flow_ctx[rid] = {
                                **info,
                                "boat4_course4_n180": n180,
                                "boat4_course4_avg_st_180": (sum(st_vals) / len(st_vals)) if st_vals else None,
                                "boat4_course4_win_rate_180": (wins / n180) if n180 else None,
                                "boat4_motor4_n180": mn,
                                "boat4_motor4_win_rate_180": (mwins / mn) if mn else None,
                            }
                except Exception as e:
                    logger.warning("ashiya 4-head flow context failed: %s", e)
                    try:
                        conn.rollback()
                    except Exception:
                        pass

                # === 6b. Miyajima trifecta 4-head watch context (future-free, before target date only) ===
                try:
                    miyajima_targets = {
                        rid: info for rid, info in all_race_info.items()
                        if info.get("stadium") == 17
                           and info.get("boat4_class") == 1
                           and info.get("boat4_racer")
                           and info.get("boat4_motor_number") is not None
                           and (info.get("boat4_motor_top2") or 0) >= 40
                    }
                    if miyajima_targets:
                        try:
                            racer_cutoff_iso = (
                                _dt.fromisoformat(target_date).date() - _td(days=730)
                            ).isoformat()
                            motor_cutoff_iso = (
                                _dt.fromisoformat(target_date).date() - _td(days=180)
                            ).isoformat()
                        except Exception:
                            racer_cutoff_iso = "1900-01-01"
                            motor_cutoff_iso = "1900-01-01"

                        target_racers = sorted({info.get("boat4_racer") for info in miyajima_targets.values()})
                        racer_placeholders = ",".join("?" for _ in target_racers)
                        racer_course4: dict[int, dict] = {}
                        cur = conn.execute(f"""
                            SELECT e.racer_number,
                                   rr.start_timing,
                                   rr.finishing_position
                              FROM race_entries e
                              JOIN races rh ON rh.race_id = e.race_id
                              JOIN race_results rr
                                ON rr.race_id = e.race_id
                               AND rr.boat_number = e.boat_number
                             WHERE rh.race_date < ?
                               AND rh.race_date >= ?
                               AND e.racer_number IN ({racer_placeholders})
                               AND rr.course_number = 4
                               AND rr.start_timing IS NOT NULL
                               AND rr.finishing_position IS NOT NULL
                        """, [target_date, racer_cutoff_iso, *target_racers])
                        for racer, st, pos in cur.fetchall():
                            rec = racer_course4.setdefault(racer, {"n": 0, "st_sum": 0.0, "wins": 0})
                            rec["n"] += 1
                            rec["st_sum"] += float(st)
                            if int(pos) == 1:
                                rec["wins"] += 1

                        target_motors = sorted({str(info.get("boat4_motor_number")) for info in miyajima_targets.values()})
                        motor_placeholders = ",".join("?" for _ in target_motors)
                        motor_course4: dict[tuple[str, str], dict] = {}
                        cur = conn.execute(f"""
                            SELECT rh.stadium_number,
                                   e.assigned_motor_number,
                                   rr.finishing_position
                              FROM race_entries e
                              JOIN races rh ON rh.race_id = e.race_id
                              JOIN race_results rr
                                ON rr.race_id = e.race_id
                               AND rr.boat_number = e.boat_number
                             WHERE rh.race_date < ?
                               AND rh.race_date >= ?
                               AND rh.stadium_number = 17
                               AND e.assigned_motor_number IN ({motor_placeholders})
                               AND rr.course_number = 4
                               AND rr.finishing_position IS NOT NULL
                        """, [target_date, motor_cutoff_iso, *target_motors])
                        for stadium_no, motor_no, pos in cur.fetchall():
                            key = (str(stadium_no), str(motor_no))
                            rec = motor_course4.setdefault(key, {"n": 0, "wins": 0})
                            rec["n"] += 1
                            if int(pos) == 1:
                                rec["wins"] += 1

                        target_race_ids = sorted(miyajima_targets)
                        race_placeholders = ",".join("?" for _ in target_race_ids)
                        racer_ex_hist: dict[int, dict] = {}
                        cur = conn.execute(f"""
                            SELECT e.racer_number,
                                   rp.start_timing_exhibition
                              FROM race_entries e
                              JOIN races rh ON rh.race_id = e.race_id
                              JOIN race_previews rp
                                ON rp.race_id = e.race_id
                               AND rp.boat_number = e.boat_number
                             WHERE rh.race_date < ?
                               AND rh.race_date >= ?
                               AND e.racer_number IN ({racer_placeholders})
                               AND rp.start_timing_exhibition IS NOT NULL
                        """, [target_date, racer_cutoff_iso, *target_racers])
                        for racer, ex_time in cur.fetchall():
                            try:
                                ex_v = float(ex_time)
                            except Exception:
                                continue
                            rec = racer_ex_hist.setdefault(racer, {"n": 0, "sum": 0.0})
                            rec["n"] += 1
                            rec["sum"] += ex_v

                        race_ex_rows: dict[str, list[float]] = {}
                        cur = conn.execute(f"""
                            SELECT rp.race_id,
                                   rp.start_timing_exhibition
                              FROM race_previews rp
                             WHERE rp.race_id IN ({race_placeholders})
                               AND rp.start_timing_exhibition IS NOT NULL
                        """, [*target_race_ids])
                        for race_id, ex_time in cur.fetchall():
                            try:
                                ex_v = float(ex_time)
                            except Exception:
                                continue
                            race_ex_rows.setdefault(str(race_id), []).append(ex_v)

                        for rid, info in miyajima_targets.items():
                            racer = info.get("boat4_racer")
                            racer_stats = racer_course4.get(racer, {})
                            motor_key = (str(info.get("stadium")), str(info.get("boat4_motor_number")))
                            motor_stats = motor_course4.get(motor_key, {})
                            racer_ex_stats = racer_ex_hist.get(racer, {})
                            r4_n = int(racer_stats.get("n") or 0)
                            m4_n = int(motor_stats.get("n") or 0)
                            rex_n = int(racer_ex_stats.get("n") or 0)
                            boat4_ex_time = info.get("boat4_ex_time")
                            try:
                                boat4_ex_v = float(boat4_ex_time) if boat4_ex_time is not None else None
                            except Exception:
                                boat4_ex_v = None
                            race_times = race_ex_rows.get(str(rid), [])
                            best_ex = min(race_times) if race_times else None
                            boat4_best_diff = (
                                (boat4_ex_v - best_ex)
                                if boat4_ex_v is not None and best_ex is not None
                                else None
                            )
                            boat4_racer_avg_ex = (
                                float(racer_ex_stats["sum"]) / rex_n if rex_n else None
                            )
                            boat4_racer_gain = (
                                boat4_racer_avg_ex - boat4_ex_v
                                if boat4_racer_avg_ex is not None and boat4_ex_v is not None
                                else None
                            )
                            miyajima_boat4_watch[rid] = {
                                **info,
                                "boat4_course4_n": r4_n,
                                "boat4_course4_avg_st": (
                                    float(racer_stats["st_sum"]) / r4_n if r4_n else None
                                ),
                                "boat4_course4_win_rate": (
                                    float(racer_stats["wins"]) / r4_n if r4_n else None
                                ),
                                "boat4_motor4_n": m4_n,
                                "boat4_motor4_win_rate": (
                                    float(motor_stats["wins"]) / m4_n if m4_n else None
                                ),
                                "boat4_racer_ex_n": rex_n,
                                "boat4_racer_avg_ex": boat4_racer_avg_ex,
                                "boat4_best_ex_diff": boat4_best_diff,
                                "boat4_racer_gain": boat4_racer_gain,
                            }
                except Exception as e:
                    logger.warning("miyajima boat4 watch context failed: %s", e)
                    try:
                        conn.rollback()
                    except Exception:
                        pass

                try:
                    target_pairs = {
                        (info.get("boat1_racer"), info.get("boat2_racer"))
                        for info in all_race_info.values()
                        if info.get("boat1_racer") and info.get("boat2_racer")
                    }
                    if target_pairs:
                        r1s = sorted({p[0] for p in target_pairs})
                        r2s = sorted({p[1] for p in target_pairs})
                        q1 = ",".join("?" for _ in r1s)
                        q2 = ",".join("?" for _ in r2s)
                        cur = conn.execute(f"""
                            SELECT e1.racer_number AS r1, e2.racer_number AS r2,
                                   rr1.finishing_position AS p1,
                                   rr2.finishing_position AS p2
                              FROM race_entries e1
                              JOIN race_entries e2
                                ON e2.race_id = e1.race_id
                               AND e2.racer_number <> e1.racer_number
                              JOIN races rh ON rh.race_id = e1.race_id
                              JOIN race_results rr1
                                ON rr1.race_id = e1.race_id
                               AND rr1.boat_number = e1.boat_number
                              JOIN race_results rr2
                                ON rr2.race_id = e2.race_id
                               AND rr2.boat_number = e2.boat_number
                             WHERE rh.race_date < ?
                               AND e1.racer_number IN ({q1})
                               AND e2.racer_number IN ({q2})
                               AND rr1.finishing_position IS NOT NULL
                               AND rr2.finishing_position IS NOT NULL
                        """, [target_date, *r1s, *r2s])
                        pair_stats: dict[tuple[int, int], list[int]] = {}
                        for r1, r2, p1, p2 in cur.fetchall():
                            key = (r1, r2)
                            if key not in target_pairs:
                                continue
                            rec = pair_stats.setdefault(key, [0, 0])
                            rec[0] += 1
                            if p1 < p2:
                                rec[1] += 1
                        for rid, info in all_race_info.items():
                            key = (info.get("boat1_racer"), info.get("boat2_racer"))
                            meetings, ahead = pair_stats.get(key, [0, 0])
                            rate = (ahead / meetings) if meetings else None
                            exacta_pair_affinity[rid] = {
                                "meetings": meetings,
                                "ahead": ahead,
                                "rate": rate,
                                "is_boat1_over_boat2": bool(meetings >= 2 and rate is not None and rate >= 0.60),
                            }

                    if all_race_info:
                        tsu_seed = _build_tsu_suminoe_history_seed(conn, target_date)
                        for rid, info in all_race_info.items():
                            ctx = _build_tsu_suminoe_ctx(info.get("stadium"), info, tsu_seed)
                            if ctx:
                                tsu_suminoe_live[rid] = ctx
                except Exception as e:
                    logger.warning("exacta pair affinity fetch failed: %s", e)
                    try:
                        conn.rollback()
                    except Exception:
                        pass
        except Exception as e:
            logger.exception("market-signals setup failed: %s", e)

        EXCLUDE_B = set(_LOSING_VENUES.keys()) | set(_QUESTIONABLE_VENUES.keys())

        def _rain_exclusion_active(race_closed_at) -> bool:
            """Return True from five minutes before close; weather is judged then."""
            if not race_closed_at:
                return False
            try:
                closed = datetime.fromisoformat(str(race_closed_at).replace(" ", "T"))
            except (TypeError, ValueError):
                return False
            now = datetime.now(closed.tzinfo) if closed.tzinfo else datetime.now()
            return now >= closed - timedelta(minutes=5)

        def _l4_rank(natl_1, local_1):
            """1号艇選手の成績から L4 のサブランク判定 (単一情報源を委譲)"""
            return _l4_rank_shared(natl_1, local_1)

        def _compute_tetsuban(base: dict, race_no: int) -> tuple[int, str]:
            """鉄板度スコア (1-6) と表示ラベルを計算 (backlog item 11)。

            条件 (各 1 点、合計 0-6):
              + 高グレード (SG/G1/G2)
              + F1 一般戦 (一般×国1%≥7×2号40)
              + race_number 11 または 12 (prime / メインレース)
              + race_number 12 (最終レース、上に+1で 12R は計 2 点)
                → 12R は実質 +2 点扱いで「鉄板側に振れる」
                  ※ 11R も prime bonus が付くが 12R bonus は付かない
              + L4 1c80 (1号艇1コース 1着率 80%+)
              + L4 PRO (ベテラン × ST × 展示)
              + L4++ (国1%≥7 + 地1%≥9)
              + 1号艇国1%≥7 のみ満たす L4+ (中間)

            戻り値: (score 1-6, label) — 鉄板度マーク "★×N" + 評価名
            """
            level = base.get("level", "")
            is_high_grade = level in ("SG", "G1", "G2")
            is_f1 = bool(base.get("is_f1"))
            is_prime_r = race_no in (11, 12)
            is_final_r = race_no == 12
            is_1c80 = bool(base.get("is_1c80"))
            is_pro  = bool(base.get("is_l4_pro"))
            rank    = base.get("rank")
            is_plus_plus = rank == "plus_plus"
            is_plus      = rank == "plus"

            score = 0
            if is_high_grade:    score += 1
            if is_f1:            score += 1
            if is_prime_r:       score += 1
            if is_final_r:       score += 1   # 12R は更に +1 (prime と合わせて +2)
            if is_1c80:          score += 1
            if is_pro:           score += 1
            if is_plus_plus:     score += 1
            elif is_plus:        score += 0   # plus 単独は弱いので加点しない

            # スコアを 1-5 の星に圧縮
            stars = 1
            if score >= 5: stars = 5
            elif score >= 4: stars = 4
            elif score >= 3: stars = 3
            elif score >= 2: stars = 2
            else: stars = 1

            # ラベル (backlog item 9: ダイヤモンド廃止 → ★×N 表記に統一)
            if stars >= 5:
                lab = f"鉄板 {stars}★"
            elif stars == 4:
                lab = f"強推 {stars}★"
            elif stars == 3:
                lab = f"推奨 {stars}★"
            elif stars == 2:
                lab = f"候補 {stars}★"
            else:
                lab = f"通常 {stars}★"
            return stars, lab

        def _evaluate_l4_portfolio_strong(stadium, grade, cls, natl_1=None,
                                          avg_st=None, age=None,
                                          boat2_motor_top2=None,
                                          race_number=None, wind_speed=None,
                                          n_female=0, target_date_iso=None):
            """10年検証で年別150%以上だったL4派生ポートフォリオ。

            確定オッズや払戻では絞らず、レース前に分かる条件だけで強監視する。
            検証: 2016-06-13〜2026-06-13、N=96、的中35、回収率312.5%。
            ただし年別Nは薄いため、実弾本命ではなく「強バッジ」扱い。
            """
            try:
                rn = int(race_number) if race_number is not None else 0
            except (TypeError, ValueError):
                rn = 0
            try:
                n1 = float(natl_1) if natl_1 is not None else 0.0
            except (TypeError, ValueError):
                n1 = 0.0
            try:
                st = float(avg_st) if avg_st is not None else 9.99
            except (TypeError, ValueError):
                st = 9.99
            try:
                a = int(age) if age is not None else 0
            except (TypeError, ValueError):
                a = 0
            try:
                m2 = float(boat2_motor_top2) if boat2_motor_top2 is not None else 0.0
            except (TypeError, ValueError):
                m2 = 0.0
            try:
                wind = int(wind_speed) if wind_speed is not None else None
            except (TypeError, ValueError):
                wind = None
            try:
                month = int(str(target_date_iso)[5:7]) if target_date_iso else 0
            except (TypeError, ValueError):
                month = 0

            no_female = not n_female
            n1_75_85 = 7.5 <= n1 < 8.5
            m2_ge45 = m2 >= 45.0
            highgrade_or_f1 = (
                grade in (1, 2, 3, 4)
                or (grade == 5 and n1 >= 7.0 and m2 >= 40.0)
            )
            if not (highgrade_or_f1 and no_female and n1_75_85 and m2_ge45):
                return None

            tag_a_venues = {1, 5, 6, 9, 11, 12, 13, 16, 17, 18, 23}
            tag_b_venues = {5, 12, 13}
            tag_b_months = {2, 5, 6, 11, 12}
            wind_le3_unknown = wind is None or wind <= 3
            wind_not2_unknown = wind is None or wind != 2

            tag_a = (
                9 <= rn <= 11
                and st < 0.160
                and 40 <= a <= 49
                and wind_le3_unknown
                and stadium in tag_a_venues
            )
            tag_b = (
                7 <= rn <= 12
                and st < 0.165
                and 30 <= a <= 49
                and wind_not2_unknown
                and stadium in tag_b_venues
                and month in tag_b_months
            )
            if not (tag_a or tag_b):
                return None

            tag_label = "A+B" if tag_a and tag_b else ("A" if tag_a else "B")
            return {
                "level": "portfolio_strong",
                "label": "🔥強バッジ",
                "recovery": 312.5,
                "bet": "3連単 1-2-3",
                "n": 96,
                "rank": "portfolio",
                "rank_label": f"強監視 {tag_label}",
                "rank_emoji": "🔥",
                "natl_1": natl_1,
                "local_1": None,
                "is_portfolio_strong": True,
                "portfolio_tag": tag_label,
                "portfolio_recovery": 312.5,
                "portfolio_n": 96,
                "portfolio_hit_rate": 36.5,
                "portfolio_note": "10年検証で年別150%以上。ただし年別Nは薄いので強監視。",
                "tetsuban_score": 5,
                "tetsuban_label": "強監視 5★",
            }

        def _apply_l4_portfolio_strong(base, portfolio):
            if not portfolio:
                return base
            if base is None:
                return portfolio
            base["is_portfolio_strong"] = True
            base["portfolio_tag"] = portfolio["portfolio_tag"]
            base["portfolio_recovery"] = portfolio["portfolio_recovery"]
            base["portfolio_n"] = portfolio["portfolio_n"]
            base["portfolio_hit_rate"] = portfolio["portfolio_hit_rate"]
            base["portfolio_note"] = portfolio["portfolio_note"]
            base["portfolio_label"] = portfolio["label"]
            if not base.get("is_exhibition_st_out"):
                base["is_reference"] = False
            if (base.get("tetsuban_score") or 0) < 5:
                base["tetsuban_score"] = 5
                base["tetsuban_label"] = "強監視 5★"
            return base


        def _evaluate_l4_general_200(stadium, grade, cls, natl_1=None,
                                     boat2_top2=None, boat2_exhibition_time=None,
                                     boat3_exhibition_time=None, ex_st=None):
            """L4 general-race strengthened watch.

            Backtest memo (2025-02-01 to 2026-06-24):
              general + B-excluded + boat1 A1 + 1-2-3 payout 500-999
              + boat1 national win >= 7% + boat2 top2 >= 40%
              + boat2 exhibition time faster than boat3
              => n=164, hit=36.0%, recovery=251.6%.
              + boat1 exhibition ST < 0.18 => n=150, hit=37.3%, recovery=259.3%.
            """
            b_excluded = stadium not in EXCLUDE_B if stadium is not None else False
            if not (grade == 5 and cls == 1 and b_excluded):
                return None
            try:
                n1 = float(natl_1) if natl_1 is not None else 0.0
            except (TypeError, ValueError):
                n1 = 0.0
            try:
                b2 = float(boat2_top2) if boat2_top2 is not None else 0.0
            except (TypeError, ValueError):
                b2 = 0.0
            if n1 < 7.0 or b2 < 40.0:
                return None

            b2_ex = None
            b3_ex = None
            try:
                if boat2_exhibition_time is not None:
                    b2_ex = float(boat2_exhibition_time)
                if boat3_exhibition_time is not None:
                    b3_ex = float(boat3_exhibition_time)
            except (TypeError, ValueError):
                b2_ex = b3_ex = None
            has_exhibition = b2_ex is not None and b3_ex is not None
            b2_faster = bool(has_exhibition and b2_ex < b3_ex)
            try:
                ex_st_f = float(ex_st) if ex_st is not None else None
            except (TypeError, ValueError):
                ex_st_f = None
            ex_st_good = ex_st_f is not None and ex_st_f < 0.18

            if has_exhibition and not b2_faster:
                return None

            active = b2_faster
            recovery = 259.3 if ex_st_good else 251.6
            n = 150 if ex_st_good else 164
            hit_rate = 37.3 if ex_st_good else 36.0
            level = "l4_general_200" if active else "morning_watch_l4_general_200"
            label = "L4一般200" if active else "朝監視 L4一般200"
            return {
                "level": level,
                "label": label,
                "recovery": recovery,
                "bet": "3連単 1-2-3",
                "n": n,
                "rank": "general200",
                "rank_label": "一般200",
                "rank_emoji": "強",
                "natl_1": natl_1,
                "local_1": None,
                "is_l4_general_200": True,
                "is_morning": not active,
                "is_morning_watch": not active,
                "is_reference": False,
                "general200_hit_rate": hit_rate,
                "general200_recovery": recovery,
                "general200_n": n,
                "general200_boat2_top2": b2,
                "general200_boat2_exhibition_time": b2_ex,
                "general200_boat3_exhibition_time": b3_ex,
                "general200_boat2_faster": b2_faster,
                "general200_ex_st": ex_st_f,
                "general200_ex_st_good": ex_st_good,
                "tetsuban_score": 5 if active else 4,
                "tetsuban_label": "一般200 強" if active else "一般200 監視",
            }

        def _apply_l4_general_200(base, general200):
            if not general200:
                return base
            if base is None:
                return general200
            base["is_l4_general_200"] = True
            base["general200_hit_rate"] = general200["general200_hit_rate"]
            base["general200_recovery"] = general200["general200_recovery"]
            base["general200_n"] = general200["general200_n"]
            base["general200_boat2_top2"] = general200["general200_boat2_top2"]
            base["general200_boat2_exhibition_time"] = general200["general200_boat2_exhibition_time"]
            base["general200_boat3_exhibition_time"] = general200["general200_boat3_exhibition_time"]
            base["general200_boat2_faster"] = general200["general200_boat2_faster"]
            base["general200_ex_st"] = general200["general200_ex_st"]
            base["general200_ex_st_good"] = general200["general200_ex_st_good"]
            base["recovery"] = general200["recovery"]
            base["n"] = general200["n"]
            base["level"] = general200["level"]
            base["label"] = general200["label"]
            base["rank"] = general200["rank"]
            base["rank_label"] = general200["rank_label"]
            base["is_reference"] = False
            base["is_morning_watch"] = general200["is_morning_watch"]
            if (base.get("tetsuban_score") or 0) < general200["tetsuban_score"]:
                base["tetsuban_score"] = general200["tetsuban_score"]
                base["tetsuban_label"] = general200["tetsuban_label"]
            return base

        def _evaluate_candidate_134_signal(
            stadium,
            grade,
            race_number,
            natl_1=None,
            age=None,
            course1=None,
            boat2_motor_top2=None,
            avg_st=None,
            avg_st_n=None,
            weather=None,
            n_female=0,
            target_date_iso=None,
        ):
            try:
                month = int(str(target_date_iso)[5:7]) if target_date_iso else 0
            except (TypeError, ValueError):
                month = 0
            try:
                rn = int(race_number) if race_number is not None else 0
            except (TypeError, ValueError):
                rn = 0
            try:
                n1 = float(natl_1) if natl_1 is not None else 0.0
            except (TypeError, ValueError):
                n1 = 0.0
            try:
                a1 = int(age) if age is not None else None
            except (TypeError, ValueError):
                a1 = None
            try:
                c1 = int(course1) if course1 is not None else 0
            except (TypeError, ValueError):
                c1 = 0
            try:
                m2 = float(boat2_motor_top2) if boat2_motor_top2 is not None else 0.0
            except (TypeError, ValueError):
                m2 = 0.0
            try:
                dst1 = float(avg_st) if avg_st is not None else None
            except (TypeError, ValueError):
                dst1 = None
            try:
                dstn1 = int(avg_st_n) if avg_st_n is not None else 0
            except (TypeError, ValueError):
                dstn1 = 0

            female_count = int(n_female or 0)
            cand1 = (
                female_count == 0
                and c1 in (1, 2)
                and stadium in (5, 12, 13)
                and month in (2, 5, 6, 11, 12)
                and 7.5 <= n1 < 8.5
                and a1 is not None and 40 <= a1 <= 49
            )
            cand3 = (
                female_count == 0
                and c1 in (1, 2)
                and stadium in (1, 5, 6, 9, 11, 12, 13, 16, 17, 18, 23)
                and 10 <= rn <= 12
                and 7.5 <= n1 < 8.5
                and a1 is not None and 40 <= a1 <= 49
                and m2 >= 45.0
            )
            highgrade_or_f1 = grade in (1, 2, 3, 4) or (grade == 5 and n1 >= 7.0 and m2 >= 40.0)
            cand4 = (
                female_count == 0
                and highgrade_or_f1
                and 9 <= rn <= 11
                and dst1 is not None and dst1 < 0.160
                and dstn1 >= 6
                and a1 is not None and 40 <= a1 <= 49
                and 7.5 <= n1 < 8.5
                and m2 >= 50.0
                and weather != 3
                and stadium in (1, 5, 6, 9, 11, 12, 13, 16, 17, 18, 23)
            )
            matched = []
            if cand1:
                matched.append(("cand1", "候補1", 204.0, 1189))
            if cand3:
                matched.append(("cand3", "候補3", 259.3, 150))
            if cand4:
                matched.append(("cand4", "候補4", 293.3, 9))
            if not matched:
                return None
            primary = matched[-1]
            return {
                "level": primary[0],
                "label": primary[1],
                "recovery": primary[2],
                "bet": "3連単 1-2-3",
                "n": primary[3],
                "rank": primary[0],
                "rank_label": primary[1],
                "natl_1": natl_1,
                "local_1": None,
                "is_reference": False,
                "candidate_keys": [m[0] for m in matched],
                "candidate_labels": [m[1] for m in matched],
                "tetsuban_score": 5 if primary[0] == "cand4" else (4 if primary[0] == "cand3" else 3),
                "tetsuban_label": primary[1],
            }

        def _pick_best_market_signal(*signals):
            valid = []
            best = None
            best_recovery = float("-inf")
            for sig in signals:
                if not sig:
                    continue
                valid.append(sig)
                try:
                    rec = float(sig.get("recovery")) if sig.get("recovery") is not None else float("-inf")
                except (TypeError, ValueError):
                    rec = float("-inf")
                if best is None or rec > best_recovery:
                    best = sig
                    best_recovery = rec
            if best is None:
                return None
            merged = dict(best)
            merged["matched_levels"] = [s.get("level") for s in valid if s.get("level")]
            merged["matched_labels"] = [s.get("label") for s in valid if s.get("label")]
            merged["matched_bets"] = [s.get("bet") for s in valid if s.get("bet")]
            merged["matched_recoveries"] = [s.get("recovery") for s in valid if s.get("recovery") is not None]
            return merged

        def _prefer_adopted_signal_over_general200(selected, adopted):
            """Keep adopted strategy labels visible when general200 also matches.

            `l4_general_200` is a useful overlay, but it is not part of the
            current adopted-strategy set shown in ROI pages. When both match the
            same race, prefer the adopted strategy for consistency between the
            ROI dashboard and the live "ROIが高いレース" list.
            """
            if not selected or not adopted:
                return selected
            selected_level = str(selected.get("level") or "")
            if selected_level not in {"l4_general_200", "morning_watch_l4_general_200"}:
                return selected

            merged = dict(adopted)
            matched_levels = []
            for level in (
                *(selected.get("matched_levels") or []),
                selected.get("level"),
                *(adopted.get("matched_levels") or []),
                adopted.get("level"),
            ):
                if level and level not in matched_levels:
                    matched_levels.append(level)
            matched_labels = []
            for label in (
                *(selected.get("matched_labels") or []),
                selected.get("label"),
                *(adopted.get("matched_labels") or []),
                adopted.get("label"),
            ):
                if label and label not in matched_labels:
                    matched_labels.append(label)
            matched_bets = []
            for bet in (
                *(selected.get("matched_bets") or []),
                selected.get("bet"),
                *(adopted.get("matched_bets") or []),
                adopted.get("bet"),
            ):
                if bet and bet not in matched_bets:
                    matched_bets.append(bet)
            matched_recoveries = []
            for recovery in (
                *(selected.get("matched_recoveries") or []),
                selected.get("recovery"),
                *(adopted.get("matched_recoveries") or []),
                adopted.get("recovery"),
            ):
                if recovery is not None and recovery not in matched_recoveries:
                    matched_recoveries.append(recovery)

            merged["matched_levels"] = matched_levels
            merged["matched_labels"] = matched_labels
            merged["matched_bets"] = matched_bets
            merged["matched_recoveries"] = matched_recoveries
            merged["is_l4_general_200"] = bool(selected.get("is_l4_general_200"))
            merged["general200_hit_rate"] = selected.get("general200_hit_rate")
            merged["general200_recovery"] = selected.get("general200_recovery")
            merged["general200_n"] = selected.get("general200_n")
            merged["general200_boat2_top2"] = selected.get("general200_boat2_top2")
            merged["general200_boat2_exhibition_time"] = selected.get("general200_boat2_exhibition_time")
            merged["general200_boat3_exhibition_time"] = selected.get("general200_boat3_exhibition_time")
            merged["general200_boat2_faster"] = selected.get("general200_boat2_faster")
            merged["general200_ex_st"] = selected.get("general200_ex_st")
            merged["general200_ex_st_good"] = selected.get("general200_ex_st_good")
            return merged

        def _evaluate_boat3_trifecta_niche(ctx: dict | None):
            if not ctx:
                return None

            def _to_float(value):
                try:
                    return float(value) if value is not None else None
                except (TypeError, ValueError):
                    return None

            def _to_int(value):
                try:
                    return int(value) if value is not None else None
                except (TypeError, ValueError):
                    return None

            stadium = _to_int(ctx.get("stadium"))
            race_number = _to_int(ctx.get("race_number"))
            motor_top2 = _to_float(ctx.get("boat3_motor_top2"))
            ex_st = _to_float(ctx.get("boat3_ex_st"))
            ex_time = _to_float(ctx.get("boat3_exhibition_time"))
            best_ex = _to_float(ctx.get("best_exhibition_time"))
            same_day_avg = _to_float(ctx.get("same_day_boat3_avg_ex"))
            venue_avg = _to_float(ctx.get("venue_boat3_avg_ex"))

            if None in (stadium, race_number, motor_top2, ex_st, ex_time):
                return None

            same_day_gain = (same_day_avg - ex_time) if same_day_avg is not None else None
            venue_gain = (venue_avg - ex_time) if venue_avg is not None else None
            fastest_diff = (ex_time - best_ex) if best_ex is not None else None

            matched = []
            if motor_top2 >= 40.0 and ex_st <= 0.06 and same_day_gain is not None and same_day_gain >= 0.08:
                matched.append({
                    "level": "trifecta_niche_324_core",
                    "label": "3号艇まくり型 3-2-4",
                    "bet": "3連単 3-2-4",
                    "rank": "trifecta_niche",
                    "rank_label": "3連単ニッチ",
                    "recovery": 2316.9,
                    "n": 13,
                    "hit_rate": 30.8,
                    "tag": "motor_ge40 + st_le006 + same_day_le008",
                    "name": "324型",
                    "tetsuban_score": 10,
                })
            if motor_top2 >= 35.0 and ex_st <= 0.08 and same_day_gain is not None and same_day_gain >= 0.08:
                matched.append({
                    "level": "trifecta_niche_321_core",
                    "label": "3号艇まくり型 3-2-1",
                    "bet": "3連単 3-2-1",
                    "rank": "trifecta_niche",
                    "rank_label": "3連単ニッチ",
                    "recovery": 1518.7,
                    "n": 23,
                    "hit_rate": 34.8,
                    "tag": "motor_ge35 + st_le008 + same_day_le008",
                    "name": "321型",
                    "tetsuban_score": 8,
                })
            if (
                motor_top2 >= 35.0
                and ex_st <= 0.08
                and same_day_gain is not None and same_day_gain >= 0.08
                and fastest_diff is not None and fastest_diff <= 0.08
            ):
                matched.append({
                    "level": "trifecta_niche_321_boost",
                    "label": "3号艇まくり型 強化 3-2-1",
                    "bet": "3連単 3-2-1",
                    "rank": "trifecta_niche",
                    "rank_label": "3連単ニッチ",
                    "recovery": 1587.7,
                    "n": 22,
                    "hit_rate": 36.4,
                    "tag": "motor_ge35 + st_le008 + fastest_le008 + same_day_le008",
                    "name": "321強化型",
                    "tetsuban_score": 9,
                })
            if ex_st <= 0.06 and same_day_gain is not None and same_day_gain >= 0.08 and venue_gain is not None and venue_gain >= 0.08:
                matched.append({
                    "level": "trifecta_niche_324_venue",
                    "label": "3号艇まくり型 会場補正 3-2-4",
                    "bet": "3連単 3-2-4",
                    "rank": "trifecta_niche",
                    "rank_label": "3連単ニッチ",
                    "recovery": 1617.6,
                    "n": 21,
                    "hit_rate": 19.0,
                    "tag": "st_le006 + same_day_le008 + venue_le008",
                    "name": "324会場補正型",
                    "tetsuban_score": 8,
                })

            best = _pick_best_market_signal(*matched)
            if not best:
                return None

            return {
                **best,
                "natl_1": None,
                "local_1": None,
                "is_reference": False,
                "is_trifecta_niche": True,
                "trifecta_niche_name": best["name"],
                "trifecta_niche_tag": best["tag"],
                "trifecta_niche_hit_rate": best["hit_rate"],
                "trifecta_niche_recovery": best["recovery"],
                "boat3_niche_stadium": stadium,
                "boat3_niche_race_number": race_number,
                "boat3_niche_motor_top2": round(motor_top2, 1),
                "boat3_niche_ex_st": round(ex_st, 3),
                "boat3_niche_same_day_gain": round(same_day_gain, 3) if same_day_gain is not None else None,
                "boat3_niche_fastest_diff": round(fastest_diff, 3) if fastest_diff is not None else None,
                "boat3_niche_venue_gain": round(venue_gain, 3) if venue_gain is not None else None,
                "is_display_confirmed": True,
                "tetsuban_label": best["name"],
            }

        def _evaluate_boat3_trifecta_niche_watch(ctx: dict | None):
            if not ctx:
                return None

            def _to_float(value):
                try:
                    return float(value) if value is not None else None
                except (TypeError, ValueError):
                    return None

            motor_top2 = _to_float(ctx.get("boat3_motor_top2"))
            ex_st = _to_float(ctx.get("boat3_ex_st"))
            same_day_gain = _to_float(ctx.get("boat3_same_day_gain"))
            fastest_diff = _to_float(ctx.get("boat3_fastest_diff"))
            venue_gain = _to_float(ctx.get("boat3_venue_gain"))
            if motor_top2 is None:
                return None

            matched = []
            if motor_top2 >= 40.0:
                matched.append({
                    "label": "朝監視 3-2-4",
                    "bet": "3連単 3-2-4",
                    "recovery": 2316.9,
                    "n": 13,
                })
            if motor_top2 >= 35.0:
                matched.append({
                    "label": "朝監視 3-2-1",
                    "bet": "3連単 3-2-1",
                    "recovery": 1518.7,
                    "n": 23,
                })
            if not matched:
                return None

            best = max(matched, key=lambda x: x.get("recovery", 0))
            has_exhibition = any(v is not None for v in (ex_st, same_day_gain, fastest_diff, venue_gain))
            qualifies_324 = (
                motor_top2 >= 40.0
                and ex_st is not None and ex_st <= 0.06
                and same_day_gain is not None and same_day_gain >= 0.08
            )
            qualifies_321 = (
                motor_top2 >= 35.0
                and ex_st is not None and ex_st <= 0.08
                and same_day_gain is not None and same_day_gain >= 0.08
            )
            qualifies_321_boost = qualifies_321 and fastest_diff is not None and fastest_diff <= 0.08
            qualifies_324_venue = (
                motor_top2 >= 40.0
                and ex_st is not None and ex_st <= 0.06
                and same_day_gain is not None and same_day_gain >= 0.08
                and venue_gain is not None and venue_gain >= 0.08
            )
            display_confirmed = qualifies_324 or qualifies_321 or qualifies_321_boost or qualifies_324_venue
            return {
                "level": "morning_watch_boat3_multi",
                "label": best["label"],
                "recovery": best["recovery"],
                "n": best["n"],
                "bet": best["bet"],
                "rank": "trifecta_niche",
                "rank_label": "朝監視",
                "rank_emoji": "👀",
                "is_reference": True,
                "is_morning": True,
                "is_morning_watch": True,
                "watch_strategy_labels": [m["label"] for m in matched],
                "watch_strategy_bets": [m["bet"] for m in matched],
                "watch_strategy_count": len(matched),
                "is_display_confirmed": display_confirmed,
                "is_after_exhibition_out": has_exhibition and not display_confirmed,
                "tetsuban_score": 4 if len(matched) == 1 else 5,
                "tetsuban_label": "朝監視 3-2",
            }

        def _evaluate_tokoname_12_exacta(ctx: dict | None):
            if not ctx:
                return None

            def _to_float(value):
                try:
                    return float(value) if value is not None else None
                except (TypeError, ValueError):
                    return None

            def _to_int(value):
                try:
                    return int(value) if value is not None else None
                except (TypeError, ValueError):
                    return None

            stadium = _to_int(ctx.get("stadium"))
            n_female = _to_int(ctx.get("n_female")) or 0
            boat2_top2 = _to_float(ctx.get("boat2_top2"))
            boat2_ex_st = _to_float(ctx.get("ex_st"))
            boat2_ex_time = _to_float(ctx.get("boat2_exhibition_time"))
            same_day_avg = _to_float(ctx.get("boat2_same_day_avg_ex"))
            venue_avg = _to_float(ctx.get("boat2_venue_avg_ex"))
            course1_win_rate = _to_float(ctx.get("boat1_course1_win_rate"))
            escape_share = _to_float(ctx.get("boat1_escape_share"))
            ex_times = {
                boat_no: _to_float(ctx.get(f"boat{boat_no}_exhibition_time"))
                for boat_no in range(1, 7)
            }

            if not (
                stadium == 8
                and n_female == 0
                and boat2_top2 is not None and boat2_top2 >= 25.0
                and course1_win_rate is not None and course1_win_rate >= 0.55
                and escape_share is not None and escape_share >= 0.35
                and boat2_ex_st is not None and boat2_ex_st <= 0.10
                and boat2_ex_time is not None and boat2_ex_time > 0
            ):
                return None

            valid = [v for v in ex_times.values() if v is not None and v > 0]
            if not valid:
                return None
            ex_rank2 = 1 + sum(1 for v in valid if v < boat2_ex_time)
            fastest_diff2 = boat2_ex_time - min(valid)
            same_day_gain = (same_day_avg - boat2_ex_time) if same_day_avg is not None else None
            venue_gain = (venue_avg - boat2_ex_time) if venue_avg is not None else None
            any_gap = max(
                same_day_gain if same_day_gain is not None else float("-inf"),
                venue_gain if venue_gain is not None else float("-inf"),
            )
            if ex_rank2 > 1 or fastest_diff2 > 0.05 or any_gap < 0.08:
                return None

            return {
                "level": "tokoname_12_exa",
                "label": "常滑 1-2",
                "recovery": 164.8,
                "n": 25,
                "bet": "2連単 1-2",
                "rank": "exacta_niche",
                "rank_label": "2連単ニッチ",
                "rank_emoji": "2連",
                "natl_1": None,
                "local_1": None,
                "is_reference": False,
                "is_exacta_niche": True,
                "exacta_niche_tag": "常滑 + 1コース勝率55%+ + 逃げshare35%+ + 2号艇2連対率25%+ + 展示ST0.10以下 + 展示最速級",
                "exacta_niche_hit_rate": 44.0,
                "exacta_niche_recovery": 164.8,
                "tokoname_12_same_day_gain": round(same_day_gain, 3) if same_day_gain is not None else None,
                "tokoname_12_venue_gain": round(venue_gain, 3) if venue_gain is not None else None,
                "tokoname_12_course1_win_rate": round(course1_win_rate * 100, 1),
                "tokoname_12_escape_share": round(escape_share * 100, 1),
                "tokoname_12_ex_rank": ex_rank2,
                "tokoname_12_fastest_diff": round(fastest_diff2, 3),
                "is_display_confirmed": True,
                "tetsuban_score": 6,
                "tetsuban_label": "常滑12",
            }

        def _evaluate_tokoname_12_exacta_watch(ctx: dict | None):
            if not ctx:
                return None

            def _to_float(value):
                try:
                    return float(value) if value is not None else None
                except (TypeError, ValueError):
                    return None

            def _to_int(value):
                try:
                    return int(value) if value is not None else None
                except (TypeError, ValueError):
                    return None

            stadium = _to_int(ctx.get("stadium"))
            n_female = _to_int(ctx.get("n_female")) or 0
            boat2_top2 = _to_float(ctx.get("boat2_top2"))
            course1_win_rate = _to_float(ctx.get("boat1_course1_win_rate"))
            escape_share = _to_float(ctx.get("boat1_escape_share"))
            boat2_ex_st = _to_float(ctx.get("ex_st"))
            boat2_ex_time = _to_float(ctx.get("boat2_exhibition_time"))
            same_day_avg = _to_float(ctx.get("boat2_same_day_avg_ex"))
            venue_avg = _to_float(ctx.get("boat2_venue_avg_ex"))
            ex_times = {
                boat_no: _to_float(ctx.get(f"boat{boat_no}_exhibition_time"))
                for boat_no in range(1, 7)
            }

            if not (
                stadium == 8
                and n_female == 0
                and boat2_top2 is not None and boat2_top2 >= 25.0
                and course1_win_rate is not None and course1_win_rate >= 0.55
                and escape_share is not None and escape_share >= 0.35
            ):
                return None

            has_exhibition = any(v is not None for v in [boat2_ex_st, boat2_ex_time, same_day_avg, venue_avg, *ex_times.values()])
            display_confirmed = False
            if boat2_ex_st is not None and boat2_ex_time is not None and boat2_ex_time > 0:
                valid = [v for v in ex_times.values() if v is not None and v > 0]
                if valid:
                    ex_rank2 = 1 + sum(1 for v in valid if v < boat2_ex_time)
                    fastest_diff2 = boat2_ex_time - min(valid)
                    same_day_gain = (same_day_avg - boat2_ex_time) if same_day_avg is not None else None
                    venue_gain = (venue_avg - boat2_ex_time) if venue_avg is not None else None
                    any_gap = max(
                        same_day_gain if same_day_gain is not None else float("-inf"),
                        venue_gain if venue_gain is not None else float("-inf"),
                    )
                    display_confirmed = (
                        boat2_ex_st <= 0.10
                        and ex_rank2 <= 1
                        and fastest_diff2 <= 0.05
                        and any_gap >= 0.08
                    )

            return {
                "level": "morning_watch_tokoname_12_exa",
                "label": "朝監視 常滑1-2",
                "recovery": 164.8,
                "n": 25,
                "bet": "2連単 1-2",
                "rank": "exacta_niche",
                "rank_label": "朝監視",
                "rank_emoji": "朝監視",
                "is_reference": True,
                "is_morning": True,
                "is_morning_watch": True,
                "is_exacta_niche": True,
                "watch_strategy_labels": ["朝監視 常滑1-2"],
                "watch_strategy_bets": ["2連単 1-2"],
                "watch_strategy_count": 1,
                "is_display_confirmed": display_confirmed,
                "is_after_exhibition_out": has_exhibition and not display_confirmed,
                "tetsuban_score": 4,
                "tetsuban_label": "朝監視 常滑12",
            }

        def _evaluate_tri124_132_trifecta_niche(ctx: dict | None):
            if not ctx:
                return None

            def _to_float(value):
                try:
                    return float(value) if value is not None else None
                except (TypeError, ValueError):
                    return None

            def _to_int(value):
                try:
                    return int(value) if value is not None else None
                except (TypeError, ValueError):
                    return None

            cls = _to_int(ctx.get("class"))
            stadium = _to_int(ctx.get("stadium"))
            if stadium is None:
                stadium = _to_int(ctx.get("stadium_number"))
            weather = _to_int(ctx.get("weather"))
            n_female = _to_int(ctx.get("n_female")) or 0
            race_no = _to_int(ctx.get("race_number"))
            boat4_natl_1 = _to_float(ctx.get("boat4_natl_1"))
            boat4_motor_top2 = _to_float(ctx.get("boat4_motor_top2"))
            boat4_ex_st = _to_float(ctx.get("boat4_ex_st"))
            ex_times = {
                boat_no: _to_float(ctx.get(f"boat{boat_no}_exhibition_time"))
                for boat_no in range(1, 7)
            }

            if cls not in (1, 2) or weather == 3 or n_female != 0:
                return None

            def _ex_rank(boat_no: int) -> float | None:
                target = ex_times.get(boat_no)
                if target is None or target <= 0:
                    return None
                valid = [v for v in ex_times.values() if v is not None and v > 0]
                if not valid:
                    return None
                return 1 + sum(1 for v in valid if v < target)

            def _fastest_diff(boat_no: int) -> float | None:
                target = ex_times.get(boat_no)
                valid = [v for v in ex_times.values() if v is not None and v > 0]
                if target is None or target <= 0 or not valid:
                    return None
                return target - min(valid)

            if (
                stadium == 13
                and race_no is not None and 10 <= race_no <= 12
                and None not in (boat4_natl_1, boat4_motor_top2, boat4_ex_st)
                and boat4_natl_1 >= 5.0
                and boat4_motor_top2 >= 35.0
                and (_ex_rank(4) or 99) == 1
                and (_ex_rank(1) or 99) <= 3
                and boat4_ex_st <= 0.10
                and (_fastest_diff(4) or 99) <= 0.05
            ):
                return {
                    "level": "trifecta_niche_143_a12",
                    "label": "尼崎 1-4-3",
                    "bet": "三連単 1-4-3",
                    "rank": "trifecta_niche",
                    "rank_label": "三連単ニッチ",
                    "recovery": 347.5,
                    "n": 20,
                    "hit_rate": 25.0,
                    "tag": "尼崎10-12R + 4号艇全国1着率>=5 + 4号艇モーター>=35 + 展示4=1位 + 展示ST4<=0.10",
                    "name": "尼崎143",
                    "tetsuban_score": 8,
                    "natl_1": None,
                    "local_1": None,
                    "is_reference": False,
                    "is_trifecta_niche": True,
                    "trifecta_niche_name": "尼崎143",
                    "trifecta_niche_tag": "尼崎10-12R + 4号艇全国1着率>=5 + 4号艇モーター>=35 + 展示4=1位 + 展示ST4<=0.10",
                    "trifecta_niche_hit_rate": 25.0,
                    "trifecta_niche_recovery": 347.5,
                    "is_display_confirmed": True,
                    "tetsuban_label": "尼崎143",
                }

            return None

        def _evaluate_tri124_132_trifecta_niche_watch(ctx: dict | None):
            if not ctx:
                return None

            def _to_float(value):
                try:
                    return float(value) if value is not None else None
                except (TypeError, ValueError):
                    return None

            def _to_int(value):
                try:
                    return int(value) if value is not None else None
                except (TypeError, ValueError):
                    return None

            cls = _to_int(ctx.get("class"))
            stadium = _to_int(ctx.get("stadium"))
            if stadium is None:
                stadium = _to_int(ctx.get("stadium_number"))
            weather = _to_int(ctx.get("weather"))
            n_female = _to_int(ctx.get("n_female")) or 0
            race_no = _to_int(ctx.get("race_number"))
            boat4_natl_1 = _to_float(ctx.get("boat4_natl_1"))
            boat4_motor_top2 = _to_float(ctx.get("boat4_motor_top2"))
            boat4_ex_time = _to_float(ctx.get("boat4_exhibition_time"))
            boat4_ex_st = _to_float(ctx.get("boat4_ex_st"))
            ex_times = {
                boat_no: _to_float(ctx.get(f"boat{boat_no}_exhibition_time"))
                for boat_no in range(1, 7)
            }

            if cls not in (1, 2) or weather == 3 or n_female != 0:
                return None

            if not (
                stadium == 13
                and race_no is not None and 10 <= race_no <= 12
                and boat4_natl_1 is not None and boat4_natl_1 >= 5.0
                and boat4_motor_top2 is not None and boat4_motor_top2 >= 35.0
            ):
                return None

            def _ex_rank(boat_no: int) -> float | None:
                target = ex_times.get(boat_no)
                if target is None or target <= 0:
                    return None
                valid = [v for v in ex_times.values() if v is not None and v > 0]
                if not valid:
                    return None
                return 1 + sum(1 for v in valid if v < target)

            has_exhibition = any(v is not None for v in ex_times.values())
            display_confirmed = (
                boat4_ex_time is not None
                and boat4_ex_st is not None and boat4_ex_st <= 0.10
                and (_ex_rank(4) or 99) == 1
                and (_ex_rank(1) or 99) <= 3
            )

            return {
                "level": "morning_watch_tri143_a12",
                "label": "朝監視 尼崎 1-4-3",
                "recovery": 347.5,
                "n": 20,
                "bet": "3連単 1-4-3",
                "rank": "trifecta_niche",
                "rank_label": "朝監視",
                "rank_emoji": "👀",
                "is_reference": True,
                "is_morning": True,
                "is_morning_watch": True,
                "watch_strategy_labels": ["朝監視 尼崎 1-4-3"],
                "watch_strategy_bets": ["3連単 1-4-3"],
                "watch_strategy_count": 1,
                "is_display_confirmed": display_confirmed,
                "is_after_exhibition_out": has_exhibition and not display_confirmed,
                "tetsuban_score": 4,
                "tetsuban_label": "朝監視 尼崎 1-4-3",
            }

        def _evaluate_g23_optb_signal(stadium, grade, cls, min_payout, natl_1=None, local_1=None,
                                     avg_st=None, age=None, ex_st=None, boat2_motor_top2=None,
                                     weather=None, n_female=0):
            return None

        def _evaluate_g23_optb_watch(stadium, grade, cls, natl_1=None, local_1=None,
                                     avg_st=None, age=None, boat2_motor_top2=None,
                                     weather=None, n_female=0):
            try:
                _natl1 = float(natl_1) if natl_1 is not None else 0.0
                _local1 = float(local_1) if local_1 is not None else 0.0
                _avg_st = float(avg_st) if avg_st is not None else None
                _age = int(age) if age is not None else None
                _b2m = float(boat2_motor_top2) if boat2_motor_top2 is not None else 0.0
            except (TypeError, ValueError):
                return None
            g23_caps = {11: 35.0, 12: 35.0, 16: 40.0, 20: 30.0, 22: 35.0, 23: 40.0}
            if not (
                stadium in g23_caps
                and grade in (3, 4)
                and cls == 1
                and n_female == 0
                and weather != 3
                and _natl1 >= 7.0
                and _local1 >= 6.0
                and _age is not None and 30 <= _age <= 49
                and _avg_st is not None and _avg_st < 0.155
                and _b2m <= g23_caps[stadium]
            ):
                return None
            return {
                "level": "g23_optb_tri",
                "label": "G2/G3 1-2-3",
                "recovery": 190.3,
                "n": 126,
                "bet": "3連単 1-2-3",
                "rank": "trifecta_niche",
                "rank_label": "3連単ニッチ",
                "rank_emoji": "👀",
                "is_reference": False,
                "tetsuban_score": 4,
                "tetsuban_label": "G2/G3 1-2-3",
            }

        def _evaluate_fukuoka_wind_exa_signal(stadium, cls, boat2_top2=None, wind_speed=None):
            try:
                _b2 = float(boat2_top2) if boat2_top2 is not None else 0.0
                _wind = float(wind_speed) if wind_speed is not None else None
            except (TypeError, ValueError):
                return None
            if not (
                stadium == 22
                and cls != 1
                and _wind is not None and _wind >= 5.0
                and _b2 >= 35.0
            ):
                return None
            return {
                "level": "fukuoka_wind_exa",
                "label": "?? ?? 2-1",
                "recovery": 165.7,
                "n": 49,
                "bet": "2?? 2-1",
                "rank": "exacta_niche",
                "rank_label": "2?????",
                "rank_emoji": "??",
                "is_reference": False,
                "exacta_niche_name": "?? 2?? 2-1 ???",
                "exacta_niche_recovery": 165.7,
                "exacta_niche_hit_rate": 22.4,
                "tetsuban_score": 5,
                "tetsuban_label": "?? ?? 2-1",
            }

        def _evaluate_general_c_signal(
            stadium,
            grade,
            cls,
            min_payout,
            l4_band_ok=None,
            natl_1=None,
            boat2_top2=None,
            race_number=None,
            weather=None,
            boat2_exhibition_time=None,
            boat3_exhibition_time=None,
            n_female=0,
        ):
            try:
                payout = int(min_payout) if min_payout is not None else None
            except (TypeError, ValueError):
                payout = None
            try:
                n1 = float(natl_1) if natl_1 is not None else 0.0
            except (TypeError, ValueError):
                n1 = 0.0
            try:
                b2 = float(boat2_top2) if boat2_top2 is not None else 0.0
            except (TypeError, ValueError):
                b2 = 0.0
            try:
                rn = int(race_number) if race_number is not None else 0
            except (TypeError, ValueError):
                rn = 0
            try:
                b2_ex = float(boat2_exhibition_time) if boat2_exhibition_time is not None else None
            except (TypeError, ValueError):
                b2_ex = None
            try:
                b3_ex = float(boat3_exhibition_time) if boat3_exhibition_time is not None else None
            except (TypeError, ValueError):
                b3_ex = None

            in_l4_band = False
            if l4_band_ok is True:
                in_l4_band = True
            elif payout is not None:
                # Keep live-list behaviour aligned with daily ROI aggregation:
                # when the precomputed odds-window flag is missing or false, fall
                # back to the latest available payout band for retrospective
                # same-day checks and cache regeneration.
                in_l4_band = 500 <= payout < 1000

            if not (
                grade == 5
                and cls == 1
                and stadium not in EXCLUDE_B_VENUES
                and weather != 3
                and n_female == 0
                and in_l4_band
                and n1 >= 7.0
                and b2 >= 40.0
                and rn in (10, 11, 12)
                and b2_ex is not None and b3_ex is not None and b2_ex <= b3_ex
            ):
                return None

            return {
                "level": "general_c_tri",
                "label": "一般C候補",
                "recovery": 255.3,
                "n": 148,
                "bet": "3連単 1-2-3",
                "rank": "trifecta_niche",
                "rank_label": "一般C",
                "rank_emoji": "候補",
                "natl_1": natl_1,
                "local_1": None,
                "is_reference": False,
                "is_trifecta_niche": True,
                "trifecta_niche_name": "一般C 1-2-3",
                "trifecta_niche_tag": "一般戦 + 10-12R + 1号艇全国1着率7%以上 + 2号艇全国2連対率40%以上 + 展示で2号艇<=3号艇",
                "trifecta_niche_hit_rate": 37.2,
                "trifecta_niche_recovery": 255.3,
                "tetsuban_score": 6,
                "tetsuban_label": "一般C",
            }

        def _evaluate_general_c_watch(
            stadium,
            grade,
            cls,
            natl_1=None,
            boat2_top2=None,
            race_number=None,
            weather=None,
            boat2_exhibition_time=None,
            boat3_exhibition_time=None,
            n_female=0,
        ):
            try:
                n1 = float(natl_1) if natl_1 is not None else 0.0
                b2 = float(boat2_top2) if boat2_top2 is not None else 0.0
                rn = int(race_number) if race_number is not None else 0
                b2_ex = float(boat2_exhibition_time) if boat2_exhibition_time is not None else None
                b3_ex = float(boat3_exhibition_time) if boat3_exhibition_time is not None else None
            except (TypeError, ValueError):
                return None
            if not (
                grade == 5
                and cls == 1
                and stadium not in EXCLUDE_B_VENUES
                and weather != 3
                and n_female == 0
                and n1 >= 7.0
                and b2 >= 40.0
                and rn in (10, 11, 12)
            ):
                return None
            has_exhibition = (b2_ex is not None) or (b3_ex is not None)
            display_confirmed = (
                b2_ex is not None
                and b3_ex is not None
                and b2_ex <= b3_ex
            )
            return {
                "level": "morning_watch_general_c_tri",
                "label": "朝監視 一般C",
                "recovery": 255.3,
                "n": 148,
                "bet": "3連単 1-2-3",
                "rank": "trifecta_niche",
                "rank_label": "朝監視",
                "rank_emoji": "👀",
                "is_reference": True,
                "is_morning": True,
                "is_morning_watch": True,
                "watch_strategy_labels": ["朝監視 一般C"],
                "watch_strategy_bets": ["3連単 1-2-3"],
                "watch_strategy_count": 1,
                "is_display_confirmed": display_confirmed,
                "is_after_exhibition_out": has_exhibition and not display_confirmed,
                "tetsuban_score": 4,
                "tetsuban_label": "朝監視 一般C",
            }

        def _evaluate_omura_123_watch(
            stadium,
            race_number=None,
            boat4_local_1=None,
            boat2_top2=None,
            boat3_top2=None,
            wind_speed=None,
            boat3_ex_st=None,
            boat1_exhibition_time=None,
            best_exhibition_time=None,
        ):
            try:
                rn = int(race_number) if race_number is not None else 0
                b4_local = float(boat4_local_1) if boat4_local_1 is not None else None
                b2 = float(boat2_top2) if boat2_top2 is not None else None
                b3 = float(boat3_top2) if boat3_top2 is not None else None
                wind = float(wind_speed) if wind_speed is not None else None
                b3st = float(boat3_ex_st) if boat3_ex_st is not None else None
                b1ex = float(boat1_exhibition_time) if boat1_exhibition_time is not None else None
                best_ex = float(best_exhibition_time) if best_exhibition_time is not None else None
            except (TypeError, ValueError):
                return None

            if not (
                stadium == 24
                and rn in (10, 11, 12)
                and b4_local is not None
                and b4_local <= 5.0
                and b2 is not None
                and b3 is not None
                and b2 >= b3
            ):
                return None

            # OUT(圏外) へ落とすのは「展示後に必要な展示項目が実際に入った後」だけにする。
            # 風速は展示前から取れているため、これだけで再判定済み扱いにすると
            # 夜レースが朝の時点で圏外表示になってしまう。
            has_followup = any(v is not None for v in (b3st, b1ex, best_ex))
            display_confirmed = (
                wind is not None and wind >= 5.0
                and b3st is not None and b3st <= 0.15
                and b1ex is not None
                and best_ex is not None
                and (b1ex - best_ex) <= 0.10
            )
            return {
                "level": "morning_watch_omura_123_tri",
                "label": "朝監視 大村1-2-3",
                "recovery": 336.8,
                "n": 38,
                "bet": "3連単 1-2-3",
                "rank": "trifecta_niche",
                "rank_label": "朝監視",
                "rank_emoji": "👀",
                "is_reference": True,
                "is_morning": True,
                "is_morning_watch": True,
                "watch_strategy_labels": ["朝監視 大村1-2-3"],
                "watch_strategy_bets": ["3連単 1-2-3"],
                "watch_strategy_count": 1,
                "is_display_confirmed": display_confirmed,
                "is_after_exhibition_out": has_followup and not display_confirmed,
                "tetsuban_score": 4 if not display_confirmed else 5,
                "tetsuban_label": "朝監視 大村123",
            }

        def _evaluate_tide_tri_signal(
            stadium,
            weather=None,
            wind_speed=None,
            wave_height=None,
            tide_delta_60m_cm=None,
            tide_range_cm=None,
            is_high_tide_zone=0,
            is_low_tide_zone=0,
        ):
            try:
                weather_v = int(weather) if weather is not None else None
            except (TypeError, ValueError):
                weather_v = None
            try:
                wind_v = float(wind_speed) if wind_speed is not None else None
            except (TypeError, ValueError):
                wind_v = None
            try:
                wave_v = float(wave_height) if wave_height is not None else None
            except (TypeError, ValueError):
                wave_v = None
            try:
                delta_v = float(tide_delta_60m_cm) if tide_delta_60m_cm is not None else None
            except (TypeError, ValueError):
                delta_v = None
            try:
                range_v = float(tide_range_cm) if tide_range_cm is not None else None
            except (TypeError, ValueError):
                range_v = None

            matched = []
            if (
                stadium == 17
                and wave_v is not None and wave_v >= 5.0
                and wind_v is not None and 3.0 <= wind_v <= 4.0
                and delta_v is not None and delta_v <= -15.0
            ):
                matched.append({
                    "level": "miyajima_tide_132_tri",
                    "label": "宮島 1-3-2 潮型",
                    "bet": "3連単 1-3-2",
                    "rank": "trifecta_niche",
                    "rank_label": "3連単 採用",
                    "recovery": 1242.0,
                    "n": 30,
                    "hit_rate": 40.0,
                    "name": "宮島132 潮型",
                    "tag": "wave5+ + wind3-4 + fall15+",
                    "tetsuban_score": 10,
                })
            if (
                stadium == 7
                and delta_v is not None and -15.0 < delta_v <= -5.0
                and range_v is not None and range_v >= 150.0
                and wind_v is not None and wind_v <= 2.0
            ):
                matched.append({
                    "level": "gamagori_tide_132_tri",
                    "label": "蒲郡 1-3-2 潮型",
                    "bet": "3連単 1-3-2",
                    "rank": "trifecta_niche",
                    "rank_label": "3連単採用",
                    "recovery": 718.6,
                    "n": 132,
                    "hit_rate": 27.3,
                    "name": "蒲郡132 潮型",
                    "tag": "fall5-14 + range150+ + wind0-2",
                    "tetsuban_score": 8,
                })
            if (
                stadium == 15
                and range_v is not None and 90.0 <= range_v < 120.0
                and wind_v is not None and 5.0 <= wind_v <= 6.0
                and weather_v == 1
            ):
                matched.append({
                    "level": "marugame_tide_123_tri",
                    "label": "丸亀 1-2-3 潮型",
                    "bet": "3連単 1-2-3",
                    "rank": "trifecta_niche",
                    "rank_label": "3連単 採用",
                    "recovery": 431.4,
                    "n": 42,
                    "hit_rate": 28.6,
                    "name": "丸亀123 潮型",
                    "tag": "range90-119 + wind5-6 + sun",
                    "tetsuban_score": 7,
                })
            if (
                stadium == 22
                and range_v is not None and range_v >= 150.0
                and delta_v is not None and 5.0 <= delta_v < 15.0
                and wind_v is not None and wind_v <= 2.0
            ):
                matched.append({
                    "level": "fukuoka_tide_132_tri",
                    "label": "福岡 1-3-2 潮型",
                    "bet": "3連単 1-3-2",
                    "rank": "trifecta_niche",
                    "rank_label": "3連単 採用",
                    "recovery": 470.0,
                    "n": 66,
                    "hit_rate": 27.3,
                    "name": "福岡132 潮型",
                    "tag": "range150+ + rise5-14 + wind0-2",
                    "tetsuban_score": 7,
                })

            return _pick_best_market_signal(*matched)

        def _evaluate_gamagori_adopted_signal(ctx: dict | None):
            if not ctx:
                return None

            def _to_float(value):
                try:
                    return float(value) if value is not None else None
                except (TypeError, ValueError):
                    return None

            def _to_int(value):
                try:
                    return int(value) if value is not None else None
                except (TypeError, ValueError):
                    return None

            stadium = _to_int(ctx.get("stadium"))
            if stadium is None:
                stadium = _to_int(ctx.get("stadium_number"))
            grade = _to_int(ctx.get("grade", ctx.get("race_grade_number")))
            cls = _to_int(ctx.get("class", ctx.get("class_number")))
            race_no = _to_int(ctx.get("race_number"))
            weather = _to_int(ctx.get("weather"))
            n_female = _to_int(ctx.get("n_female")) or 0
            natl_1 = _to_float(ctx.get("natl_1"))
            avg_st = _to_float(ctx.get("avg_st_180", ctx.get("avg_st")))
            boat2_top2 = _to_float(ctx.get("boat2_top2"))
            boat3_motor = _to_float(ctx.get("boat3_motor_top2"))
            wind_v = _to_float(ctx.get("wind_speed"))
            delta_v = _to_float(ctx.get("tide_delta_60m_cm"))
            range_v = _to_float(ctx.get("tide_range_cm"))

            if stadium != 7 or grade != 5 or cls != 1 or weather == 3 or n_female != 0:
                return None

            matched = []
            if (
                race_no is not None and race_no in (9, 10, 11, 12)
                and avg_st is not None and avg_st < 0.16
                and boat2_top2 is not None and boat2_top2 >= 35.0
                and delta_v is not None and -15.0 < delta_v <= -5.0
            ):
                matched.append({
                    "level": "gamagori_123_general_practical_tri",
                    "label": "蒲郡 1-2-3 実戦型",
                    "bet": "3連単 1-2-3",
                    "rank": "trifecta_niche",
                    "rank_label": "3連単採用",
                    "recovery": 277.6,
                    "n": 21,
                    "hit_rate": 33.3,
                    "name": "蒲郡123 実戦型",
                    "tag": "一般 + 9-12R + 1号艇180日ST<0.16 + 2号艇全国2連対率>=35 + 60分潮位差(-15,-5]",
                    "tetsuban_score": 7,
                })
            if (
                natl_1 is not None and natl_1 >= 7.0
                and boat3_motor is not None and boat3_motor >= 40.0
                and wind_v is not None and wind_v <= 2.0
                and range_v is not None and range_v >= 150.0
            ):
                matched.append({
                    "level": "gamagori_13_exa",
                    "label": "蒲郡 1-3",
                    "bet": "2連単 1-3",
                    "rank": "exacta_niche",
                    "rank_label": "2連単採用",
                    "recovery": 154.9,
                    "n": 33,
                    "hit_rate": 51.5,
                    "name": "蒲郡13",
                    "tag": "一般 + 1号艇全国1着率>=7 + 3号艇モーター2連率>=40 + 風速<=2 + 潮位レンジ>=150",
                    "tetsuban_score": 5,
                })
            return _pick_best_market_signal(*matched)

        def _evaluate_non_exhibition_core_signal(
            stadium,
            grade,
            cls,
            race_number=None,
            weather=None,
            wind_speed=None,
            n_female=0,
            local_1=None,
            avg_st=None,
            age=None,
            boat1_motor_top2=None,
            boat1_exhibition_time=None,
            boat2_top2=None,
            boat2_motor_top2=None,
            boat3_top2=None,
            boat3_natl_1=None,
            boat3_motor_top2=None,
            boat3_ex_st=None,
            boat4_class=None,
            boat4_local_1=None,
            boat4_motor_top2=None,
            best_exhibition_time=None,
            boat5_motor_top2=None,
            boat2_exhibition_time=None,
            boat3_exhibition_time=None,
            boat1_ex_rank=None,
            boat2_ex_st_rank=None,
        ):
            try:
                rn = int(race_number) if race_number is not None else 0
            except (TypeError, ValueError):
                rn = 0
            try:
                wind_speed_v = float(wind_speed) if wind_speed is not None else 0.0
            except (TypeError, ValueError):
                wind_speed_v = 0.0
            try:
                local_1_v = float(local_1) if local_1 is not None else 0.0
            except (TypeError, ValueError):
                local_1_v = 0.0
            try:
                avg_st_v = float(avg_st) if avg_st is not None else None
            except (TypeError, ValueError):
                avg_st_v = None
            try:
                age_v = int(age) if age is not None else None
            except (TypeError, ValueError):
                age_v = None
            try:
                b1_motor_v = float(boat1_motor_top2) if boat1_motor_top2 is not None else 0.0
            except (TypeError, ValueError):
                b1_motor_v = 0.0
            try:
                b2_top2_v = float(boat2_top2) if boat2_top2 is not None else 0.0
            except (TypeError, ValueError):
                b2_top2_v = 0.0
            try:
                b2_motor_v = float(boat2_motor_top2) if boat2_motor_top2 is not None else 0.0
            except (TypeError, ValueError):
                b2_motor_v = 0.0
            try:
                b3_top2_v = float(boat3_top2) if boat3_top2 is not None else 0.0
            except (TypeError, ValueError):
                b3_top2_v = 0.0
            try:
                b3_n1_v = float(boat3_natl_1) if boat3_natl_1 is not None else 0.0
            except (TypeError, ValueError):
                b3_n1_v = 0.0
            try:
                b3_motor_v = float(boat3_motor_top2) if boat3_motor_top2 is not None else 0.0
            except (TypeError, ValueError):
                b3_motor_v = 0.0
            try:
                b3_ex_st_v = float(boat3_ex_st) if boat3_ex_st is not None else None
            except (TypeError, ValueError):
                b3_ex_st_v = None
            try:
                b4_local_1_v = float(boat4_local_1) if boat4_local_1 is not None else 0.0
            except (TypeError, ValueError):
                b4_local_1_v = 0.0
            try:
                b4_motor_v = float(boat4_motor_top2) if boat4_motor_top2 is not None else 0.0
            except (TypeError, ValueError):
                b4_motor_v = 0.0
            try:
                b1_ex_v = float(boat1_exhibition_time) if boat1_exhibition_time is not None else None
            except (TypeError, ValueError):
                b1_ex_v = None
            try:
                best_ex_v = float(best_exhibition_time) if best_exhibition_time is not None else None
            except (TypeError, ValueError):
                best_ex_v = None
            try:
                b5_motor_v = float(boat5_motor_top2) if boat5_motor_top2 is not None else 0.0
            except (TypeError, ValueError):
                b5_motor_v = 0.0
            try:
                b2_ex_v = float(boat2_exhibition_time) if boat2_exhibition_time is not None else None
            except (TypeError, ValueError):
                b2_ex_v = None
            try:
                b3_ex_v = float(boat3_exhibition_time) if boat3_exhibition_time is not None else None
            except (TypeError, ValueError):
                b3_ex_v = None
            try:
                b1_ex_rank_v = int(boat1_ex_rank) if boat1_ex_rank is not None else None
            except (TypeError, ValueError):
                b1_ex_rank_v = None
            try:
                b2_ex_st_rank_v = int(boat2_ex_st_rank) if boat2_ex_st_rank is not None else None
            except (TypeError, ValueError):
                b2_ex_st_rank_v = None

            if not (
                grade == 5
                and cls in (1, 2)
                and weather != 3
                and n_female == 0
                and rn in (10, 11, 12)
            ):
                return None

            matched = []
            if stadium == 18 and b2_motor_v >= 40.0 and b5_motor_v >= 45.0:
                matched.append({
                    "level": "tokuyama_123_tri",
                    "label": "徳山 1-2-3",
                    "bet": "3連単 1-2-3",
                    "rank": "trifecta_niche",
                    "rank_label": "一般戦コア",
                    "recovery": 230.5,
                    "n": 19,
                    "hit_rate": 31.6,
                    "name": "徳山123",
                    "tag": "一般戦10-12R + A1/A2 + 雨除外 + 女子戦除外 + 2号艇モーター2連率>=40 + 5号艇モーター2連率>=45",
                    "tetsuban_score": 7,
                })
            if (
                stadium == 18
                and local_1_v >= 7.0
                and b2_motor_v >= 35.0
                and b2_ex_v is not None
                and b3_ex_v is not None
                and b2_ex_v <= b3_ex_v
                and b2_ex_st_rank_v is not None and b2_ex_st_rank_v <= 3
                and b1_ex_rank_v is not None and b1_ex_rank_v <= 2
            ):
                matched.append({
                    "level": "tokuyama_12a_exa",
                    "label": "徳山 1-2 強化A",
                    "bet": "2連単 1-2",
                    "rank": "exacta_niche",
                    "rank_label": "2連単採用",
                    "recovery": 166.9,
                    "n": 54,
                    "hit_rate": 59.3,
                    "name": "徳山12強化A",
                    "tag": "徳山一般10-12R + A1/A2 + 雨除外 + 当地1着率>=7 + 2号艇現行M>=35 + 展示T2<=3 + 2号艇展示ST順位<=3 + 1号艇展示T順位<=2",
                    "tetsuban_score": 6,
                })
            if stadium == 19 and b3_n1_v >= 6.0 and b3_motor_v >= 40.0 and avg_st_v is not None and avg_st_v < 0.16:
                matched.append({
                    "level": "shimonoseki_132_tri",
                    "label": "下関 1-3-2",
                    "bet": "3連単 1-3-2",
                    "rank": "trifecta_niche",
                    "rank_label": "一般戦コア",
                    "recovery": 203.8,
                    "n": 16,
                    "hit_rate": 25.0,
                    "name": "下関132",
                    "tag": "一般戦10-12R + A1/A2 + 雨除外 + 女子戦除外 + 3号艇全国1着率>=6 + 3号艇モーター2連率>=40 + 1号艇平均ST<0.16",
                    "tetsuban_score": 6,
                })
            if stadium == 16 and age_v is not None and 40 <= age_v <= 49 and b2_motor_v >= 45.0 and b2_top2_v >= 35.0:
                matched.append({
                    "level": "kojima_124_tri",
                    "label": "児島 1-2-4",
                    "bet": "3連単 1-2-4",
                    "rank": "trifecta_niche",
                    "rank_label": "一般戦コア",
                    "recovery": 221.2,
                    "n": 17,
                    "hit_rate": 29.4,
                    "name": "児島124",
                    "tag": "一般戦10-12R + A1/A2 + 雨除外 + 女子戦除外 + 1号艇年齢40-49 + 2号艇モーター2連率>=45 + 2号艇全国2連対率>=35",
                    "tetsuban_score": 7,
                })

            best = _pick_best_market_signal(*matched)
            if not best:
                return None
            return {
                **best,
                "natl_1": None,
                "local_1": None,
                "is_reference": False,
                "is_trifecta_niche": True,
                "trifecta_niche_name": best["name"],
                "trifecta_niche_tag": best["tag"],
                "trifecta_niche_hit_rate": best["hit_rate"],
                "trifecta_niche_recovery": best["recovery"],
                "tetsuban_label": best["name"],
                "is_display_confirmed": True,
            }

        def _evaluate_exacta_niche(stadium, race_number, boat1_motor_top2=None,
                                   boat2_motor_top2=None, boat4_motor_top2=None,
                                   boat1_natl_1=None, boat4_natl_1=None, n_female=0,
                                   program_type=None, pair_affinity=None, grade=None, weather=None):
            """Evaluate verified exacta niche strategies."""
            try:
                rn = int(race_number) if race_number is not None else 0
            except (TypeError, ValueError):
                rn = 0
            try:
                m1 = float(boat1_motor_top2) if boat1_motor_top2 is not None else 0.0
            except (TypeError, ValueError):
                m1 = 0.0
            try:
                m2 = float(boat2_motor_top2) if boat2_motor_top2 is not None else 0.0
            except (TypeError, ValueError):
                m2 = 0.0
            try:
                m4 = float(boat4_motor_top2) if boat4_motor_top2 is not None else 0.0
            except (TypeError, ValueError):
                m4 = 0.0
            try:
                n1 = float(boat1_natl_1) if boat1_natl_1 is not None else 0.0
            except (TypeError, ValueError):
                n1 = 0.0
            try:
                n4 = float(boat4_natl_1) if boat4_natl_1 is not None else 0.0
            except (TypeError, ValueError):
                n4 = 0.0

            if stadium == 13 and m2 >= 55.0:
                return {
                    "level": "exacta_niche_amagasaki_motor",
                    "label": "尼崎M 2連単1-4",
                    "recovery": 180.7,
                    "n": 99,
                    "bet": "2連単 1-4",
                    "rank": "exacta_niche",
                    "rank_label": "2連単ニッチ",
                    "rank_emoji": "2R",
                    "natl_1": None,
                    "local_1": None,
                    "is_exacta_niche": True,
                    "exacta_niche_tag": "尼崎 + 2号艇モーター2連率55%以上",
                    "exacta_niche_hit_rate": 22.2,
                    "exacta_niche_recovery": 180.7,
                    "exacta_niche_meetings": None,
                    "exacta_niche_ahead": None,
                    "exacta_niche_rate": None,
                    "tetsuban_score": 5,
                    "tetsuban_label": "2連単 5★",
                    "uses_rain_filter": True,
                }
            if stadium == 6 and grade == 5 and n_female == 0 and n1 >= 7.0 and n4 >= 5.0 and m4 >= 40.0 and m2 <= 35.0:
                return {
                    "level": "exacta_niche_hamanako14",
                    "label": "浜名湖 2連単1-4",
                    "recovery": 151.6,
                    "n": 50,
                    "bet": "2連単 1-4",
                    "rank": "exacta_niche",
                    "rank_label": "2連単ニッチ",
                    "rank_emoji": "2R",
                    "natl_1": None,
                    "local_1": None,
                    "is_exacta_niche": True,
                    "exacta_niche_tag": "浜名湖 一般戦 1-4",
                    "exacta_niche_hit_rate": 30.0,
                    "exacta_niche_recovery": 151.6,
                    "exacta_niche_meetings": None,
                    "exacta_niche_ahead": None,
                    "exacta_niche_rate": None,
                    "tetsuban_score": 5,
                    "tetsuban_label": "2連単 5★",
                    "uses_rain_filter": True,
                }

            if stadium == 24 and grade == 5 and rn in (5, 6, 7, 8) and n_female == 0 and n1 >= 7.0 and n4 >= 5.0 and m2 <= 35.0:
                return {
                    "level": "exacta_niche_omura14",
                    "label": "大村 2連単1-4",
                    "recovery": 152.8,
                    "n": 32,
                    "bet": "2連単 1-4",
                    "rank": "exacta_niche",
                    "rank_label": "2連単ニッチ",
                    "rank_emoji": "2R",
                    "natl_1": None,
                    "local_1": None,
                    "is_exacta_niche": True,
                    "exacta_niche_tag": "大村 一般戦5-8R 1-4",
                    "exacta_niche_hit_rate": 31.2,
                    "exacta_niche_recovery": 152.8,
                    "exacta_niche_meetings": None,
                    "exacta_niche_ahead": None,
                    "exacta_niche_rate": None,
                    "tetsuban_score": 5,
                    "tetsuban_label": "2連単 5★",
                    "uses_rain_filter": True,
                }

            pair = pair_affinity or {}
            if not pair.get("is_boat1_over_boat2"):
                return None
            meetings = pair.get("meetings") or 0
            ahead = pair.get("ahead") or 0
            rate = pair.get("rate")
            rate_pct = round(rate * 100, 1) if rate is not None else None

            if stadium == 19 and rn == 7 and m1 >= 45.0 and meetings >= 5:
                return {
                    "level": "exacta_niche_shimonoseki7",
                    "label": "下関7R 2連単1-4",
                    "recovery": 195.3,
                    "n": 34,
                    "bet": "2連単 1-4",
                    "rank": "exacta_niche",
                    "rank_label": "2連単ニッチ",
                    "rank_emoji": "2R",
                    "natl_1": None,
                    "local_1": None,
                    "is_exacta_niche": True,
                    "exacta_niche_tag": "下関7R + 1号艇モーター2連率45%以上 + 1号艇が2号艇に相性優位",
                    "exacta_niche_hit_rate": 32.4,
                    "exacta_niche_recovery": 195.3,
                    "exacta_niche_meetings": meetings,
                    "exacta_niche_ahead": ahead,
                    "exacta_niche_rate": rate_pct,
                    "tetsuban_score": 5,
                    "tetsuban_label": "2連単 5★",
                }

            if program_type == "fixed_B1A" and m1 >= 40.0:
                return {
                    "level": "exacta_niche_fixed_b1a",
                    "label": "進入固定B1A 2連単1-2",
                    "recovery": 140.1,
                    "n": 93,
                    "bet": "2連単 1-2",
                    "rank": "exacta_niche",
                    "rank_label": "2連単ニッチ",
                    "rank_emoji": "2R",
                    "natl_1": None,
                    "local_1": None,
                    "is_exacta_niche": True,
                    "exacta_niche_tag": "進入固定B1A + 1号艇モーター2連率40%以上 + 1号艇が2号艇に相性優位",
                    "exacta_niche_hit_rate": 29.0,
                    "exacta_niche_recovery": 140.1,
                    "exacta_niche_meetings": meetings,
                    "exacta_niche_ahead": ahead,
                    "exacta_niche_rate": rate_pct,
                    "tetsuban_score": 4,
                    "tetsuban_label": "2連単 4★",
                }
            return None

        def _evaluate_ashiya_boat4_lift(ctx: dict | None):
            """Evaluate verified Ashiya exacta 4-1 exhibition lift strategy."""
            if not ctx:
                return None

            def _to_float(value):
                try:
                    return float(value) if value is not None else None
                except (TypeError, ValueError):
                    return None

            def _to_int(value):
                try:
                    return int(value) if value is not None else None
                except (TypeError, ValueError):
                    return None

            stadium = _to_int(ctx.get("stadium"))
            ex_course = _to_int(ctx.get("boat4_ex_course"))
            r4_n = _to_int(ctx.get("boat4_course4_n")) or 0
            racer_ex_n = _to_int(ctx.get("boat4_racer_ex_n")) or 0
            motor_ex_n = _to_int(ctx.get("boat4_motor_ex_n")) or 0
            r4_avg_st = _to_float(ctx.get("boat4_course4_avg_st"))
            r4_win_rate = _to_float(ctx.get("boat4_course4_win_rate"))
            boat4_ex_time = _to_float(ctx.get("boat4_exhibition_time"))
            best_ex_time = _to_float(ctx.get("best_exhibition_time"))
            racer_avg_ex = _to_float(ctx.get("boat4_racer_avg_ex"))
            motor_avg_ex = _to_float(ctx.get("boat4_motor_avg_ex"))
            boat4_ex_st = _to_float(ctx.get("boat4_ex_st"))
            n_exhibition_times = _to_int(ctx.get("n_exhibition_times")) or 0

            if None in (r4_avg_st, r4_win_rate, boat4_ex_time, best_ex_time,
                        racer_avg_ex, motor_avg_ex, boat4_ex_st):
                return None

            best_diff = boat4_ex_time - best_ex_time
            racer_gain = racer_avg_ex - boat4_ex_time
            motor_gain = motor_avg_ex - boat4_ex_time

            if not (
                stadium == 21
                and ex_course == 4
                and r4_n >= 8
                and r4_avg_st <= 0.16
                and r4_win_rate >= 0.08
                and n_exhibition_times >= 6
                and best_diff <= 0.08
                and racer_ex_n >= 8
                and racer_gain >= 0.08
                and motor_ex_n >= 5
                and motor_gain >= 0.05
                and boat4_ex_st <= 0.10
            ):
                return None

            return {
                "level": "exacta_niche_ashiya_boat4_lift",
                "label": "芦屋 4号艇展示上振れ",
                "recovery": 311.5,
                "n": 54,
                "bet": "2連単 4-1",
                "rank": "exacta_niche",
                "rank_label": "2連単ニッチ",
                "rank_emoji": "2R",
                "natl_1": None,
                "local_1": None,
                "is_exacta_niche": True,
                "is_ashiya_boat4_lift": True,
                "exacta_niche_tag": (
                    "芦屋 + 4号艇展示進入4C + 4C平均ST0.16以下 "
                    "+ 展示タイム上振れ + 展示ST0.10以下"
                ),
                "exacta_niche_hit_rate": 20.4,
                "exacta_niche_recovery": 311.5,
                "exacta_niche_meetings": None,
                "exacta_niche_ahead": None,
                "exacta_niche_rate": None,
                "ashiya_boat4_best_diff": round(best_diff, 3),
                "ashiya_boat4_racer_gain": round(racer_gain, 3),
                "ashiya_boat4_motor_gain": round(motor_gain, 3),
                "ashiya_boat4_course4_n": r4_n,
                "ashiya_boat4_course4_win_rate": round(r4_win_rate * 100, 1),
                "is_display_confirmed": True,
                "tetsuban_score": 6,
                "tetsuban_label": "芦屋4C 6★",
            }

        def _evaluate_ashiya_boat4_watch(ctx: dict | None):
            """Evaluate pre-exhibition Ashiya 4C monitor state for the exacta 4-1 setup."""
            if not ctx:
                return None

            def _to_float(value):
                try:
                    return float(value) if value is not None else None
                except (TypeError, ValueError):
                    return None

            def _to_int(value):
                try:
                    return int(value) if value is not None else None
                except (TypeError, ValueError):
                    return None

            stadium = _to_int(ctx.get("stadium"))
            if stadium != 21:
                return None

            r4_n = _to_int(ctx.get("boat4_course4_n")) or 0
            racer_ex_n = _to_int(ctx.get("boat4_racer_ex_n")) or 0
            motor_ex_n = _to_int(ctx.get("boat4_motor_ex_n")) or 0
            r4_avg_st = _to_float(ctx.get("boat4_course4_avg_st"))
            r4_win_rate = _to_float(ctx.get("boat4_course4_win_rate"))
            racer_avg_ex = _to_float(ctx.get("boat4_racer_avg_ex"))
            motor_avg_ex = _to_float(ctx.get("boat4_motor_avg_ex"))
            ex_course = _to_int(ctx.get("boat4_ex_course"))
            boat4_ex_time = _to_float(ctx.get("boat4_exhibition_time"))
            best_ex_time = _to_float(ctx.get("best_exhibition_time"))
            boat4_ex_st = _to_float(ctx.get("boat4_ex_st"))

            if None in (r4_avg_st, r4_win_rate, racer_avg_ex, motor_avg_ex):
                return None

            if not (
                r4_n >= 8
                and r4_avg_st <= 0.16
                and r4_win_rate >= 0.08
                and racer_ex_n >= 8
                and motor_ex_n >= 5
            ):
                return None

            best_diff = None
            racer_gain = None
            motor_gain = None
            ready_count = 0
            ready_total = 5

            if ex_course == 4:
                ready_count += 1
            if boat4_ex_time is not None and best_ex_time is not None:
                best_diff = boat4_ex_time - best_ex_time
                if best_diff <= 0.08:
                    ready_count += 1
                racer_gain = racer_avg_ex - boat4_ex_time
                motor_gain = motor_avg_ex - boat4_ex_time
                if racer_gain >= 0.08:
                    ready_count += 1
                if motor_gain >= 0.05:
                    ready_count += 1
            if boat4_ex_st is not None and boat4_ex_st <= 0.10:
                ready_count += 1

            ready_now = (
                ex_course == 4
                and best_diff is not None and best_diff <= 0.08
                and racer_gain is not None and racer_gain >= 0.08
                and motor_gain is not None and motor_gain >= 0.05
                and boat4_ex_st is not None and boat4_ex_st <= 0.10
            )

            return {
                "level": "morning_watch_ashiya_boat4_lift",
                "label": "朝監視 芦屋4C",
                "recovery": 311.5,
                "n": 54,
                "bet": "2連単 4-1",
                "rank": "exacta_niche",
                "rank_label": "2連単ニッチ",
                "rank_emoji": "2R",
                "natl_1": None,
                "local_1": None,
                "is_exacta_niche": True,
                "is_reference": True,
                "is_morning": True,
                "is_morning_watch": True,
                "is_ashiya_boat4_watch": True,
                "exacta_niche_tag": "芦屋4C朝監視 / 展示後に4-1へ絞り込み",
                "exacta_niche_hit_rate": 20.4,
                "exacta_niche_recovery": 311.5,
                "exacta_niche_meetings": None,
                "exacta_niche_ahead": None,
                "exacta_niche_rate": None,
                "ashiya_boat4_course4_n": r4_n,
                "ashiya_boat4_course4_avg_st": round(r4_avg_st, 3),
                "ashiya_boat4_course4_win_rate": round(r4_win_rate * 100, 1),
                "ashiya_boat4_racer_ex_n": racer_ex_n,
                "ashiya_boat4_racer_avg_ex": round(racer_avg_ex, 3),
                "ashiya_boat4_motor_ex_n": motor_ex_n,
                "ashiya_boat4_motor_avg_ex": round(motor_avg_ex, 3),
                "ashiya_boat4_best_diff": round(best_diff, 3) if best_diff is not None else None,
                "ashiya_boat4_racer_gain": round(racer_gain, 3) if racer_gain is not None else None,
                "ashiya_boat4_motor_gain": round(motor_gain, 3) if motor_gain is not None else None,
                "ashiya_boat4_ex_course": ex_course,
                "ashiya_boat4_ex_st": round(boat4_ex_st, 3) if boat4_ex_st is not None else None,
                "ashiya_watch_ready_count": ready_count,
                "ashiya_watch_ready_total": ready_total,
                "ashiya_watch_ready": ready_now,
                "is_display_confirmed": ready_now,
                "is_after_exhibition_out": (
                    ex_course is not None
                    or boat4_ex_time is not None
                    or boat4_ex_st is not None
                ) and not ready_now,
                "tetsuban_score": 5 if ready_now else 4,
                "tetsuban_label": "芦屋4C 点灯" if ready_now else "芦屋4C 朝監視",
            }

        def _evaluate_ashiya_4head_flow(ctx: dict | None):
            """Evaluate adopted Ashiya trifecta 4-head flow strategy."""
            if not ctx:
                return None

            def _to_float(value):
                try:
                    return float(value) if value is not None else None
                except (TypeError, ValueError):
                    return None

            def _to_int(value):
                try:
                    return int(value) if value is not None else None
                except (TypeError, ValueError):
                    return None

            stadium = _to_int(ctx.get("stadium"))
            boat1_natl1 = _to_float(ctx.get("natl_1"))
            boat1_motor = _to_float(ctx.get("boat1_motor_top2"))
            boat2_motor = _to_float(ctx.get("boat2_motor_top2"))
            boat3_motor = _to_float(ctx.get("boat3_motor_top2"))
            n180 = _to_int(ctx.get("boat4_course4_n180")) or 0
            avg_st_180 = _to_float(ctx.get("boat4_course4_avg_st_180"))
            win_rate_180 = _to_float(ctx.get("boat4_course4_win_rate_180"))
            motor_n180 = _to_int(ctx.get("boat4_motor4_n180")) or 0
            motor_win_rate_180 = _to_float(ctx.get("boat4_motor4_win_rate_180"))
            if not (
                stadium == 21
                and boat1_natl1 is not None and boat1_natl1 <= 6.5
                and boat1_motor is not None and boat1_motor <= 35.0
                and boat2_motor is not None and boat2_motor <= 40.0
                and boat3_motor is not None and boat3_motor <= 35.0
                and n180 >= 12
                and avg_st_180 is not None and avg_st_180 <= 0.15
                and win_rate_180 is not None and win_rate_180 >= 0.12
                and motor_n180 >= 8
                and motor_win_rate_180 is not None and motor_win_rate_180 >= 0.08
            ):
                return None
            return {
                "level": "ashiya_4head_flow_tri",
                "label": "芦屋 4頭総流し",
                "bet": "3連単 4-全-全 (20点)",
                "rank": "trifecta_niche",
                "rank_label": "3連単 採用",
                "rank_emoji": "3連単",
                "recovery": 154.5,
                "n": 52,
                "natl_1": None,
                "local_1": None,
                "is_trifecta_niche": True,
                "trifecta_niche_name": "芦屋 4頭総流し",
                "trifecta_niche_tag": "1号艇弱め + 4号艇4コース実績 + 4号艇現行モーター実績 + 2/3号艇モーター弱め",
                "trifecta_niche_hit_rate": 51.9,
                "trifecta_niche_recovery": 154.5,
                "tetsuban_score": 6,
                "tetsuban_label": "芦屋4頭",
                "is_display_confirmed": True,
                "uses_rain_filter": False,
            }

        def _evaluate_toda_42_flow(ctx: dict | None):
            """Evaluate adopted Toda trifecta 4-2-all flow strategy."""
            if not ctx:
                return None

            def _to_float(value):
                try:
                    return float(value) if value is not None else None
                except (TypeError, ValueError):
                    return None

            def _to_int(value):
                try:
                    return int(value) if value is not None else None
                except (TypeError, ValueError):
                    return None

            stadium = _to_int(ctx.get("stadium"))
            boat1_natl1 = _to_float(ctx.get("natl_1"))
            boat2_motor = _to_float(ctx.get("boat2_motor_top2"))
            boat3_motor = _to_float(ctx.get("boat3_motor_top2"))
            boat4_motor = _to_float(ctx.get("boat4_motor_top2"))
            wave_height = _to_float(ctx.get("wave_height"))
            n180 = _to_int(ctx.get("boat4_course4_n180")) or 0
            avg_st_180 = _to_float(ctx.get("boat4_course4_avg_st_180"))
            win_rate_180 = _to_float(ctx.get("boat4_course4_win_rate_180"))
            makuri_rate_180 = _to_float(ctx.get("boat4_course4_makuri_rate_180"))
            if not (stadium == 2 and n180 >= 8 and avg_st_180 is not None and avg_st_180 <= 0.15 and win_rate_180 is not None and win_rate_180 >= 0.08 and makuri_rate_180 is not None and makuri_rate_180 >= 0.05 and boat4_motor is not None and boat4_motor >= 40.0 and boat1_natl1 is not None and boat1_natl1 <= 7.0 and boat2_motor is not None and boat2_motor <= 40.0 and boat3_motor is not None and boat3_motor <= 40.0 and wave_height is not None and wave_height >= 2.0):
                return None
            return {
                "level": "toda_42_flow_tri",
                "label": "戸田 4-2-全",
                "bet": "3連単 4-2-全",
                "rank": "trifecta_niche",
                "rank_label": "3連単 採用",
                "rank_emoji": "3R",
                "recovery": 1445.0,
                "n": 4,
                "natl_1": None,
                "local_1": None,
                "is_trifecta_niche": True,
                "trifecta_niche_name": "戸田 4-2-全",
                "trifecta_niche_tag": "戸田4号4C実績 + 4号モーター40%以上 + 1/2/3弱め + 波高2cm以上",
                "trifecta_niche_hit_rate": 25.0,
                "trifecta_niche_recovery": 1445.0,
                "tetsuban_score": 7,
                "tetsuban_label": "戸田4-2-全",
                "is_display_confirmed": True,
                "uses_rain_filter": False,
            }

        def _evaluate_miyajima_boat4_watch(ctx: dict | None):
            """Evaluate verified Miyajima boat4 makuri watch strategy."""
            if not ctx:
                return None
            return None

            def _to_float(value):
                try:
                    return float(value) if value is not None else None
                except (TypeError, ValueError):
                    return None

            def _to_int(value):
                try:
                    return int(value) if value is not None else None
                except (TypeError, ValueError):
                    return None

            stadium = _to_int(ctx.get("stadium"))
            boat4_class = _to_int(ctx.get("boat4_class"))
            ex_course = _to_int(ctx.get("boat4_ex_course"))
            ex_st = _to_float(ctx.get("boat4_ex_st"))
            best_diff = _to_float(ctx.get("boat4_best_ex_diff"))
            racer_gain = _to_float(ctx.get("boat4_racer_gain"))
            boat4_motor_top2 = _to_float(ctx.get("boat4_motor_top2")) or 0.0
            boat1_natl1 = _to_float(ctx.get("natl_1")) or 0.0
            boat1_motor_top2 = _to_float(ctx.get("boat1_motor_top2")) or 0.0
            r4_n = _to_int(ctx.get("boat4_course4_n")) or 0
            m4_n = _to_int(ctx.get("boat4_motor4_n")) or 0
            rex_n = _to_int(ctx.get("boat4_racer_ex_n")) or 0
            r4_avg_st = _to_float(ctx.get("boat4_course4_avg_st"))
            r4_win_rate = _to_float(ctx.get("boat4_course4_win_rate"))
            m4_win_rate = _to_float(ctx.get("boat4_motor4_win_rate"))
            racer_avg_ex = _to_float(ctx.get("boat4_racer_avg_ex"))

            if None in (r4_avg_st, r4_win_rate, m4_win_rate):
                return None

            base_ok = (
                stadium == 17
                and boat4_class == 1
                and boat4_motor_top2 >= 40.0
                and boat1_natl1 <= 8.0
                and boat1_motor_top2 <= 35.0
                and r4_avg_st <= 0.15
                and r4_win_rate >= 0.08
                and m4_n >= 12
                and m4_win_rate >= 0.08
            )
            if not base_ok:
                return None

            ready_checks = [
                ex_course == 4,
                ex_st is not None and ex_st <= 0.12,
                best_diff is not None and best_diff <= 0.10,
                racer_gain is not None and racer_gain > 0.0,
            ]
            ready_count = sum(1 for ok in ready_checks if ok)
            is_ready = all(ready_checks)

            base_payload = {
                "natl_1": None,
                "local_1": None,
                "miyajima_boat4_course4_n": r4_n,
                "miyajima_boat4_course4_avg_st": round(r4_avg_st, 3),
                "miyajima_boat4_course4_win_rate": round(r4_win_rate * 100, 1),
                "miyajima_boat4_motor_n": m4_n,
                "miyajima_boat4_motor_win_rate": round(m4_win_rate * 100, 1),
                "miyajima_boat4_ex_course": ex_course,
                "miyajima_boat4_ex_st": round(ex_st, 3) if ex_st is not None else None,
                "miyajima_boat4_best_ex_diff": round(best_diff, 3) if best_diff is not None else None,
                "miyajima_boat4_racer_ex_n": rex_n,
                "miyajima_boat4_racer_avg_ex": round(racer_avg_ex, 3) if racer_avg_ex is not None else None,
                "miyajima_boat4_racer_gain": round(racer_gain, 3) if racer_gain is not None else None,
                "miyajima_watch_ready_count": ready_count,
                "miyajima_watch_ready_total": 4,
                "miyajima_watch_ready": is_ready,
            }
            return None

        def _evaluate_win_niche(stadium, boat1_top2=None, boat3_top2=None, boat4_top2=None):
            """Evaluate verified win-bet niche strategies."""
            return None
            if stadium == 1 and (b3 - b1) >= 0.08 and (b3 - b4) >= 0.05:
                return {
                    "level": "win_niche_kiryu_win2",
                    "label": "桐生 単勝2",
                    "recovery": 181.0,
                    "n": 209,
                    "bet": "単勝 2",
                    "rank": "win_niche",
                    "rank_label": "単勝ニッチ",
                    "rank_emoji": "W2",
                    "natl_1": None,
                    "local_1": None,
                    "is_win_niche": True,
                    "win_niche_tag": "桐生 + 3号艇2着内予測が1号艇より8pt以上 + 4号艇より5pt以上",
                    "win_niche_hit_rate": 24.9,
                    "win_niche_recovery": 181.0,
                    "win_niche_buy_odds": 6.0,
                    "win_niche_watch_odds": 4.0,
                    "boat1_top2": b1,
                    "boat3_top2": b3,
                    "boat4_top2": b4,
                    "is_reference": False,
                    "tetsuban_score": 5,
                    "tetsuban_label": "単勝2 5★",
                }
            return None

        def _evaluate_l4(stadium, grade, cls, mp_int, natl_1=None, local_1=None,
                         race_id=None, avg_st=None, age=None, ex_st=None,
                         boat2_top2=None, race_number=None, boat3_natl_1=None,
                         mid132_mp_int=None):
            """確定オッズベース L4 マーク判定 (L4+ / L4++ ランク付き)

            一般戦 (grade=5) は F1 条件
              「1号艇 国1%≥7 ∧ 2号艇 国2連率≥40」
            を満たす場合のみ採用候補 (is_reference=False)。
            それ以外の一般戦は従来通り参考扱い (is_reference=True)。
            OOS Tier 1 認定 (4年 ROI 204% / CI 下限 ≥150%)。
            """
            in_500_1000 = mp_int is not None and 500 <= mp_int < 1000
            # L4-Mid (2026-05-19 追加): オッズ 10-20倍帯で 1-3-2 単点。
            # 検証 ???76.5% (n=198)、L4 帯と別 universe (排他)。
            in_1000_2000 = mp_int is not None and 1000 <= mp_int < 2000
            mid132_in_1000_2000 = mid132_mp_int is not None and 1000 <= mid132_mp_int < 2000
            b_excluded = stadium not in EXCLUDE_B if stadium is not None else False
            # 企画レース観察 (2026-05-19 追加): 戸田 7R は B除外を無視して
            # L4 帯 + A1 のみで観察対象とする (3 ヶ月実績で採用判断)
            try:
                _rn_int = int(race_number) if race_number is not None else 0
            except (TypeError, ValueError):
                _rn_int = 0
            is_planned_obs = (
                in_500_1000 and cls == 1 and
                (stadium == 2 and _rn_int == 7)   # 戸田 7R (検証 ROI 171.5%)
            )
            # L4-Mid 観察対象: A1 + B除外 + 10-20倍帯
            is_obs_mid_132 = (
                mid132_in_1000_2000 and cls == 1 and b_excluded
            )
            if not (in_500_1000 and b_excluded):
                # L4-Mid (10-20倍 + B除外 + A1) を最優先で判定
                if is_obs_mid_132:
                    # Tier A 判定 (2026-05-19): 3号艇 国1% ≥ 7%
                    # 4 年検証 ???293.3% / n=9 ???? 認定
                    try:
                        n1_3 = float(boat3_natl_1) if boat3_natl_1 is not None else 0.0
                    except (TypeError, ValueError):
                        n1_3 = 0.0
                    is_tier_a = (n1_3 >= 7.0)
                    return {
                        "level": "obs_mid_132_tier_a" if is_tier_a else "obs_mid_132",
                        "label": "🟦 T-A L4-Mid 1-3-2 (3号艇強)" if is_tier_a else "🟦L4-Mid 1-3-2",
                        "recovery": 293.3 if is_tier_a else 76.5,
                        "bet": "3連単 1-3-2",
                        "n": 9 if is_tier_a else 198,
                        "is_reference": True,
                        "is_obs_mid_132": True,
                        "is_obs_mid_132_tier_a": is_tier_a,
                        "rank": "tier_a" if is_tier_a else "base",
                        "rank_label": "Tier A 観察" if is_tier_a else "Tier B 観察",
                        "rank_emoji": "🟦",
                        "natl_1": natl_1,
                        "local_1": local_1,
                        "boat3_natl_1": n1_3,
                        "tetsuban_score": 0,
                        "tetsuban_label": "",
                    }
                # 通常の L4 universe から外れていても、企画レース観察対象なら
                # 観察バッジ用 base dict を返す (採用ベースには加算しない)
                if is_planned_obs:
                    # 戸田 7R 企画レース観察 (検証 ROI 171.5%, n=106)
                    return {
                        "level": "obs_planned",
                        "label": "🟢L4-戸田7R",
                        "recovery": 171.5,
                        "bet": "3連単 1-2-3",
                        "n": 106,
                        "is_reference": True,    # 本日候補リスト除外
                        "is_planned_obs": True,
                        "is_obs_toda_7r": True,
                        "rank": "base",
                        "rank_label": "観察",
                        "rank_emoji": "🟢",
                        "natl_1": natl_1,
                        "local_1": local_1,
                        "tetsuban_score": 0,
                        "tetsuban_label": "",
                    }
                return None

            # L4 戦略の対象: 1号艇A1 + SG/G1/G2/G3 + 本命500-1000 + B除外
            base = None
            if cls == 1:
                if grade == 1:
                    base = {"level": "SG", "label": "👑L4 SG×A1",
                            "recovery": 258.2, "bet": "3連単 1-2-3", "n": 40}
                elif grade == 2:
                    base = {"level": "G1", "label": "👑L4 G1×A1",
                            "recovery": 242.8, "bet": "3連単 1-2-3", "n": 227}
                elif grade == 3:
                    base = {"level": "G2", "label": "👑L4 G2×A1",
                            "recovery": 242.7, "bet": "3連単 1-2-3", "n": 30}
                elif grade == 4:
                    base = {"level": "G3", "label": "🎯L4 G3×A1",
                            "recovery": 149.2, "bet": "3連単 1-2-3", "n": 195}
                elif grade == 5:
                    # 一般戦: F1 条件 (国1%≥7 + 2号 top_2≥40) を満たすかチェック
                    try:
                        n1 = float(natl_1) if natl_1 is not None else 0.0
                    except (TypeError, ValueError):
                        n1 = 0.0
                    try:
                        b2 = float(boat2_top2) if boat2_top2 is not None else 0.0
                    except (TypeError, ValueError):
                        b2 = 0.0
                    try:
                        b2_ex = float(boat2_exhibition_time) if boat2_exhibition_time is not None else None
                    except (TypeError, ValueError):
                        b2_ex = None
                    try:
                        b3_ex = float(boat3_exhibition_time) if boat3_exhibition_time is not None else None
                    except (TypeError, ValueError):
                        b3_ex = None
                    try:
                        b2_ex = float(boat2_exhibition_time) if boat2_exhibition_time is not None else None
                    except (TypeError, ValueError):
                        b2_ex = None
                    try:
                        b3_ex = float(boat3_exhibition_time) if boat3_exhibition_time is not None else None
                    except (TypeError, ValueError):
                        b3_ex = None
                    try:
                        b2_ex = float(boat2_exhibition_time) if boat2_exhibition_time is not None else None
                    except (TypeError, ValueError):
                        b2_ex = None
                    try:
                        b3_ex = float(boat3_exhibition_time) if boat3_exhibition_time is not None else None
                    except (TypeError, ValueError):
                        b3_ex = None
                    if n1 >= 7.0 and b2 >= 40.0:
                        # ★ F1 該当: 採用候補 (本日候補/メール対象)
                        # backlog item 5: バッジ名は短く (旧 "🌟L4 G++ (一般×国1%≥7×2号40)")
                        base = {"level": "general_f1",
                                "label": "🌟L4 G++",
                                "recovery": 204.0,
                                "bet": "3連単 1-2-3",
                                "n": 1189,
                                "is_reference": False,
                                "is_f1": True}
                    else:
                        # F1 非該当: 従来通り参考バッジ
                        base = {"level": "general", "label": "L4参考",
                                "recovery": 147.7, "bet": "3連単 1-2-3", "n": 1776,
                                "is_reference": True}
                # grade unknown は対象外 (バッジも出さない)
            # A2 派生は L4 戦略対象外なので表示しない
            if not base:
                return None

            # ▼ L4 サブランク (1号艇A1のみ)
            rank_code, rank_label, rank_emoji, rec_override = _l4_rank(natl_1, local_1)
            base["rank"] = rank_code             # "base" / "plus" / "plus_plus"
            base["rank_label"] = rank_label
            base["rank_emoji"] = rank_emoji
            base["natl_1"] = natl_1
            base["local_1"] = local_1
            # ランクに応じて recovery 値を補正 (検証実測値ベース)
            # 参考レース (一般戦) は ROI 集計対象外なので rank 補正もスキップ。
            # F1 一般戦は独自の検証 ROI (204%) を保持するため rank 補正もスキップ。
            if rec_override is not None and not base.get("is_reference") and not base.get("is_f1"):
                base["recovery"] = rec_override
                base["label"] = f"{rank_emoji}{base['label']} ({rank_label})"

            # ▼ L4+1c80 (1コース 1着率 80%+ オーバーレイ)
            #   過去 180 日 ×20戦以上 で 1着率 ≥80% → +1c80 ランク付与
            #   検証: 3連単 1-2-3 ROI 215% (= L4 平均 190% + 25pt)
            if race_id:
                c1 = course1_stats.get(race_id)
                if c1 and is_1c80(c1[0], c1[1]):
                    base["course1_winrate"] = c1[0]
                    base["course1_starts"] = c1[1]
                    base["is_1c80"] = True
                    base["recovery_1c80"] = L4_1C80_RECOVERY
                    base["label_1c80"] = f"🚀1c80 ({c1[0]*100:.0f}%)"

            # ▼ L4 PRO (ベテラン × スタート上手 × 展示好調)
            #   平均ST<0.16 + 30-49歳 + 展示ST<0.18 (展示無ければ 2 条件で候補)
            #   検証: 4年 n=247、ROI 241.5%

            # ▼ L4-prime / L4-12R / 一般戦×12R 観察フラグ (3 ヶ月実績で採用判断)
            # base が確定したレース (= L4 universe 通過済) なら race_number で判定
            try:
                rn = int(race_number) if race_number is not None else 0
            except (TypeError, ValueError):
                rn = 0
            if rn == 12:
                base["is_obs_r12"] = True
            # 企画レース観察 (2026-05-19 追加): 戸田7R
            if stadium == 2 and rn == 7:
                base["is_obs_toda_7r"] = True

            # ▼ F1 Prime 11-12R (ユーザー提案、 2026-05-30 追加)
            #   F1 (一般戦×国1%≥7×2号モ2連率≥40) + 11/12R + L4 帯
            #   = is_f1 AND rn in (11, 12) のとき is_f1_prime=True
            #   検証 ROI: 暫定 204% (F1 単体と同等、 本番再検証で更新予定)

            # ▼ 抜きフィルター (2026-05-30 追加、 決まり手データ投入後検証)
            #   江戸川/浜名湖/鳴門は水面流れあり → 1号艇「抜き」勝ち比率高い
            #   L4 base 1号艇 1着 cohort で ROI 115.9% → フィルター成立で 129.9% (+14pt)
            #   時系列 robust (train 132.1% / test 116.9%)
            #   詳細: src/evaluation/nuki_filter.py
            try:
                from src.evaluation.nuki_filter import (
                    is_nuki_likely as _ns_is_likely,
                    NUKI_RECOVERY as _NS_REC,
                    NUKI_FILTER_LABEL as _NS_LABEL,
                )
                if _ns_is_likely(
                    stadium_number=stadium,
                    boat1_class=cls,
                    weather_number=weather,
                ):
                    base["is_nuki_filter"] = True
                    base["nuki_recovery"] = _NS_REC
                    base["nuki_label"] = _NS_LABEL
            except Exception:  # noqa: BLE001
                pass

            # ▼ 鉄板度スコア (backlog item 11): 条件が多く揃うほど高ROI 期待
            base["tetsuban_score"], base["tetsuban_label"] = _compute_tetsuban(base, rn)
            return base

        # --- L4 展示タイム補助点 (検証 2026-05-30) ---
        # ユーザー検証: 展示タイムは「除外」ではなく「強弱付け」に向く.
        # 軸A: 1号艇 best差 ≤ 0.03, 軸B: 2号艇 < 3号艇 → 0-2点.
        # 推奨投入: 100/150/200円 (補助点 0/1/2).
        # 検証 ROI: 展示なし L4 164.4% / score=0 146.9% / =1 171.4% / =2 180.8%.
        # 詳細: src/evaluation/exhibition_bonus.py
        from src.evaluation.exhibition_bonus import (
            evaluate_l4_with_bonus as _exb_eval,
        )

        # 2026-05-30: パフォーマンス改善 — 旧実装は race ごとに db_connect()
        # を新規に開いていた (180 race × 2 戦略 = 最大 360 DB 接続). 当日
        # 全 race 分を 1 つの bulk クエリで取得し、 dict[race_id] で
        # lookup する設計に変更.

        def _bulk_load_exhibition_times(date_iso):
            """当日 race の展示タイム (exhibition_time) を 1 クエリで一括取得.

            Returns: dict[race_id, dict[boat_number, exhibition_time]]
              race_previews に該当データが無い race は dict にキーが立たない.
            """
            try:
                with db_connect() as bconn:
                    cur = bconn.execute(
                        """
                        SELECT pv.race_id, pv.boat_number, NULLIF(pv.exhibition_time, 0)
                        FROM race_previews pv
                        JOIN races r ON r.race_id = pv.race_id
                        WHERE r.race_date = ?
                        """,
                        (date_iso,),
                    )
                    out: dict[str, dict[int, Optional[float]]] = {}
                    for rid_v, bn, et in cur.fetchall():
                        d = out.setdefault(rid_v, {})
                        d[bn] = et
                    return out
            except Exception:  # noqa: BLE001
                return {}

        def _compute_exhibition_bonus_for_l4(rid, ex_by_race):
            """L4 候補 race に対する展示タイム補助点を計算.

            ex_by_race は _bulk_load_exhibition_times で取得した
            dict[race_id, dict[boat_number, exhibition_time]].
            展示タイム不足/取得失敗時は None を返す.
            """
            ex_by_boat = ex_by_race.get(rid)
            if not ex_by_boat or len(ex_by_boat) < 6:
                return None
            all_times = [ex_by_boat.get(i) for i in range(1, 7)]
            return _exb_eval(
                boat1_ex_time=ex_by_boat.get(1),
                boat2_ex_time=ex_by_boat.get(2),
                boat3_ex_time=ex_by_boat.get(3),
                all_ex_times=all_times,
            )

        def _evaluate_morning_l4(stadium, grade, cls, prob_first, natl_1=None, local_1=None,
                                 race_id=None, avg_st=None, age=None, ex_st=None,
                                 boat2_top2=None, race_number=None):
            """朝判定用 L4 候補マーク (prob_first ベース)。

            一般戦 (grade=5) は F1 条件
              「1号艇 国1%≥7 ∧ 2号艇 国2連率≥40」
            を満たす場合のみ採用候補 (is_reference=False)。
            """
            if prob_first is None:
                return None

            def _apply_exhibition_st_out(base):
                """Mark morning ST candidates as out-of-range after exhibition ST."""
                if not base or ex_st is None:
                    return base
                try:
                    ex = float(ex_st)
                except (TypeError, ValueError):
                    return base
                if ex < 0.18:
                    return base

                if not base.get("is_morning_watch_st"):
                    return base

                base["is_reference"] = True
                base["is_exhibition_st_out"] = True
                base["exhibition_st"] = ex
                base["exhibition_st_threshold"] = 0.18
                base["label"] = f"{base['label']} (展示ST圏外)"
                base["st_out_reason"] = "朝時点ではST系候補でしたが、展示STが0.18未満を満たさないため購入対象外"
                return base

            b_excluded = stadium not in EXCLUDE_B if stadium is not None else False
            # 企画レース観察 (2026-05-19): 戸田7R は B除外を無視
            try:
                _rn_int = int(race_number) if race_number is not None else 0
            except (TypeError, ValueError):
                _rn_int = 0
            is_planned_obs_eligible = cls == 1 and 0.65 <= prob_first < 0.85 and (
                stadium == 2 and _rn_int == 7
            )
            if not b_excluded:
                if is_planned_obs_eligible:
                    # 戸田 7R: 通常 L4 universe 外だが観察対象 (検証 ROI 171.5%, n=106)
                    base = {
                        "level": "morning_obs_planned",
                        "label": "🌅🟢朝L4-戸田7R",
                        "recovery": 171.5,
                        "bet": "3連単 1-2-3 (確定後)",
                        "n": 106,
                        "is_reference": True,
                        "is_planned_obs": True,
                        "is_obs_toda_7r": True,
                        "is_morning": True,
                        "prob_first": prob_first,
                        "rank": "base",
                        "rank_label": "観察",
                        "rank_emoji": "🟢",
                        "natl_1": natl_1,
                        "local_1": local_1,
                        "tetsuban_score": 0,
                        "tetsuban_label": "",
                    }
                    return base
                return None
            # 1号艇 A1 + prob_first 0.65-0.85 → 500-1000帯候補
            base = None
            if cls == 1 and 0.65 <= prob_first < 0.85:
                if grade == 1:
                    base = {"level": "morning_SG", "label": "🌅👑朝L4 SG候補",
                            "recovery": 258.2, "n": 40}
                elif grade == 2:
                    base = {"level": "morning_G1", "label": "🌅👑朝L4 G1候補",
                            "recovery": 242.8, "n": 227}
                elif grade == 3:
                    base = {"level": "morning_G2", "label": "🌅👑朝L4 G2候補",
                            "recovery": 242.7, "n": 30}
                elif grade == 5:
                    # 一般戦: F1 条件 (国1%≥7 + 2号 top_2≥40) チェック
                    try:
                        n1 = float(natl_1) if natl_1 is not None else 0.0
                    except (TypeError, ValueError):
                        n1 = 0.0
                    try:
                        b2 = float(boat2_top2) if boat2_top2 is not None else 0.0
                    except (TypeError, ValueError):
                        b2 = 0.0
                    if n1 >= 7.0 and b2 >= 40.0:
                        # ★ F1 該当: 朝候補として配信対象
                        # backlog item 5: 短縮 (旧 "🌅🌟朝L4 G++ 候補")
                        base = {"level": "morning_general_f1",
                                "label": "🌅L4 G++",
                                "recovery": 204.0,
                                "n": 1189,
                                "is_reference": False,
                                "is_f1": True}
                    else:
                        # F1 非該当: 参考バッジ
                        base = {"level": "morning_general",
                                "label": "🌅L4参考",
                                "recovery": 147.7, "n": 1776,
                                "is_reference": True}
            elif cls == 1 and 0.60 <= prob_first < 0.65 and grade in (1, 2, 3, 4):
                # 展示前の朝監視枠:
                # 実弾の朝候補 (0.65-0.85) には少し届かないが、SG/G1/G2/G3 の
                # 1号艇A1で直前オッズ次第では L4 入りし得るものを見逃さない。
                # 例: 2026-06-09 宮島7R (prob_first=0.6366, T-120=12.7, T-5=9.9)。
                # is_reference=True のまま高ROI一覧へ「監視」として薄く残し、
                # 実際のROI集計には入れない。
                if grade == 1:
                    base = {"level": "morning_watch_SG", "label": "🌅👀朝監視 SG",
                            "recovery": 258.2, "n": 40, "is_reference": True,
                            "is_morning_watch": True}
                elif grade == 2:
                    base = {"level": "morning_watch_G1", "label": "🌅👀朝監視 G1",
                            "recovery": 242.8, "n": 227, "is_reference": True,
                            "is_morning_watch": True}
                elif grade == 3:
                    base = {"level": "morning_watch_G2", "label": "🌅👀朝監視 G2",
                            "recovery": 242.7, "n": 30, "is_reference": True,
                            "is_morning_watch": True}
            elif cls == 1 and 0.58 <= prob_first < 0.60 and grade in (1, 2, 3):
                # 朝監視ST:
                # T-120 オッズは見ず、1号艇A1 + 予測やや低め + 平均STの良さで
                # 直前浮上を朝から監視する。例: 2026-06-10 尼崎11R
                # (prob_first=0.5839, G1, avg_st=0.14, T-5=9.8)。
                try:
                    avg_st_for_watch = float(avg_st) if avg_st is not None else 9.99
                except (TypeError, ValueError):
                    avg_st_for_watch = 9.99
                if avg_st_for_watch <= 0.15:
                    if grade == 1:
                        base = {"level": "morning_watch_st_SG", "label": "🌅⚡朝監視ST SG",
                                "recovery": 258.2, "n": 40, "is_reference": True,
                                "is_morning_watch": True, "is_morning_watch_st": True}
                    elif grade == 2:
                        base = {"level": "morning_watch_st_G1", "label": "🌅⚡朝監視ST G1",
                                "recovery": 242.8, "n": 227, "is_reference": True,
                                "is_morning_watch": True, "is_morning_watch_st": True}
                    elif grade == 3:
                        base = {"level": "morning_watch_st_G2", "label": "🌅⚡朝監視ST G2",
                                "recovery": 242.7, "n": 30, "is_reference": True,
                                "is_morning_watch": True, "is_morning_watch_st": True}
            # grade unknown は対象外
            if not base:
                return None

            base["bet"] = "3連単 1-2-3 (確定後)"
            base["is_morning"] = True
            base["prob_first"] = prob_first

            # ▼ L4 サブランク (1号艇A1のみ)
            rank_code, rank_label, rank_emoji, rec_override = _l4_rank(natl_1, local_1)
            base["rank"] = rank_code             # "base" / "plus" / "plus_plus"
            base["rank_label"] = rank_label
            base["rank_emoji"] = rank_emoji
            base["natl_1"] = natl_1
            base["local_1"] = local_1
            # ▼ L4+1c80 オーバーレイ
            if race_id:
                c1 = course1_stats.get(race_id)
                if c1 and is_1c80(c1[0], c1[1]):
                    base["course1_winrate"] = c1[0]
                    base["course1_starts"] = c1[1]
                    base["is_1c80"] = True
                    base["recovery_1c80"] = L4_1C80_RECOVERY
                    base["label_1c80"] = f"🚀1c80 ({c1[0]*100:.0f}%)"
            # ▼ L4 PRO オーバーレイ (展示 ST 無い朝予測でも 2 条件で候補判定)
            # 参考レース (一般戦) と F1 一般戦は rank 補正をスキップ (固有の検証値を保持)
            if rec_override is not None and not base.get("is_reference") and not base.get("is_f1"):
                base["recovery"] = rec_override
                base["label"] = f"{rank_emoji}{base['label']} ({rank_label})"

            # ▼ L4-prime / L4-12R / 一般戦×12R 観察フラグ
            try:
                rn = int(race_number) if race_number is not None else 0
            except (TypeError, ValueError):
                rn = 0
            if rn == 12:
                base["is_obs_r12"] = True
            # 企画レース観察 (2026-05-19 追加): 戸田7R
            if stadium == 2 and rn == 7:
                base["is_obs_toda_7r"] = True

            # ▼ F1 Prime 11-12R (朝判定も同じロジック、 2026-05-30 追加)

            # ▼ 抜きフィルター (朝判定も同じロジック、 2026-05-30 追加)
            try:
                from src.evaluation.nuki_filter import (
                    is_nuki_likely as _ns_is_likely,
                    NUKI_RECOVERY as _NS_REC,
                    NUKI_FILTER_LABEL as _NS_LABEL,
                )
                if _ns_is_likely(
                    stadium_number=stadium,
                    boat1_class=cls,
                    weather_number=None,  # 朝判定では weather 未確定なので None で通す
                ):
                    base["is_nuki_filter"] = True
                    base["nuki_recovery"] = _NS_REC
                    base["nuki_label"] = _NS_LABEL
            except Exception:  # noqa: BLE001
                pass

            # 鉄板度スコア (朝判定も同じロジック)
            base["tetsuban_score"], base["tetsuban_label"] = _compute_tetsuban(base, rn)
            return _apply_exhibition_st_out(base)

        signals = []
        # 当日全レースを走査 (確定済 → L4、未確定 → 朝L4候補)
        # all_race_info が空 (DB エラー等) なら results のレースだけ処理
        race_iterable = all_race_info if all_race_info else {rid: {} for rid in results}
        WEATHER_LABEL = {1:"☀️ 晴", 2:"🌤️ 曇", 3:"☔ 雨", 4:"❄️ 雪"}

        # === パフォーマンス改善 (2026-05-30): bulk fetch ===
        # race ループの前に 1 度だけ DB を叩いて当日全 race 分のデータを取得.
        # これにより N+1 クエリ (race 数 × 戦略数 = 最大数百回 DB 接続) を
        # bulk クエリに削減. 旧実装で 1race ≈ 100ms 程度かかっていた
        # 部分が dict lookup (O(1)) になる.
        ex_bulk = _bulk_load_exhibition_times(target_date)

        for rid, info in race_iterable.items():
            stadium = info.get("stadium")
            grade = info.get("grade")
            race_no_info = info.get("race_number")
            cls = info.get("class")
            natl_1 = info.get("natl_1")
            local_1 = info.get("local_1")
            avg_st = info.get("avg_st")
            age = info.get("age")
            ex_st = info.get("ex_st")
            weather = info.get("weather")
            race_closed_at = info.get("race_closed_at")
            boat1_motor_top2 = info.get("boat1_motor_top2")
            boat2_top2 = info.get("boat2_top2")
            boat2_motor_top2 = info.get("boat2_motor_top2")
            boat2_exhibition_time = info.get("boat2_exhibition_time")
            boat3_natl_1 = info.get("boat3_natl_1")
            boat3_motor_top2 = info.get("boat3_motor_top2")
            boat3_exhibition_time = info.get("boat3_exhibition_time")
            boat5_motor_top2 = info.get("boat5_motor_top2")
            boat1_pred_top2 = info.get("boat1_pred_top2")
            boat3_pred_top2 = info.get("boat3_pred_top2")
            boat4_pred_top2 = info.get("boat4_pred_top2")
            program_type = info.get("program_type")
            wind_speed = info.get("wind_speed")
            n_female = info.get("n_female", 0) or 0
            is_rain = (weather == 3)
            rain_exclusion_active = _rain_exclusion_active(race_closed_at)
            is_rain_excluded = is_rain and rain_exclusion_active
            # ♀ 案A: レース内に女性が 1 名でもいると ROI が低下するため除外。
            # 男性のみ ROI 180.8% / 女性混入 134-158% / ベテラン男1号艇でも
            # 若手女性混入で 96.3% (analyze_female_deep.py 分析4) に崩落。
            is_female_present = (n_female > 0)
            boat3_trifecta_niche = _evaluate_boat3_trifecta_niche(boat3_signal_ctx.get(rid))
            tri124_132_niche = _evaluate_tri124_132_trifecta_niche(info)
            candidate_l4 = _evaluate_candidate_134_signal(
                stadium,
                grade,
                race_no_info,
                natl_1=natl_1,
                age=age,
                course1=cls,
                boat2_motor_top2=boat2_motor_top2,
                avg_st=avg_st,
                avg_st_n=info.get("avg_st_n"),
                weather=weather,
                n_female=n_female,
                target_date_iso=target_date,
            )
            core_data = results.get(rid)
            data = core_data
            if data is None and rid in mid132_results:
                data = {"min_payout": mid132_results[rid], "source": "1-3-2"}

            tsu_suminoe_signal = _evaluate_tsu_suminoe_123_signal(tsu_suminoe_live.get(rid))
            shimonoseki_123_signal = _evaluate_shimonoseki_123_signal(info)
            gamagori_adopted_signal = _evaluate_gamagori_adopted_signal(info)

            g23_optb = _evaluate_g23_optb_signal(
                stadium, grade, cls,
                data["min_payout"] if data else None,
                natl_1=natl_1,
                local_1=local_1,
                avg_st=avg_st,
                age=age,
                ex_st=ex_st,
                boat2_motor_top2=boat2_motor_top2,
                weather=weather,
                n_female=n_female,
            )
            fukuoka_wind_exa = _evaluate_fukuoka_wind_exa_signal(
                stadium, cls,
                boat2_top2=boat2_top2,
                wind_speed=wind_speed,
            )
            general_c = _evaluate_general_c_signal(
                stadium,
                grade,
                cls,
                core_data["min_payout"] if core_data else None,
                l4_band_ok=core_data.get("any_l4_in_window") if core_data else None,
                natl_1=natl_1,
                boat2_top2=boat2_top2,
                race_number=race_no_info,
                weather=weather,
                boat2_exhibition_time=boat2_exhibition_time,
                boat3_exhibition_time=boat3_exhibition_time,
                n_female=n_female,
            )
            non_exhibition_core = _evaluate_non_exhibition_core_signal(
                stadium,
                grade,
                cls,
                race_number=race_no_info,
                weather=weather,
                wind_speed=info.get("wind_speed"),
                n_female=n_female,
                local_1=local_1,
                avg_st=avg_st,
                age=age,
                boat1_motor_top2=boat1_motor_top2,
                boat1_exhibition_time=info.get("boat1_exhibition_time"),
                boat2_top2=boat2_top2,
                boat2_motor_top2=boat2_motor_top2,
                boat3_top2=info.get("boat3_top2"),
                boat3_natl_1=boat3_natl_1,
                boat3_motor_top2=boat3_motor_top2,
                boat3_ex_st=info.get("boat3_ex_st"),
                boat4_class=info.get("boat4_class"),
                boat4_local_1=info.get("boat4_local_1"),
                boat4_motor_top2=info.get("boat4_motor_top2"),
                best_exhibition_time=info.get("best_exhibition_time"),
                boat5_motor_top2=boat5_motor_top2,
                boat2_exhibition_time=boat2_exhibition_time,
                boat3_exhibition_time=boat3_exhibition_time,
                boat1_ex_rank=info.get("boat1_ex_rank"),
                boat2_ex_st_rank=info.get("boat2_ex_st_rank"),
            )

            if data:
                # === 確定オッズあり (L4 マーク) ===
                mp = data["min_payout"]
                src = data["source"]
                if mp < 500:
                    tier, expected_roi, title = "ultra_confident", 0.1845, "超本命"
                elif mp < 1000:
                    tier, expected_roi, title = "confident", 0.2741, "完全+EV"
                elif mp < 2000:
                    tier, expected_roi, title = "moderate", 0.1792, "やや本命"
                elif mp < 5000:
                    tier, expected_roi, title = "split", -0.0859, "拮抗"
                elif mp < 10000:
                    tier, expected_roi, title = "wild", -0.4310, "荒れ寄り"
                else:
                    tier, expected_roi, title = "chaos", -0.7354, "波乱"

                l4 = _evaluate_l4(stadium, grade, cls, mp, natl_1, local_1, race_id=rid,
                                  avg_st=avg_st, age=age, ex_st=ex_st,
                                  boat2_top2=boat2_top2, race_number=race_no_info,
                                  boat3_natl_1=boat3_natl_1,
                                  mid132_mp_int=mid132_results.get(rid))
                l4_portfolio = _evaluate_l4_portfolio_strong(
                    stadium, grade, cls, natl_1=natl_1,
                    avg_st=avg_st, age=age,
                    boat2_motor_top2=boat2_motor_top2,
                    race_number=race_no_info, wind_speed=wind_speed,
                    n_female=n_female, target_date_iso=target_date,
                )
                l4 = _apply_l4_portfolio_strong(l4, l4_portfolio)
                l4_general_200 = _evaluate_l4_general_200(
                    stadium, grade, cls, natl_1=natl_1,
                    boat2_top2=boat2_top2,
                    boat2_exhibition_time=boat2_exhibition_time,
                    boat3_exhibition_time=boat3_exhibition_time,
                    ex_st=ex_st,
                )
                l4 = _apply_l4_general_200(l4, l4_general_200)
                exacta_niche = _evaluate_exacta_niche(
                    stadium, race_no_info, boat1_motor_top2=boat1_motor_top2,
                    boat2_motor_top2=boat2_motor_top2, boat4_motor_top2=boat4_motor_top2,
                    boat1_natl_1=natl_1, boat4_natl_1=info.get("boat4_natl_1"), n_female=n_female,
                    program_type=program_type,
                    pair_affinity=exacta_pair_affinity.get(rid),
                    grade=grade, weather=weather,
                )
                tokoname_12_exacta = _evaluate_tokoname_12_exacta(tokoname_12_signal_ctx.get(rid))
                if exacta_niche:
                    l4 = exacta_niche
                if tokoname_12_exacta:
                    l4 = tokoname_12_exacta
                ashiya_exacta = _evaluate_ashiya_boat4_lift(ashiya_boat4_lift.get(rid))
                if ashiya_exacta:
                    l4 = ashiya_exacta
                ashiya_4head_flow = _evaluate_ashiya_4head_flow(ashiya_4head_flow_ctx.get(rid))
                if ashiya_4head_flow:
                    l4 = ashiya_4head_flow
                toda_42_flow = _evaluate_toda_42_flow(toda_42_flow_ctx.get(rid))
                if toda_42_flow:
                    l4 = toda_42_flow
                miyajima_watch = _evaluate_miyajima_boat4_watch(miyajima_boat4_watch.get(rid))
                if miyajima_watch:
                    l4 = miyajima_watch
                tide_tri = _evaluate_tide_tri_signal(
                    stadium,
                    weather=weather,
                    wind_speed=wind_speed,
                    wave_height=info.get("wave_height"),
                    tide_delta_60m_cm=info.get("tide_delta_60m_cm"),
                    tide_range_cm=info.get("tide_range_cm"),
                    is_high_tide_zone=info.get("is_high_tide_zone"),
                    is_low_tide_zone=info.get("is_low_tide_zone"),
                )
                win_niche = _evaluate_win_niche(
                    stadium,
                    boat1_top2=boat1_pred_top2,
                    boat3_top2=boat3_pred_top2,
                    boat4_top2=boat4_pred_top2,
                )
                l4 = _pick_best_market_signal(
                    l4,
                    exacta_niche,
                    tokoname_12_exacta,
                    ashiya_exacta,
                    ashiya_4head_flow,
                    toda_42_flow,
                    miyajima_watch,
                    tide_tri,
                    win_niche,
                    boat3_trifecta_niche,
                    tri124_132_niche,
                    shimonoseki_123_signal,
                    fukuoka_wind_exa,
                    non_exhibition_core,
                    general_c,
                    g23_optb,
                    tsu_suminoe_signal,
                    candidate_l4,
                    gamagori_adopted_signal,
                )
                l4 = _prefer_adopted_signal_over_general200(l4, general_c)
                if non_exhibition_core and l4 and l4.get("level") == "general_c_tri":
                    l4 = non_exhibition_core

                if l4 is None:
                    prob_first = morning_pred.get(rid)
                    morning_l4 = _evaluate_morning_l4(
                        stadium, grade, cls, prob_first,
                        natl_1, local_1, race_id=rid,
                        avg_st=avg_st, age=age, ex_st=ex_st,
                        boat2_top2=boat2_top2,
                        race_number=race_no_info,
                    )
                    l4_portfolio = _evaluate_l4_portfolio_strong(
                        stadium, grade, cls, natl_1=natl_1,
                        avg_st=avg_st, age=age,
                        boat2_motor_top2=boat2_motor_top2,
                        race_number=race_no_info, wind_speed=wind_speed,
                        n_female=n_female, target_date_iso=target_date,
                    )
                    morning_l4 = _apply_l4_portfolio_strong(morning_l4, l4_portfolio)
                    l4_general_200 = _evaluate_l4_general_200(
                        stadium, grade, cls, natl_1=natl_1,
                        boat2_top2=boat2_top2,
                        boat2_exhibition_time=None,
                        boat3_exhibition_time=None,
                        ex_st=None,
                    )
                    morning_l4 = _apply_l4_general_200(morning_l4, l4_general_200)
                    exacta_niche_watch = _evaluate_exacta_niche(
                        stadium, race_no_info, boat1_motor_top2=boat1_motor_top2,
                        boat2_motor_top2=boat2_motor_top2, boat4_motor_top2=boat4_motor_top2,
                        boat1_natl_1=natl_1, boat4_natl_1=info.get("boat4_natl_1"), n_female=n_female,
                        program_type=program_type,
                        pair_affinity=exacta_pair_affinity.get(rid),
                        grade=grade, weather=weather,
                    )
                    tokoname_12_watch = _evaluate_tokoname_12_exacta_watch(tokoname_12_signal_ctx.get(rid))
                    ashiya_watch = _evaluate_ashiya_boat4_watch(ashiya_boat4_lift.get(rid))
                    ashiya_exacta_watch = _evaluate_ashiya_boat4_lift(ashiya_boat4_lift.get(rid))
                    ashiya_4head_flow_watch = _evaluate_ashiya_4head_flow(ashiya_4head_flow_ctx.get(rid))
                    toda_42_flow_watch = _evaluate_toda_42_flow(toda_42_flow_ctx.get(rid))
                    miyajima_watch = _evaluate_miyajima_boat4_watch(miyajima_boat4_watch.get(rid))
                    tide_tri_watch = _evaluate_tide_tri_signal(
                        stadium,
                        weather=weather,
                        wind_speed=wind_speed,
                        wave_height=info.get("wave_height"),
                        tide_delta_60m_cm=info.get("tide_delta_60m_cm"),
                        tide_range_cm=info.get("tide_range_cm"),
                        is_high_tide_zone=info.get("is_high_tide_zone"),
                        is_low_tide_zone=info.get("is_low_tide_zone"),
                    )
                    omura_123_watch = _evaluate_omura_123_watch(
                        stadium,
                        race_number=race_no_info,
                        boat4_local_1=info.get("boat4_local_1"),
                        boat2_top2=boat2_top2,
                        boat3_top2=info.get("boat3_top2"),
                        wind_speed=wind_speed,
                        boat3_ex_st=info.get("boat3_ex_st"),
                        boat1_exhibition_time=boat1_exhibition_time,
                        best_exhibition_time=info.get("best_exhibition_time"),
                    )
                    tri124_132_watch = _evaluate_tri124_132_trifecta_niche_watch(info)
                    shimonoseki_123_watch = _evaluate_shimonoseki_123_signal(info)
                    gamagori_watch = _evaluate_gamagori_adopted_signal(info)
                    general_c_watch = _evaluate_general_c_watch(
                        stadium, grade, cls,
                        natl_1=natl_1,
                        boat2_top2=boat2_top2,
                        race_number=race_no_info,
                        weather=weather,
                        boat2_exhibition_time=boat2_exhibition_time,
                        boat3_exhibition_time=boat3_exhibition_time,
                        n_female=n_female,
                    )
                    g23_watch = _evaluate_g23_optb_watch(
                        stadium, grade, cls,
                        natl_1=natl_1,
                        local_1=local_1,
                        avg_st=avg_st,
                        age=age,
                        boat2_motor_top2=boat2_motor_top2,
                        weather=weather,
                        n_female=n_female,
                    )
                    win_niche_watch = _evaluate_win_niche(
                        stadium,
                        boat1_top2=boat1_pred_top2,
                        boat3_top2=boat3_pred_top2,
                        boat4_top2=boat4_pred_top2,
                    )
                    morning_l4 = _pick_best_market_signal(
                        morning_l4,
                        exacta_niche_watch,
                        tokoname_12_watch,
                        ashiya_watch,
                        ashiya_exacta_watch,
                        ashiya_4head_flow_watch,
                        toda_42_flow_watch,
                        miyajima_watch,
                        tide_tri_watch,
                        omura_123_watch,
                        boat3_trifecta_niche,
                        tri124_132_watch,
                        general_c_watch,
                        g23_watch,
                        shimonoseki_123_watch,
                        tsu_suminoe_signal,
                        win_niche_watch,
                        candidate_l4,
                        gamagori_watch,
                    )
                    morning_l4 = _prefer_adopted_signal_over_general200(morning_l4, general_c_watch)
                    if morning_l4:
                        if is_rain_excluded and (
                            (not morning_l4.get("is_exacta_niche") and not morning_l4.get("is_win_niche") and not morning_l4.get("is_trifecta_niche"))
                            or morning_l4.get("is_ashiya_boat4_lift")
                            or morning_l4.get("is_ashiya_boat4_watch")
                            or morning_l4.get("uses_rain_filter")
                        ):
                            morning_l4["is_rain"] = True
                            morning_l4["rain_exclusion_active"] = True
                            morning_l4["is_reference"] = True
                            morning_l4["label"] = f"☔{morning_l4['label']} (雨除外)"
                        if is_female_present and not morning_l4.get("is_exacta_niche") and not morning_l4.get("is_win_niche") and not morning_l4.get("is_trifecta_niche"):
                            if n_female == 6:
                                morning_l4["is_venus"] = True
                                morning_l4["is_female_present"] = True
                                morning_l4["label"] = "🌸 Venus L4"
                                morning_l4["recovery"] = 175.5
                                morning_l4["n"] = 502
                            else:
                                if morning_l4.get("level") == "morning_watch_omura_123_tri":
                                    morning_l4 = None
                                else:
                                    morning_l4["is_female_present"] = True
                                    morning_l4["is_reference"] = True
                                    morning_l4["label"] = f"🚫{morning_l4['label']} (女性{n_female}名混在)"
                        if morning_l4:
                            signals.append({
                                "race_id": rid,
                                "tier": "morning_l4",
                                "min_payout": None,
                                "source": "morning_predict",
                                "expected_roi": (morning_l4["recovery"] - 100) / 100,
                                "title": morning_l4["label"],
                                "is_positive_ev": morning_l4["recovery"] >= 130,
                                "weather": weather,
                                "weather_label": WEATHER_LABEL.get(weather),
                                "is_rain": is_rain,
                                "rain_exclusion_active": rain_exclusion_active,
                                "is_rain_excluded": is_rain_excluded,
                                "n_female": n_female,
                                "is_female_present": is_female_present,
                                "l4": morning_l4,
                            })
                    continue

                if l4 is not None and not l4.get("is_exacta_niche") and not l4.get("is_win_niche") and not l4.get("is_trifecta_niche"):
                    prob_first = morning_pred.get(rid)
                    morning_l4 = _evaluate_morning_l4(
                        stadium, grade, cls, prob_first,
                        natl_1, local_1, race_id=rid,
                        avg_st=avg_st, age=age, ex_st=ex_st,
                        boat2_top2=boat2_top2,
                        race_number=race_no_info,
                    )
                    if morning_l4 and morning_l4.get("is_morning_watch_st"):
                        l4["promoted_from_morning_watch_st"] = True
                        l4["promotion_label"] = "朝監視ST→L4"

                # ユーザ指摘 (2026-05-18): 朝予測 (prob_first 0.65-0.85) で候補
                # だったが確定オッズで L4 帯外になったレースも画面表示すべき。
                # 例: 若松 20-12 (prob_first=0.65、T-5 オッズ¥1680 で本命想定外)。
                # → L4 評価が None でも morning_l4 を計算し、「朝候補だった
                #   が本命想定外」として表示 (淡青 reference バッジ)。
                if False and l4 is None:
                    prob_first = morning_pred.get(rid)
                    morning_l4 = _evaluate_morning_l4(
                        stadium, grade, cls, prob_first,
                        natl_1, local_1, race_id=rid,
                        avg_st=avg_st, age=age, ex_st=ex_st,
                        boat2_top2=boat2_top2,
                        race_number=race_no_info,
                    )
                    if morning_l4:
                        # 朝候補だが確定オッズで L4 帯外: 観察用バッジに格下げ
                        # ラベルには既に 🌅 が含まれているため追加しない。
                        # 確定オッズ (本命円) を付記して「想定外」と示す。
                        morning_l4["is_reference"] = True
                        morning_l4["label"] = (
                            f"{morning_l4['label']} (確定¥{mp})"
                        )
                        l4 = morning_l4
                        l4 = _apply_l4_portfolio_strong(l4, l4_portfolio)
                        l4_general_200 = _evaluate_l4_general_200(
                            stadium, grade, cls, natl_1=natl_1,
                            boat2_top2=boat2_top2,
                            boat2_exhibition_time=boat2_exhibition_time,
                            boat3_exhibition_time=boat3_exhibition_time,
                            ex_st=ex_st,
                        )
                        l4 = _apply_l4_general_200(l4, l4_general_200)

                # ☔ 雨レースは L4 候補から除外 (ROI 100% で break-even)
                # ただし最近のレースで「これから ROI 100% かもしれない」と分かるよう
                # バッジは出すが is_rain=True で本日候補リストから除外
                if l4 and is_rain_excluded and (
                    (not l4.get("is_exacta_niche") and not l4.get("is_win_niche") and not l4.get("is_trifecta_niche"))
                    or l4.get("is_ashiya_boat4_lift")
                    or l4.get("is_ashiya_boat4_watch")
                    or l4.get("uses_rain_filter")
                ):
                    l4["is_rain"] = True
                    l4["rain_exclusion_active"] = True
                    l4["is_reference"] = True  # 候補リスト除外フラグ
                    l4["label"] = f"☔{l4['label']} (雨除外)"

                # 🌸 Venus パス: 全女子戦 (n_female=6) は案A除外対象外 (ROI 175.5%, n=502)
                # 混合レース (n_female=1-5) は ROI 低下のため案A除外継続。
                if l4 and not l4.get("is_exacta_niche") and not l4.get("is_win_niche") and not l4.get("is_trifecta_niche") and is_female_present:
                    if n_female == 6:
                        l4["is_venus"] = True
                        l4["is_female_present"] = True
                        l4["label"] = "🌸 Venus L4"
                        l4["recovery"] = 175.5
                        l4["n"] = 502
                        l4["bet"] = "3連単 1-2-3"
                        l4["level"] = "venus_l4"
                    else:
                        l4["is_female_present"] = True
                        l4["is_reference"] = True
                        l4["label"] = f"♀{l4['label']} (女性{n_female}名除外)"

                # 展示タイム補助点 (L4 候補のみ、3連単 1-2-3 の本命買い強弱付け).
                # L4 が無い / 既に Mid_132 等別 universe のものはスキップ.
                if l4 and not l4.get("is_obs_mid_132") \
                        and not l4.get("is_obs_mid_132_tier_a") \
                        and l4.get("bet", "").startswith("3連単 1-2-3"):
                    ex_bonus = _compute_exhibition_bonus_for_l4(rid, ex_bulk)
                    if ex_bonus is not None:
                        l4["ex_bonus_score"] = ex_bonus["score"]
                        l4["ex_bonus_label"] = ex_bonus["score_label"]
                        l4["ex_axis_best_diff"] = ex_bonus["axis_best_diff"]
                        l4["ex_axis_boat2_faster"] = ex_bonus["axis_boat2_faster"]
                        l4["ex_incomplete"] = ex_bonus["incomplete"]
                        l4["ex_recommended_bet_yen"] = ex_bonus["recommended_bet_yen"]
                        l4["ex_expected_roi_pct"] = ex_bonus["expected_roi_pct"]

                signals.append({
                    "race_id": rid,
                    "tier": tier,
                    "min_payout": mp,
                    "source": src,
                    "expected_roi": expected_roi,
                    "title": title,
                    "is_positive_ev": expected_roi > 0,
                    "weather": weather,
                    "weather_label": WEATHER_LABEL.get(weather),
                    "is_rain": is_rain,
                    "rain_exclusion_active": rain_exclusion_active,
                    "is_rain_excluded": is_rain_excluded,
                    "n_female": n_female,
                    "is_female_present": is_female_present,
                    "l4": l4,
                })
            else:
                # === 未確定 (朝判定) → 予測ベース L4 候補 ===
                prob_first = morning_pred.get(rid)
                morning_l4 = _evaluate_morning_l4(stadium, grade, cls, prob_first,
                                                   natl_1, local_1, race_id=rid,
                                                   avg_st=avg_st, age=age, ex_st=ex_st,
                                                   boat2_top2=boat2_top2,
                                                   race_number=race_no_info)
                l4_portfolio = _evaluate_l4_portfolio_strong(
                    stadium, grade, cls, natl_1=natl_1,
                    avg_st=avg_st, age=age,
                    boat2_motor_top2=boat2_motor_top2,
                    race_number=race_no_info, wind_speed=wind_speed,
                    n_female=n_female, target_date_iso=target_date,
                )
                morning_l4 = _apply_l4_portfolio_strong(morning_l4, l4_portfolio)
                l4_general_200 = _evaluate_l4_general_200(
                    stadium, grade, cls, natl_1=natl_1,
                    boat2_top2=boat2_top2,
                    boat2_exhibition_time=None,
                    boat3_exhibition_time=None,
                    ex_st=None,
                )
                morning_l4 = _apply_l4_general_200(morning_l4, l4_general_200)
                exacta_niche = _evaluate_exacta_niche(
                    stadium, race_no_info, boat1_motor_top2=boat1_motor_top2,
                    boat2_motor_top2=boat2_motor_top2, boat4_motor_top2=boat4_motor_top2,
                    boat1_natl_1=natl_1, boat4_natl_1=info.get("boat4_natl_1"), n_female=n_female,
                    program_type=program_type,
                    pair_affinity=exacta_pair_affinity.get(rid),
                    grade=grade, weather=weather,
                )
                tokoname_12_watch = _evaluate_tokoname_12_exacta_watch(tokoname_12_signal_ctx.get(rid))
                ashiya_watch = _evaluate_ashiya_boat4_watch(ashiya_boat4_lift.get(rid))
                ashiya_exacta = _evaluate_ashiya_boat4_lift(ashiya_boat4_lift.get(rid))
                ashiya_4head_flow_watch = _evaluate_ashiya_4head_flow(ashiya_4head_flow_ctx.get(rid))
                toda_42_flow_watch = _evaluate_toda_42_flow(toda_42_flow_ctx.get(rid))
                miyajima_watch = _evaluate_miyajima_boat4_watch(miyajima_boat4_watch.get(rid))
                tide_tri = _evaluate_tide_tri_signal(
                    stadium,
                    weather=weather,
                    wind_speed=wind_speed,
                    wave_height=info.get("wave_height"),
                    tide_delta_60m_cm=info.get("tide_delta_60m_cm"),
                    tide_range_cm=info.get("tide_range_cm"),
                    is_high_tide_zone=info.get("is_high_tide_zone"),
                    is_low_tide_zone=info.get("is_low_tide_zone"),
                )
                omura_123_watch = _evaluate_omura_123_watch(
                    stadium,
                    race_number=race_no_info,
                    boat4_local_1=info.get("boat4_local_1"),
                    boat2_top2=boat2_top2,
                    boat3_top2=info.get("boat3_top2"),
                    wind_speed=wind_speed,
                    boat3_ex_st=info.get("boat3_ex_st"),
                    boat1_exhibition_time=boat1_exhibition_time,
                    best_exhibition_time=info.get("best_exhibition_time"),
                )
                tri124_132_watch = _evaluate_tri124_132_trifecta_niche_watch(info)
                shimonoseki_123_watch = _evaluate_shimonoseki_123_signal(info)
                gamagori_watch = _evaluate_gamagori_adopted_signal(info)
                general_c_watch = _evaluate_general_c_watch(
                    stadium, grade, cls,
                    natl_1=natl_1,
                    boat2_top2=boat2_top2,
                    race_number=race_no_info,
                    weather=weather,
                    boat2_exhibition_time=boat2_exhibition_time,
                    boat3_exhibition_time=boat3_exhibition_time,
                    n_female=n_female,
                )
                g23_watch = _evaluate_g23_optb_watch(
                    stadium, grade, cls,
                    natl_1=natl_1,
                    local_1=local_1,
                    avg_st=avg_st,
                    age=age,
                    boat2_motor_top2=boat2_motor_top2,
                    weather=weather,
                    n_female=n_female,
                )
                win_niche = _evaluate_win_niche(
                    stadium,
                    boat1_top2=boat1_pred_top2,
                    boat3_top2=boat3_pred_top2,
                    boat4_top2=boat4_pred_top2,
                )
                morning_l4 = _pick_best_market_signal(
                    morning_l4,
                    exacta_niche,
                    tokoname_12_watch,
                    ashiya_watch,
                    ashiya_exacta,
                    ashiya_4head_flow_watch,
                    toda_42_flow_watch,
                    miyajima_watch,
                    tide_tri,
                    omura_123_watch,
                    boat3_trifecta_niche,
                    tri124_132_watch,
                    general_c_watch,
                    g23_watch,
                    shimonoseki_123_watch,
                    tsu_suminoe_signal,
                    win_niche,
                    candidate_l4,
                    gamagori_watch,
                )
                morning_l4 = _prefer_adopted_signal_over_general200(morning_l4, general_c_watch)
                if morning_l4:
                    if is_rain_excluded and (
                        (not morning_l4.get("is_exacta_niche") and not morning_l4.get("is_win_niche") and not morning_l4.get("is_trifecta_niche"))
                        or morning_l4.get("is_ashiya_boat4_lift")
                        or morning_l4.get("is_ashiya_boat4_watch")
                        or morning_l4.get("uses_rain_filter")
                    ):
                        morning_l4["is_rain"] = True
                        morning_l4["rain_exclusion_active"] = True
                        morning_l4["is_reference"] = True
                        morning_l4["label"] = f"☔{morning_l4['label']} (雨除外)"
                    if is_female_present and not morning_l4.get("is_exacta_niche") and not morning_l4.get("is_win_niche") and not morning_l4.get("is_trifecta_niche"):
                        if n_female == 6:
                            morning_l4["is_venus"] = True
                            morning_l4["is_female_present"] = True
                            morning_l4["label"] = "🌸 Venus L4"
                            morning_l4["recovery"] = 175.5
                            morning_l4["n"] = 502
                        else:
                            if morning_l4.get("level") == "morning_watch_omura_123_tri":
                                morning_l4 = None
                            else:
                                morning_l4["is_female_present"] = True
                                morning_l4["is_reference"] = True
                                morning_l4["label"] = f"♀{morning_l4['label']} (女性{n_female}名除外)"
                    if morning_l4:
                        signals.append({
                            "race_id": rid,
                            "tier": "morning_l4",
                            "min_payout": None,
                            "source": "morning_predict",
                            "expected_roi": (morning_l4["recovery"] - 100) / 100,
                            "title": morning_l4["label"],
                            "is_positive_ev": morning_l4["recovery"] >= 130,
                            "weather": weather,
                            "weather_label": WEATHER_LABEL.get(weather),
                            "is_rain": is_rain,
                            "rain_exclusion_active": rain_exclusion_active,
                            "is_rain_excluded": is_rain_excluded,
                            "n_female": n_female,
                            "is_female_present": is_female_present,
                            "l4": morning_l4,
                        })

        payload = {
            "date": target_date,
            "n_races": len(signals),
            "n_positive_ev": sum(1 for s in signals if s["is_positive_ev"]),
            "n_l4": sum(1 for s in signals if s["l4"]),
            "n_morning_l4": sum(1 for s in signals if s.get("l4") and s["l4"].get("is_morning")),
            "signals": {s["race_id"]: s for s in signals},
        }
        _write_json_cache(cache_key, payload)
        return jsonify(payload)

    EXCLUDE_B_VENUES = {2, 7, 10, 21, 4, 8, 19, 24}
    STRICT_ODDS_DAILY_START = "2026-05-30"
    MID132_ODDS_CACHE_VERSION = "mid132_1-3-2_odds_v2"
    AMAGASAKI_MOTOR_EXA_CACHE_VERSION = "amagasaki_motor_exa_v1"
    # 芦屋 4-1 は差分調査後に再集計したいので、キャッシュを強制無効化する。
    ASHIYA_BOAT4_EXA_CACHE_VERSION = "ashiya_boat4_exa_v2"
    ASHIYA_4HEAD_FLOW_TRI_CACHE_VERSION = "ashiya_4head_flow_tri_v1"
    HAMANAKO_14_EXA_CACHE_VERSION = "hamanako_14_exa_v2"
    OMURA_14_EXA_CACHE_VERSION = "omura_14_exa_v2"
    KIRYU_WIN2_CACHE_VERSION = "kiryu_win2_v1"
    GENERAL200_CACHE_VERSION = "general200_v1"
    GENERAL_C_TRI_CACHE_VERSION = "general_c_tri_v5"
    BOAT3_NICHE_CACHE_VERSION = "boat3_niche_v3"
    TSU_123_TRI_CACHE_VERSION = "tsu_123_tri_v2"
    SUMINOE_123_TRI_CACHE_VERSION = "suminoe_123_tri_v2"
    TOKUYAMA_123_TRI_CACHE_VERSION = "tokuyama_123_tri_v1"
    TOKUYAMA_12A_EXA_CACHE_VERSION = "tokuyama_12a_exa_v1"
    SHIMONOSEKI_123_TRI_CACHE_VERSION = "shimonoseki_123_tri_v1"
    SHIMONOSEKI_132_TRI_CACHE_VERSION = "shimonoseki_132_tri_v1"
    KOJIMA_124_TRI_CACHE_VERSION = "kojima_124_tri_v1"
    TSU_124_TRI_CACHE_VERSION = "tsu_124_tri_v1"
    OMURA_123_TRI_CACHE_VERSION = "omura_123_tri_v1"
    OMURA_132_TRI_CACHE_VERSION = "omura_132_tri_v2"
    AMAGASAKI_143_TRI_CACHE_VERSION = "amagasaki_143_tri_v1"
    MIYAJIMA_TIDE_132_TRI_CACHE_VERSION = "miyajima_tide_132_tri_v1"
    GAMAGORI_TIDE_132_TRI_CACHE_VERSION = "gamagori_tide_132_tri_v1"
    GAMAGORI_123_GENERAL_PRACTICAL_TRI_CACHE_VERSION = "gamagori_123_general_practical_tri_v1"
    GAMAGORI_13_EXA_CACHE_VERSION = "gamagori_13_exa_v1"
    MARUGAME_TIDE_123_TRI_CACHE_VERSION = "marugame_tide_123_tri_v1"
    FUKUOKA_TIDE_132_TRI_CACHE_VERSION = "fukuoka_tide_132_tri_v1"
    TODA_42_FLOW_TRI_CACHE_VERSION = "toda_42_flow_tri_v1"
    ADOPTED_DAILY_SELECT_VERSION = "adopted_daily_select_v11"
    ADOPTED_DAILY_SELECT_COMPAT_VERSIONS = {
        "adopted_daily_select_v8",
        "adopted_daily_select_v9",
        "adopted_daily_select_v10",
        ADOPTED_DAILY_SELECT_VERSION,
    }
    BET_UNIT_MAP = {"toda_42_flow_tri": 400, "ashiya_4head_flow_tri": 2000}

    def _l4_daily_stats(from_date: str, to_date: str, force_full_scan: bool = False) -> list[dict]:
        """日別の L4 戦略統計を集計。
        L4 条件: 三連単本命 500-1000 + B除外 + 1号艇A1
        集計対象: 単勝 / 2連単1-2 / 3連単1-2-3

        2026-05-30 パフォーマンス改善:
          - l4_daily_stats_cache テーブルに過去日分の集計値を JSON で保存.
          - 過去日 (date < today) のみ cache 優先で取得し、 残りを SQL で計算.
          - Supabase 環境では l4_daily_summary (既存) と併用可能.
        """
        import json as _json
        from datetime import date as _date

        # === A. cache テーブルから過去日分を取得 (高速) ===
        cached_by_date: dict[str, dict] = {}
        try:
            with db_connect() as conn_c:
                cur_c = conn_c.execute(
                    "SELECT race_date, stats_json FROM l4_daily_stats_cache "
                    "WHERE race_date BETWEEN ? AND ?",
                    (from_date, to_date),
                )
                for rdate, sjson in cur_c.fetchall():
                    try:
                        day_d = _json.loads(sjson)
                        # 2026-05-30 以降は実運用ROIとして扱うため、締切前オッズ
                        # ベースで再計算した cache のみ使う。古い cache は確定払戻
                        # 代理を含み、実際に買えた案件とズレる。
                        if rdate >= STRICT_ODDS_DAILY_START and not day_d.get("_strict_odds_only"):
                            continue
                        if day_d.get("_mid132_odds_version") != MID132_ODDS_CACHE_VERSION:
                            continue
                        if day_d.get("_amagasaki_motor_exa_version") != AMAGASAKI_MOTOR_EXA_CACHE_VERSION:
                            continue
                        if day_d.get("_ashiya_boat4_exa_version") != ASHIYA_BOAT4_EXA_CACHE_VERSION:
                            continue
                        if day_d.get("_ashiya_4head_flow_tri_version") != ASHIYA_4HEAD_FLOW_TRI_CACHE_VERSION:
                            continue
                        if day_d.get("_hamanako_14_exa_version") != HAMANAKO_14_EXA_CACHE_VERSION:
                            continue
                        if day_d.get("_omura_14_exa_version") != OMURA_14_EXA_CACHE_VERSION:
                            continue
                        if day_d.get("_kiryu_win2_version") != KIRYU_WIN2_CACHE_VERSION:
                            continue
                        if day_d.get("_general200_version") != GENERAL200_CACHE_VERSION:
                            continue
                        if day_d.get("_general_c_tri_version") != GENERAL_C_TRI_CACHE_VERSION:
                            continue
                        if day_d.get("_boat3_niche_version") != BOAT3_NICHE_CACHE_VERSION:
                            continue
                        if day_d.get("_tsu_123_tri_version") != TSU_123_TRI_CACHE_VERSION:
                            continue
                        if day_d.get("_suminoe_123_tri_version") != SUMINOE_123_TRI_CACHE_VERSION:
                            continue
                        if day_d.get("_tokuyama_123_tri_version") != TOKUYAMA_123_TRI_CACHE_VERSION:
                            continue
                        if day_d.get("_tokuyama_12a_exa_version") != TOKUYAMA_12A_EXA_CACHE_VERSION:
                            continue
                        if day_d.get("_shimonoseki_123_tri_version") != SHIMONOSEKI_123_TRI_CACHE_VERSION:
                            continue
                        if day_d.get("_shimonoseki_132_tri_version") != SHIMONOSEKI_132_TRI_CACHE_VERSION:
                            continue
                        if day_d.get("_kojima_124_tri_version") != KOJIMA_124_TRI_CACHE_VERSION:
                            continue
                        if day_d.get("_tsu_124_tri_version") != TSU_124_TRI_CACHE_VERSION:
                            continue
                        if day_d.get("_omura_123_tri_version") != OMURA_123_TRI_CACHE_VERSION:
                            continue
                        if day_d.get("_omura_132_tri_version") != OMURA_132_TRI_CACHE_VERSION:
                            continue
                        if day_d.get("_amagasaki_143_tri_version") != AMAGASAKI_143_TRI_CACHE_VERSION:
                            continue
                        if day_d.get("_miyajima_tide_132_tri_version") != MIYAJIMA_TIDE_132_TRI_CACHE_VERSION:
                            continue
                        if day_d.get("_gamagori_tide_132_tri_version") != GAMAGORI_TIDE_132_TRI_CACHE_VERSION:
                            continue
                        if day_d.get("_gamagori_123_general_practical_tri_version") != GAMAGORI_123_GENERAL_PRACTICAL_TRI_CACHE_VERSION:
                            continue
                        if day_d.get("_gamagori_13_exa_version") != GAMAGORI_13_EXA_CACHE_VERSION:
                            continue
                        if day_d.get("_marugame_tide_123_tri_version") != MARUGAME_TIDE_123_TRI_CACHE_VERSION:
                            continue
                        if day_d.get("_fukuoka_tide_132_tri_version") != FUKUOKA_TIDE_132_TRI_CACHE_VERSION:
                            continue
                        if day_d.get("_toda_42_flow_tri_version") != TODA_42_FLOW_TRI_CACHE_VERSION:
                            continue
                        if day_d.get("_adopted_daily_select_version") not in ADOPTED_DAILY_SELECT_COMPAT_VERSIONS:
                            continue
                        adopted_metric_prefixes = (
                            "g23_optb_tri",
                            "general_c_tri",
                            "hamanako_14_exa",
                            "gamagori_tide_132_tri",
                            "gamagori_123_general_practical_tri",
                            "gamagori_13_exa",
                            "tsu_123_tri",
                            "tsu_124_tri",
                            "suminoe_123_tri",
                            "amagasaki_143_tri",
                            "kojima_124_tri",
                            "miyajima_tide_132_tri",
                            "tokuyama_123_tri",
                            "tokuyama_12a_exa",
                            "shimonoseki_123_tri",
                            "shimonoseki_132_tri",
                            "fukuoka_tide_132_tri",
                            "ashiya_boat4_exa",
                "ashiya_4head_flow_tri",
                            "ashiya_4head_flow_tri",
                            "marugame_tide_123_tri",
                            "omura_14_exa",
                            "omura_123_tri",
                            "omura_132_tri",
                            "toda_42_flow_tri",
                        )
                        for prefix in adopted_metric_prefixes:
                            day_d.setdefault(f"{prefix}_bets", 0)
                            day_d.setdefault(f"{prefix}_hits", 0)
                            day_d.setdefault(f"{prefix}_pay", 0)
                        cached_by_date[rdate] = day_d
                    except (TypeError, ValueError):
                        pass
        except Exception:  # noqa: BLE001
            # cache テーブル無い (= migration 前) → 既存 SQL で全部計算
            pass
        # force_full_scan は l4_daily_summary の補完を止める意図で使う。
        # 検証済みの日別キャッシュまで全捨てすると、古い採用手法の月別実績が
        # 生SQL再計算だけに寄って欠けやすくなるため、キャッシュは利用する。

        def _finalize_l4_daily_rows(stats_by_date: dict[str, dict]) -> list[dict]:
            niche_bet_keys = (
                "amagasaki_motor_exa",
                "amagasaki_143_tri",
                "ashiya_boat4_exa",
                "hamanako_14_exa",
                "omura_14_exa",
                "kiryu_win2",
                "shimonoseki_123_tri",
                "tsu_124_tri",
                "miyajima_tide_132_tri",
                "gamagori_tide_132_tri",
                "marugame_tide_123_tri",
                "fukuoka_tide_132_tri",
                "toda_42_flow_tri",
            )
            for d in stats_by_date.values():
                for bet in niche_bet_keys:
                    d.setdefault(f"{bet}_bets", 0)
                    d.setdefault(f"{bet}_hits", 0)
                    d.setdefault(f"{bet}_pay", 0)
            for d in stats_by_date.values():
                for bet in ("win", "exa", "tri", "c80", "pro", "sgg12",
                            "gen_tri", "gen_plus_tri", "gen_f1_tri", "gen_200_tri",
                            "prime_tri", "r12_tri", "gen_r12_tri",
                            "toda_7r_tri", "mid_132_tri",
                            "mid_132_tier_a_tri", "venus_tri",
                            "amagasaki_motor_exa", "amagasaki_143_tri", "ashiya_boat4_exa", "fukuoka_wind_exa", "ashiya_4head_flow_tri", "hamanako_14_exa", "omura_14_exa", "kiryu_win2", "general_c_tri",
                            "g23_optb_tri",
                            "tsu_123_tri", "suminoe_123_tri", "shimonoseki_123_tri", "tsu_124_tri",
                            "tokuyama_123_tri", "shimonoseki_132_tri", "kojima_124_tri",
                            "tokuyama_12a_exa",
                            "omura_123_tri", "omura_132_tri",
                            "miyajima_tide_132_tri", "gamagori_tide_132_tri",
                    "gamagori_123_general_practical_tri", "gamagori_13_exa",
                            "marugame_tide_123_tri", "fukuoka_tide_132_tri", "fukuoka_wind_exa", "toda_42_flow_tri"):
                    n = d.get(f"{bet}_bets", 0)
                    pay = d.get(f"{bet}_pay", 0)
                    unit = BET_UNIT_MAP.get(bet, 100)
                    d[f"{bet}_roi"] = (pay - unit * n) / (unit * n) * 100 if n else None
                    d[f"{bet}_recovery"] = pay / (unit * n) * 100 if n else None
                    d[f"{bet}_profit"] = pay - unit * n if n else 0
            return [stats_by_date[d] for d in sorted(stats_by_date)]

        # === B. cache に無い日付を識別 ===
        today_iso = _date.today().isoformat()
        try:
            d_from = _date.fromisoformat(from_date)
            d_to = _date.fromisoformat(to_date)
            all_dates = []
            cur_d = d_from
            from datetime import timedelta as _td
            while cur_d <= d_to:
                all_dates.append(cur_d.isoformat())
                cur_d += _td(days=1)
        except ValueError:
            all_dates = []

        missing_dates = [
            d for d in all_dates
            if d not in cached_by_date or d >= today_iso
        ]
        # 当日分は常に再計算 (cache に保存しない、 リアルタイム性確保)
        # 過去日でも cache に無いものは今回 SQL で取得
        if not missing_dates:
            # 完全 cache hit でも派生指標を補完して返す
            return _finalize_l4_daily_rows(cached_by_date)

        # missing_dates の範囲で SQL 実行 (連続範囲を想定、 非連続は filter で対応)
        sql_from = missing_dates[0]
        sql_to = missing_dates[-1]
        # 既存 SQL 実装に流す (sql_from, sql_to で範囲取得)
        from_date = sql_from
        to_date = sql_to
        with db_connect() as conn:
            try:
                conn.execute("SELECT 1 FROM derived_start_stats LIMIT 1").fetchone()
                _has_derived_start_stats = True
            except Exception:  # noqa: BLE001
                _has_derived_start_stats = False
            _avg_st_expr = (
                "ds1.derived_avg_start_timing_180d"
                if _has_derived_start_stats else
                "e.avg_start_timing"
            )
            _derived_join = (
                "LEFT JOIN derived_start_stats ds1 ON ds1.race_id = r.race_id AND ds1.boat_number = 1"
                if _has_derived_start_stats else
                ""
            )
            # 別途、日別の総レース数を取得 (確定有無に関わらず)
            # _l4_daily_stats の n_total が「確定済のみ」だと
            # 当日朝のように 1 件しか確定してない時 156→1 と見えてしまう
            cur_n = conn.execute("""
                SELECT race_date, COUNT(*) FROM races
                 WHERE race_date BETWEEN ? AND ? GROUP BY race_date
            """, (from_date, to_date)).fetchall()
            n_total_by_date = {row[0]: row[1] for row in cur_n}

            tsu_suminoe_seed = _build_tsu_suminoe_history_seed(conn, from_date)

            # 集計 SQL: race_payouts/odds_trifecta/predictions 全部 LEFT JOIN し、
            # confirmed / odds / morning_miss / morning の 4 ソースで L4 判定する
            cur = conn.execute(f"""
                SELECT
                    r.race_id,
                    r.race_date,
                    r.stadium_number,
                    r.race_grade_number,
                    r.race_number,
                    e.class_number,
                    e.national_top_1_percent AS natl_1,
                    e.local_top_1_percent AS local_1,
                    e.age AS age1,
                    {_avg_st_expr} AS avg_st_180,
                    e.assigned_motor_top_2_percent AS boat1_motor_top2,
                    e2.national_top_2_percent AS boat2_top2,
                    e2.assigned_motor_top_2_percent AS boat2_motor_top2,
                    e2.racer_number AS boat2_racer,
                    e3.racer_number AS boat3_racer,
                    e3.national_top_2_percent AS boat3_top2,
                    e3.national_top_1_percent AS boat3_natl_1,
                    e3.assigned_motor_top_2_percent AS boat3_motor_top2,
                    e4.class_number AS boat4_class,
                    e4.local_top_1_percent AS boat4_local_1,
                    e4.national_top_1_percent AS boat4_natl_1,
                    e4.assigned_motor_top_2_percent AS boat4_motor_top2,
                    e5.assigned_motor_top_2_percent AS boat5_motor_top2,
                    p1t2.prob_top_2 AS boat1_pred_top2,
                    p3t2.prob_top_2 AS boat3_pred_top2,
                    p4t2.prob_top_2 AS boat4_pred_top2,
                    pp.min_pay AS fav_pay,
                    oo.min_odds AS fav_odds,
                    oo.any_in_l4 AS any_in_l4,
                    oo.l4_odds AS l4_odds,
                    oo132.mid_odds AS mid_132_odds,
                    pr.prob_first AS prob_first,
                    res1.boat_number AS w1,
                    res2.boat_number AS w2,
                    res3.boat_number AS w3,
                    pw.payout AS win_pay,
                    pw2.payout AS win2_pay,
                    pe.payout AS exacta_pay,
                    pe13.payout AS exacta_13_pay,
                    pe14.payout AS exacta_14_pay,
                    pe41.payout AS exacta_41_pay,
                    pt.payout AS tri_pay,
                    pt_132.payout AS pay_132,
                    pt_124.payout AS pay_124,
                    pt_143.payout AS pay_143,
                    pv.weather_number AS weather,
                    pv.wind_speed AS wind_speed,
                    pv.wave_height AS wave_height,
                    pv.start_timing_exhibition AS ex_st,
                    NULLIF(pv.exhibition_time, 0) AS boat1_exhibition_time,
                    NULLIF(pv2.exhibition_time, 0) AS boat2_exhibition_time,
                    (
                        SELECT 1 + COUNT(*)
                          FROM race_previews rpst
                         WHERE rpst.race_id = r.race_id
                           AND NULLIF(rpst.start_timing_exhibition, 0) IS NOT NULL
                           AND NULLIF(pv2.start_timing_exhibition, 0) IS NOT NULL
                           AND NULLIF(rpst.start_timing_exhibition, 0) < NULLIF(pv2.start_timing_exhibition, 0)
                    ) AS boat2_ex_st_rank,
                    NULLIF(pv3.exhibition_time, 0) AS boat3_exhibition_time,
                    pv3.start_timing_exhibition AS boat3_ex_st,
                    NULLIF(pv4.exhibition_time, 0) AS boat4_exhibition_time,
                    pv4.start_timing_exhibition AS boat4_ex_st,
                    NULLIF(pv5.exhibition_time, 0) AS boat5_exhibition_time,
                    NULLIF(pv6.exhibition_time, 0) AS boat6_exhibition_time,
                    (
                        SELECT 1 + COUNT(*)
                          FROM race_previews rpex
                         WHERE rpex.race_id = r.race_id
                           AND NULLIF(rpex.exhibition_time, 0) IS NOT NULL
                           AND NULLIF(pv.exhibition_time, 0) IS NOT NULL
                           AND NULLIF(rpex.exhibition_time, 0) < NULLIF(pv.exhibition_time, 0)
                    ) AS boat1_ex_rank,
                    COALESCE(fem.n_female, 0) AS n_female,
                    rt.tide_delta_60m_cm AS tide_delta_60m_cm,
                    rt.tide_range_cm AS tide_range_cm,
                    COALESCE(rt.is_high_tide_zone, 0) AS is_high_tide_zone,
                    COALESCE(rt.is_low_tide_zone, 0) AS is_low_tide_zone
                FROM races r
                LEFT JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
                LEFT JOIN race_entries e2 ON e2.race_id = r.race_id AND e2.boat_number = 2
                LEFT JOIN race_entries e3 ON e3.race_id = r.race_id AND e3.boat_number = 3
                LEFT JOIN race_entries e4 ON e4.race_id = r.race_id AND e4.boat_number = 4
                LEFT JOIN race_entries e5 ON e5.race_id = r.race_id AND e5.boat_number = 5
                LEFT JOIN predictions p1t2 ON p1t2.race_id = r.race_id AND p1t2.boat_number = 1
                {_derived_join}
                LEFT JOIN predictions p3t2 ON p3t2.race_id = r.race_id AND p3t2.boat_number = 3
                LEFT JOIN predictions p4t2 ON p4t2.race_id = r.race_id AND p4t2.boat_number = 4
                LEFT JOIN (
                    SELECT ef.race_id, COUNT(*) AS n_female
                      FROM race_entries ef
                      JOIN racers rc ON rc.racer_number = ef.racer_number
                     WHERE rc.gender = 2
                     GROUP BY ef.race_id
                ) fem ON fem.race_id = r.race_id
                LEFT JOIN (SELECT race_id, MIN(payout) AS min_pay FROM race_payouts
                           WHERE bet_type='trifecta' GROUP BY race_id) pp ON pp.race_id = r.race_id
                LEFT JOIN (SELECT race_id,
                                  MIN(odds) AS min_odds,
                                  -- ユーザ指摘 (2026-05-18): 「いずれかの snapshot
                                  -- で 500-1000 帯にあれば賭ける」運用を反映。
                                  -- T-5 で ¥510, T-1 で ¥470 のように直前で人気化
                                  -- したレースも L4 候補に含める (OR ロジック)。
                                  MAX(CASE WHEN odds >= 5 AND odds < 10 THEN 1 ELSE 0 END) AS any_in_l4,
                                  -- L4 帯にあったときの代表 odds (表示用、T-5min 優先)
                                  COALESCE(
                                      MAX(CASE WHEN snapshot_label='T-5min' AND odds >= 5 AND odds < 10 THEN odds END),
                                      MAX(CASE WHEN snapshot_label='T-4min' AND odds >= 5 AND odds < 10 THEN odds END),
                                      MAX(CASE WHEN snapshot_label='T-3min' AND odds >= 5 AND odds < 10 THEN odds END),
                                      MAX(CASE WHEN snapshot_label='T-15min' AND odds >= 5 AND odds < 10 THEN odds END),
                                      MAX(CASE WHEN snapshot_label='T-1min' AND odds >= 5 AND odds < 10 THEN odds END),
                                      MAX(CASE WHEN snapshot_label='final' AND odds >= 5 AND odds < 10 THEN odds END)
                                  ) AS l4_odds
                             FROM odds_trifecta
                           WHERE combination='1-2-3'
                             AND snapshot_label IN ('T-1min','T-2min','T-3min','T-4min','T-5min','T-15min','final')
                           GROUP BY race_id) oo ON oo.race_id = r.race_id
                LEFT JOIN (SELECT race_id,
                                  COALESCE(
                                      MAX(CASE WHEN snapshot_label='T-5min' AND odds >= 10 AND odds < 20 THEN odds END),
                                      MAX(CASE WHEN snapshot_label='T-4min' AND odds >= 10 AND odds < 20 THEN odds END),
                                      MAX(CASE WHEN snapshot_label='T-3min' AND odds >= 10 AND odds < 20 THEN odds END),
                                      MAX(CASE WHEN snapshot_label='T-15min' AND odds >= 10 AND odds < 20 THEN odds END),
                                      MAX(CASE WHEN snapshot_label='T-1min' AND odds >= 10 AND odds < 20 THEN odds END),
                                      MAX(CASE WHEN snapshot_label='final' AND odds >= 10 AND odds < 20 THEN odds END)
                                  ) AS mid_odds
                             FROM odds_trifecta
                           WHERE combination='1-3-2'
                             AND snapshot_label IN ('T-1min','T-2min','T-3min','T-4min','T-5min','T-15min','final')
                           GROUP BY race_id) oo132 ON oo132.race_id = r.race_id
                LEFT JOIN (SELECT race_id, prob_first FROM predictions
                           WHERE boat_number=1) pr ON pr.race_id = r.race_id
                LEFT JOIN race_previews pv ON pv.race_id = r.race_id AND pv.boat_number = 1
                LEFT JOIN race_previews pv2 ON pv2.race_id = r.race_id AND pv2.boat_number = 2
                LEFT JOIN race_previews pv3 ON pv3.race_id = r.race_id AND pv3.boat_number = 3
                LEFT JOIN race_previews pv4 ON pv4.race_id = r.race_id AND pv4.boat_number = 4
                LEFT JOIN race_previews pv5 ON pv5.race_id = r.race_id AND pv5.boat_number = 5
                LEFT JOIN race_previews pv6 ON pv6.race_id = r.race_id AND pv6.boat_number = 6
                LEFT JOIN race_tides rt ON rt.race_id = r.race_id
                LEFT JOIN race_results res1 ON res1.race_id = r.race_id AND res1.finishing_position=1
                LEFT JOIN race_results res2 ON res2.race_id = r.race_id AND res2.finishing_position=2
                LEFT JOIN race_results res3 ON res3.race_id = r.race_id AND res3.finishing_position=3
                LEFT JOIN race_payouts pw ON pw.race_id = r.race_id AND pw.bet_type='win' AND pw.combination='1'
                LEFT JOIN race_payouts pw2 ON pw2.race_id = r.race_id AND pw2.bet_type='win' AND pw2.combination='2'
                LEFT JOIN race_payouts pe ON pe.race_id = r.race_id AND pe.bet_type='exacta' AND pe.combination='1-2'
                LEFT JOIN race_payouts pe13 ON pe13.race_id = r.race_id AND pe13.bet_type='exacta' AND pe13.combination='1-3'
                LEFT JOIN race_payouts pe14 ON pe14.race_id = r.race_id AND pe14.bet_type='exacta' AND pe14.combination='1-4'
                LEFT JOIN race_payouts pe41 ON pe41.race_id = r.race_id AND pe41.bet_type='exacta' AND pe41.combination='4-1'
                LEFT JOIN race_payouts pt ON pt.race_id = r.race_id AND pt.bet_type='trifecta' AND pt.combination='1-2-3'
                LEFT JOIN race_payouts pt_132 ON pt_132.race_id = r.race_id AND pt_132.bet_type='trifecta' AND pt_132.combination='1-3-2'
                LEFT JOIN race_payouts pt_124 ON pt_124.race_id = r.race_id AND pt_124.bet_type='trifecta' AND pt_124.combination='1-2-4'
                LEFT JOIN race_payouts pt_143 ON pt_143.race_id = r.race_id AND pt_143.bet_type='trifecta' AND pt_143.combination='1-4-3'
                WHERE r.race_date BETWEEN ? AND ?
                ORDER BY r.race_date, r.race_number
            """, (from_date, to_date)).fetchall()

        # 日別に集計
        by_date: dict[str, dict] = {}
        # まず全日付について「枠」を用意 (確定済 0 件の日も表示するため)
        for rdate, n_tot in n_total_by_date.items():
            by_date[rdate] = {
                "date": rdate,
                "n_total": n_tot,
                "n_done": 0,        # 確定済レース数
                "n_l4": 0, "n_l4_a2": 0, "n_l4_all": 0,
                "win_bets": 0, "win_hits": 0, "win_pay": 0,
                "exa_bets": 0, "exa_hits": 0, "exa_pay": 0,
                "tri_bets": 0, "tri_hits": 0, "tri_pay": 0,
                "a2_tri_bets": 0, "a2_tri_hits": 0, "a2_tri_pay": 0,
                "all_tri_bets": 0, "all_tri_hits": 0, "all_tri_pay": 0,
                # 「L4 一般戦」分離追跡:
                # gen_*       = 一般戦 (grade=5) × A1 × B除外 × 本命500-1000 (Base、観察)
                # gen_plus_*  = 上記 × 国1%≥7 (L4+ オーバーレイ、観察)
                # gen_f1_*    = 上記 × 国1%≥7 × 2号 top_2≥40 (= F1, 採用ベース)
                #               ★OOS 検証 ROI 204% / CI [186-222] / Tier 1
                "gen_tri_bets": 0, "gen_tri_hits": 0, "gen_tri_pay": 0,
                "gen_plus_tri_bets": 0, "gen_plus_tri_hits": 0, "gen_plus_tri_pay": 0,
                "gen_f1_tri_bets": 0, "gen_f1_tri_hits": 0, "gen_f1_tri_pay": 0,
                "gen_200_tri_bets": 0, "gen_200_tri_hits": 0, "gen_200_tri_pay": 0,
                # L4-prime / L4-12R / 一般戦×12R 観察 (3 ヶ月実績で採用判断)
                # prime_*   = L4 universe × 11-12R 限定 (ROI 検証 185%)
                # r12_*     = L4 universe × 12R のみ (ROI 検証 193%)
                # gen_r12_* = 一般戦 × 12R 限定 (ROI 検証 189%)
                "prime_tri_bets": 0, "prime_tri_hits": 0, "prime_tri_pay": 0,
                "r12_tri_bets": 0, "r12_tri_hits": 0, "r12_tri_pay": 0,
                "gen_r12_tri_bets": 0, "gen_r12_tri_hits": 0, "gen_r12_tri_pay": 0,
                # 戸田 7R 企画レース観察 (2026-05-19 追加)
                "toda_7r_tri_bets": 0, "toda_7r_tri_hits": 0, "toda_7r_tri_pay": 0,
                # L4-Mid + 1-3-2 観察 (2026-05-19): オッズ10-20倍帯 (ROI 148%)
                "mid_132_tri_bets": 0, "mid_132_tri_hits": 0, "mid_132_tri_pay": 0,
                # Tier A: 3号艇国1%≥7 絞り (ROI 175.5% Tier 1)
                "mid_132_tier_a_tri_bets": 0, "mid_132_tier_a_tri_hits": 0, "mid_132_tier_a_tri_pay": 0,
                "venus_tri_bets": 0, "venus_tri_hits": 0, "venus_tri_pay": 0,
                "amagasaki_motor_exa_bets": 0, "amagasaki_motor_exa_hits": 0, "amagasaki_motor_exa_pay": 0,
                "ashiya_boat4_exa_bets": 0, "ashiya_boat4_exa_hits": 0, "ashiya_boat4_exa_pay": 0,
                "fukuoka_wind_exa_bets": 0, "fukuoka_wind_exa_hits": 0, "fukuoka_wind_exa_pay": 0,
                "ashiya_4head_flow_tri_bets": 0, "ashiya_4head_flow_tri_hits": 0, "ashiya_4head_flow_tri_pay": 0,
                "hamanako_14_exa_bets": 0, "hamanako_14_exa_hits": 0, "hamanako_14_exa_pay": 0,
                "omura_14_exa_bets": 0, "omura_14_exa_hits": 0, "omura_14_exa_pay": 0,
                "kiryu_win2_bets": 0, "kiryu_win2_hits": 0, "kiryu_win2_pay": 0,
                "miyajima_tide_132_tri_bets": 0, "miyajima_tide_132_tri_hits": 0, "miyajima_tide_132_tri_pay": 0,
                "gamagori_tide_132_tri_bets": 0, "gamagori_tide_132_tri_hits": 0, "gamagori_tide_132_tri_pay": 0,
                "gamagori_123_general_practical_tri_bets": 0, "gamagori_123_general_practical_tri_hits": 0, "gamagori_123_general_practical_tri_pay": 0,
                "gamagori_13_exa_bets": 0, "gamagori_13_exa_hits": 0, "gamagori_13_exa_pay": 0,
                "marugame_tide_123_tri_bets": 0, "marugame_tide_123_tri_hits": 0, "marugame_tide_123_tri_pay": 0,
                "fukuoka_tide_132_tri_bets": 0, "fukuoka_tide_132_tri_hits": 0, "fukuoka_tide_132_tri_pay": 0,
                "toda_42_flow_tri_bets": 0, "toda_42_flow_tri_hits": 0, "toda_42_flow_tri_pay": 0,
                "general_c_tri_bets": 0, "general_c_tri_hits": 0, "general_c_tri_pay": 0,
                "boat3_321_tri_bets": 0, "boat3_321_tri_hits": 0, "boat3_321_tri_pay": 0,
                "boat3_324_tri_bets": 0, "boat3_324_tri_hits": 0, "boat3_324_tri_pay": 0,
                "g23_optb_tri_bets": 0, "g23_optb_tri_hits": 0, "g23_optb_tri_pay": 0,
                "tsu_123_tri_bets": 0, "tsu_123_tri_hits": 0, "tsu_123_tri_pay": 0,
                "suminoe_123_tri_bets": 0, "suminoe_123_tri_hits": 0, "suminoe_123_tri_pay": 0,
                "tokuyama_123_tri_bets": 0, "tokuyama_123_tri_hits": 0, "tokuyama_123_tri_pay": 0,
                "tokuyama_12a_exa_bets": 0, "tokuyama_12a_exa_hits": 0, "tokuyama_12a_exa_pay": 0,
                "shimonoseki_132_tri_bets": 0, "shimonoseki_132_tri_hits": 0, "shimonoseki_132_tri_pay": 0,
                "kojima_124_tri_bets": 0, "kojima_124_tri_hits": 0, "kojima_124_tri_pay": 0,
                "omura_123_tri_bets": 0, "omura_123_tri_hits": 0, "omura_123_tri_pay": 0,
                "omura_132_tri_bets": 0, "omura_132_tri_hits": 0, "omura_132_tri_pay": 0,
                "grade_breakdown": {},
            }

        adopted_signal_by_race: dict[str, dict] = {}

        def _record_adopted_signal(
            race_id: str | None,
            rdate: str,
            key: str,
            recovery: float,
            hit: bool,
            pay: int,
        ) -> None:
            if not race_id:
                return
            prev = adopted_signal_by_race.get(race_id)
            payload = {
                "date": rdate,
                "key": key,
                "recovery": float(recovery),
                "hit": bool(hit),
                "pay": int(pay or 0),
            }
            if prev is None or payload["recovery"] > prev["recovery"]:
                adopted_signal_by_race[race_id] = payload

        for row in cur:
            row = tuple(row)
            if len(row) == 60:
                # Backward-compatibility for older row shapes that predate pay_143 and exacta_13.
                row = row[:43] + (None,) + row[43:] + (None,)
            elif len(row) == 61:
                # Backward-compatibility for row shapes that predate exacta_13.
                row = row + (None,)
            elif len(row) != 64:
                logger.warning("unexpected _l4_daily_stats row length=%s race_id=%s", len(row), row[0] if row else None)
                continue
            (race_id, rdate, stadium, grade, race_no, cls, natl_1, local_1_rt, age1_rt, avg_st_180,
             boat1_motor_top2, boat2_top2, boat2_motor_top2, boat2_racer, boat3_racer, boat3_top2, boat3_natl_1, boat3_motor_top2, boat4_class, boat4_local_1, boat4_natl_1, boat4_motor_top2, boat5_motor_top2,
             boat1_pred_top2, boat3_pred_top2, boat4_pred_top2,
             fav_pay, fav_odds, any_in_l4, l4_odds, mid_132_odds, prob_first,
             w1, w2, w3, win_pay, win2_pay, ex_pay, ex13_pay, ex14_pay, ex41_pay, tri_pay, pay_132, pay_124, pay_143, weather, wind_speed, wave_height, ex_st,
             boat1_exhibition_time, boat2_exhibition_time, boat2_ex_st_rank, boat3_exhibition_time, boat3_ex_st, boat4_exhibition_time, boat4_ex_st, boat5_exhibition_time, boat6_exhibition_time, boat1_ex_rank, n_female,
             tide_delta_60m_cm, tide_range_cm, is_high_tide_zone, is_low_tide_zone) = row
            # ☔ 雨除外フィルタ: weather_number=3 (雨) のレースは
            # backtest で ROI 100.8% (break-even) のためベット候補から除外。
            # weather NULL (= 直前情報未取得 or 古いデータ) は通常通り集計。
            is_rain_weather = (weather == 3)
            # ♀ 案A + 🌸 Venus フィルタ
            # 混合レース (n_female=1-5) は ROI 低下のため除外。
            # 全女子戦 (n_female=6: Venus) は ROI 175.5% で独立集計。
            is_venus_race = (n_female == 6)
            if n_female and 0 < n_female < 6:
                continue
            d = by_date.setdefault(rdate, {
                "date": rdate,
                "n_total": n_total_by_date.get(rdate, 0),
                "n_done": 0,
                "n_l4": 0,         # L4 A1 のみ
                "n_l4_a2": 0,      # L4 派生 A2
                "n_l4_all": 0,     # L4 全体 (A1+A2)
                # L4 [A1] のみの集計
                "win_bets": 0, "win_hits": 0, "win_pay": 0,
                "exa_bets": 0, "exa_hits": 0, "exa_pay": 0,
                "tri_bets": 0, "tri_hits": 0, "tri_pay": 0,
                # L4 [A2 派生] の集計
                "a2_tri_bets": 0, "a2_tri_hits": 0, "a2_tri_pay": 0,
                # L4 [A1+A2 合算] の集計
                "all_tri_bets": 0, "all_tri_hits": 0, "all_tri_pay": 0,
                # 一般戦分離追跡 (F1 採用、Base/L4+ は観察用)
                "gen_tri_bets": 0, "gen_tri_hits": 0, "gen_tri_pay": 0,
                "gen_plus_tri_bets": 0, "gen_plus_tri_hits": 0, "gen_plus_tri_pay": 0,
                "gen_f1_tri_bets": 0, "gen_f1_tri_hits": 0, "gen_f1_tri_pay": 0,
                "gen_200_tri_bets": 0, "gen_200_tri_hits": 0, "gen_200_tri_pay": 0,
                # L4-prime / L4-12R / 一般戦×12R 観察
                "prime_tri_bets": 0, "prime_tri_hits": 0, "prime_tri_pay": 0,
                "r12_tri_bets": 0, "r12_tri_hits": 0, "r12_tri_pay": 0,
                "gen_r12_tri_bets": 0, "gen_r12_tri_hits": 0, "gen_r12_tri_pay": 0,
                # 戸田 7R 企画レース観察 (2026-05-19)
                "toda_7r_tri_bets": 0, "toda_7r_tri_hits": 0, "toda_7r_tri_pay": 0,
                # L4-Mid + 1-3-2 観察 (2026-05-19): オッズ10-20倍帯で1-3-2単点 (ROI 148%)
                "mid_132_tri_bets": 0, "mid_132_tri_hits": 0, "mid_132_tri_pay": 0,
                # Tier A: 3号艇国1%≥7 絞り (???293.3%, n=9 ????)
                "mid_132_tier_a_tri_bets": 0, "mid_132_tier_a_tri_hits": 0, "mid_132_tier_a_tri_pay": 0,
                "venus_tri_bets": 0, "venus_tri_hits": 0, "venus_tri_pay": 0,
                "amagasaki_motor_exa_bets": 0, "amagasaki_motor_exa_hits": 0, "amagasaki_motor_exa_pay": 0,
                "ashiya_boat4_exa_bets": 0, "ashiya_boat4_exa_hits": 0, "ashiya_boat4_exa_pay": 0,
                "fukuoka_wind_exa_bets": 0, "fukuoka_wind_exa_hits": 0, "fukuoka_wind_exa_pay": 0,
                "ashiya_4head_flow_tri_bets": 0, "ashiya_4head_flow_tri_hits": 0, "ashiya_4head_flow_tri_pay": 0,
                "hamanako_14_exa_bets": 0, "hamanako_14_exa_hits": 0, "hamanako_14_exa_pay": 0,
                "omura_14_exa_bets": 0, "omura_14_exa_hits": 0, "omura_14_exa_pay": 0,
                "kiryu_win2_bets": 0, "kiryu_win2_hits": 0, "kiryu_win2_pay": 0,
                "general_c_tri_bets": 0, "general_c_tri_hits": 0, "general_c_tri_pay": 0,
                "boat3_321_tri_bets": 0, "boat3_321_tri_hits": 0, "boat3_321_tri_pay": 0,
                "boat3_324_tri_bets": 0, "boat3_324_tri_hits": 0, "boat3_324_tri_pay": 0,
                "g23_optb_tri_bets": 0, "g23_optb_tri_hits": 0, "g23_optb_tri_pay": 0,
                "tsu_123_tri_bets": 0, "tsu_123_tri_hits": 0, "tsu_123_tri_pay": 0,
                "suminoe_123_tri_bets": 0, "suminoe_123_tri_hits": 0, "suminoe_123_tri_pay": 0,
                "shimonoseki_123_tri_bets": 0, "shimonoseki_123_tri_hits": 0, "shimonoseki_123_tri_pay": 0,
                "tokuyama_123_tri_bets": 0, "tokuyama_123_tri_hits": 0, "tokuyama_123_tri_pay": 0,
                "tokuyama_12a_exa_bets": 0, "tokuyama_12a_exa_hits": 0, "tokuyama_12a_exa_pay": 0,
                "shimonoseki_132_tri_bets": 0, "shimonoseki_132_tri_hits": 0, "shimonoseki_132_tri_pay": 0,
                "kojima_124_tri_bets": 0, "kojima_124_tri_hits": 0, "kojima_124_tri_pay": 0,
                "tsu_124_tri_bets": 0, "tsu_124_tri_hits": 0, "tsu_124_tri_pay": 0,
                "omura_123_tri_bets": 0, "omura_123_tri_hits": 0, "omura_123_tri_pay": 0,
                "omura_132_tri_bets": 0, "omura_132_tri_hits": 0, "omura_132_tri_pay": 0,
                "amagasaki_143_tri_bets": 0, "amagasaki_143_tri_hits": 0, "amagasaki_143_tri_pay": 0,
                "miyajima_tide_132_tri_bets": 0, "miyajima_tide_132_tri_hits": 0, "miyajima_tide_132_tri_pay": 0,
                "gamagori_tide_132_tri_bets": 0, "gamagori_tide_132_tri_hits": 0, "gamagori_tide_132_tri_pay": 0,
                "gamagori_123_general_practical_tri_bets": 0, "gamagori_123_general_practical_tri_hits": 0, "gamagori_123_general_practical_tri_pay": 0,
                "gamagori_13_exa_bets": 0, "gamagori_13_exa_hits": 0, "gamagori_13_exa_pay": 0,
                "marugame_tide_123_tri_bets": 0, "marugame_tide_123_tri_hits": 0, "marugame_tide_123_tri_pay": 0,
                "fukuoka_tide_132_tri_bets": 0, "fukuoka_tide_132_tri_hits": 0, "fukuoka_tide_132_tri_pay": 0,
                "toda_42_flow_tri_bets": 0, "toda_42_flow_tri_hits": 0, "toda_42_flow_tri_pay": 0,
                "grade_breakdown": {},
            })
            # 確定済 (race_payouts trifecta あり) ならカウント
            is_done = w1 is not None and w2 is not None and w3 is not None
            if is_done:
                d["n_done"] += 1
            tri_hit = is_done and (w1 == 1 and w2 == 2 and w3 == 3)
            tri_pay_v = int(tri_pay or 0) if tri_hit else 0

            try:
                b2_motor = float(boat2_motor_top2) if boat2_motor_top2 is not None else 0.0
            except (TypeError, ValueError):
                b2_motor = 0.0
            try:
                b1_motor = float(boat1_motor_top2) if boat1_motor_top2 is not None else 0.0
            except (TypeError, ValueError):
                b1_motor = 0.0
            try:
                b3_motor = float(boat3_motor_top2) if boat3_motor_top2 is not None else 0.0
            except (TypeError, ValueError):
                b3_motor = 0.0
            try:
                b4_natl_1 = float(boat4_natl_1) if boat4_natl_1 is not None else 0.0
            except (TypeError, ValueError):
                b4_natl_1 = 0.0
            try:
                b4_motor = float(boat4_motor_top2) if boat4_motor_top2 is not None else 0.0
            except (TypeError, ValueError):
                b4_motor = 0.0
            try:
                rn = int(race_no) if race_no is not None else 0
            except (TypeError, ValueError):
                rn = 0
            if is_done and stadium == 13 and b2_motor >= 55.0:
                d["amagasaki_motor_exa_bets"] += 1
                if w1 == 1 and w2 == 4:
                    d["amagasaki_motor_exa_hits"] += 1
                    d["amagasaki_motor_exa_pay"] += (ex14_pay or 0)

            if is_done and stadium == 6 and grade == 5 and n_female == 0 and not is_rain_weather and float(natl_1 or 0) >= 7.0 and b4_natl_1 >= 5.0 and b4_motor >= 40.0 and b2_motor <= 35.0:
                d["hamanako_14_exa_bets"] += 1
                if w1 == 1 and w2 == 4:
                    d["hamanako_14_exa_hits"] += 1
                    d["hamanako_14_exa_pay"] += int(ex14_pay or 0)

            if is_done and stadium == 24 and grade == 5 and rn in (5, 6, 7, 8) and n_female == 0 and not is_rain_weather and float(natl_1 or 0) >= 7.0 and b4_natl_1 >= 5.0 and b2_motor <= 35.0:
                d["omura_14_exa_bets"] += 1
                if w1 == 1 and w2 == 4:
                    d["omura_14_exa_hits"] += 1
                    d["omura_14_exa_pay"] += int(ex14_pay or 0)

            ex_time_pairs = (
                (1, boat1_exhibition_time),
                (2, boat2_exhibition_time),
                (3, boat3_exhibition_time),
                (4, boat4_exhibition_time),
                (5, boat5_exhibition_time),
                (6, boat6_exhibition_time),
            )
            valid_ex_times = []
            for boat_no, ex_time in ex_time_pairs:
                try:
                    ex_time_v = float(ex_time) if ex_time is not None else None
                except (TypeError, ValueError):
                    ex_time_v = None
                if ex_time_v is not None and ex_time_v > 0:
                    valid_ex_times.append((boat_no, ex_time_v))
            ex_rank_by_boat = {
                boat_no: 1 + sum(1 for _, other_time in valid_ex_times if other_time < boat_time)
                for boat_no, boat_time in valid_ex_times
            }

            if is_done and cls in (1, 2) and n_female == 0 and not is_rain_weather:
                try:
                    b3_natl_1 = float(boat3_natl_1) if boat3_natl_1 is not None else 0.0
                except (TypeError, ValueError):
                    b3_natl_1 = 0.0
                if stadium == 18 and float(boat2_motor_top2 or 0) >= 40.0 and float(boat5_motor_top2 or 0) >= 45.0:
                    _record_adopted_signal(
                        race_id, rdate, "tokuyama_123_tri", 212.0,
                        tri_hit,
                        int(tri_pay_v or 0),
                    )
                if (
                    stadium == 18
                    and float(local_1_rt or 0) >= 7.0
                    and float(boat2_motor_top2 or 0) >= 35.0
                    and boat2_exhibition_time is not None
                    and boat3_exhibition_time is not None
                    and float(boat2_exhibition_time or 99) <= float(boat3_exhibition_time or 99)
                    and int(boat2_ex_st_rank or 99) <= 3
                    and int(boat1_ex_rank or 99) <= 2
                ):
                    _record_adopted_signal(
                        race_id, rdate, "tokuyama_12a_exa", 166.9,
                        w1 == 1 and w2 == 2,
                        int(ex_pay or 0),
                    )
                if stadium == 19 and b3_natl_1 >= 6.0 and b3_motor >= 40.0 and float(avg_st_180 or 9) < 0.16:
                    _record_adopted_signal(
                        race_id, rdate, "shimonoseki_132_tri", 195.3,
                        w1 == 1 and w2 == 3 and w3 == 2,
                        int(pay_132 or 0),
                    )
                if stadium == 16 and 40 <= int(age1_rt or 0) <= 49 and float(boat2_motor_top2 or 0) >= 45.0 and float(boat2_top2 or 0) >= 35.0:
                    _record_adopted_signal(
                        race_id, rdate, "kojima_124_tri", 157.0,
                        w1 == 1 and w2 == 2 and w3 == 4,
                        int(pay_124 or 0),
                    )
                try:
                    boat1_ex_v = float(boat1_exhibition_time) if boat1_exhibition_time is not None else None
                except (TypeError, ValueError):
                    boat1_ex_v = None
                try:
                    boat3_ex_st_v = float(boat3_ex_st) if boat3_ex_st is not None else None
                except (TypeError, ValueError):
                    boat3_ex_st_v = None
                try:
                    wind_speed_v = float(wind_speed) if wind_speed is not None else 0.0
                except (TypeError, ValueError):
                    wind_speed_v = 0.0
                if (
                    stadium == 24
                    and wind_speed_v >= 5.0
                    and float(boat4_local_1 or 0) <= 5.0
                    and float(boat2_top2 or 0) >= float(boat3_top2 or 0)
                    and boat3_ex_st_v is not None and boat3_ex_st_v <= 0.15
                    and boat1_ex_v is not None
                    and (
                        boat1_ex_v - min(
                            v for v in (
                                float(boat1_exhibition_time or 0) or 99,
                                float(boat2_exhibition_time or 0) or 99,
                                float(boat3_exhibition_time or 0) or 99,
                                float(boat4_exhibition_time or 0) or 99,
                                float(boat5_exhibition_time or 0) or 99,
                                float(boat6_exhibition_time or 0) or 99,
                            )
                        ) <= 0.10
                    )
                ):
                    _record_adopted_signal(
                        race_id, rdate, "omura_123_tri", 336.8,
                        tri_hit,
                        int(tri_pay_v or 0),
                    )

                ex_times_for_143 = [
                    float(v) for v in (
                        boat1_exhibition_time,
                        boat2_exhibition_time,
                        boat3_exhibition_time,
                        boat4_exhibition_time,
                        boat5_exhibition_time,
                        boat6_exhibition_time,
                    )
                    if v is not None and float(v) > 0
                ]
                if ex_times_for_143:
                    boat4_rank = 1 + sum(1 for v in ex_times_for_143 if v < float(boat4_exhibition_time or 0)) if boat4_exhibition_time else None
                    boat1_rank = 1 + sum(1 for v in ex_times_for_143 if v < float(boat1_exhibition_time or 0)) if boat1_exhibition_time else None
                    boat4_fastest_diff = (float(boat4_exhibition_time) - min(ex_times_for_143)) if boat4_exhibition_time else None
                else:
                    boat4_rank = None
                    boat1_rank = None
                    boat4_fastest_diff = None

                if (
                    cls in (1, 2)
                    and weather != 3
                    and int(n_female or 0) == 0
                    and stadium == 13
                    and race_no is not None and 10 <= race_no <= 12
                    and boat4_exhibition_time is not None
                    and boat4_natl_1 is not None
                    and boat4_motor_top2 is not None
                    and boat4_ex_st is not None
                    and float(boat4_natl_1) >= 5.0
                    and float(boat4_motor_top2) >= 35.0
                    and (boat4_rank or 99) == 1
                    and (boat1_rank or 99) <= 3
                    and float(boat4_ex_st) <= 0.10
                    and (boat4_fastest_diff or 99) <= 0.05
                ):
                    _record_adopted_signal(
                        race_id, rdate, "amagasaki_143_tri", 347.5,
                        w1 == 1 and w2 == 4 and w3 == 3,
                        int(pay_143 or 0),
                    )

            if is_done and stadium in (9, 12):
                tsu_ctx = _build_tsu_suminoe_ctx(
                    stadium,
                    {
                        "race_number": race_no,
                        "class": cls,
                        "natl_1": natl_1,
                        "local_1": local_1_rt,
                        "avg_st": avg_st_180,
                        "boat1_ex_st": ex_st,
                        "boat1_exhibition_time": boat1_exhibition_time,
                        "boat2_exhibition_time": boat2_exhibition_time,
                        "boat3_exhibition_time": boat3_exhibition_time,
                        "boat4_exhibition_time": boat4_exhibition_time,
                        "boat5_exhibition_time": boat5_exhibition_time,
                        "boat6_exhibition_time": boat6_exhibition_time,
                        "boat2_top2": boat2_top2,
                        "boat2_motor_top2": boat2_motor_top2,
                        "boat3_top2": boat3_top2,
                        "boat3_motor_top2": boat3_motor_top2,
                        "boat2_racer": boat2_racer,
                        "boat3_racer": boat3_racer,
                    },
                    tsu_suminoe_seed,
                )
                tsu_sig = _evaluate_tsu_suminoe_123_signal(tsu_ctx)
                if tsu_sig:
                    alias = "tsu_123_tri" if stadium == 9 else "suminoe_123_tri"
                    _record_adopted_signal(
                        race_id, rdate, alias, float(tsu_sig.get("recovery") or 0.0),
                        w1 == 1 and w2 == 2 and w3 == 3,
                        int(tri_pay or 0),
                    )
                _update_tsu_suminoe_history(tsu_suminoe_seed, stadium, boat2_racer, boat3_racer, w1, w2, w3)

            if is_done and stadium == 19:
                shimonoseki_sig = _evaluate_shimonoseki_123_signal({
                    "stadium": stadium,
                    "race_number": race_no,
                    "class": cls,
                    "natl_1": natl_1,
                    "local_1": local_1_rt,
                    "boat2_motor_top2": boat2_motor_top2,
                    "boat1_ex_st": ex_st,
                    "boat1_exhibition_time": boat1_exhibition_time,
                    "boat2_exhibition_time": boat2_exhibition_time,
                    "boat3_exhibition_time": boat3_exhibition_time,
                    "boat4_exhibition_time": boat4_exhibition_time,
                    "boat5_exhibition_time": boat5_exhibition_time,
                    "boat6_exhibition_time": boat6_exhibition_time,
                })
                if shimonoseki_sig:
                    _record_adopted_signal(
                        race_id, rdate, "shimonoseki_123_tri", float(shimonoseki_sig.get("recovery") or 0.0),
                        tri_hit,
                        int(tri_pay or 0),
                    )

            try:
                _tide_delta = float(tide_delta_60m_cm) if tide_delta_60m_cm is not None else None
            except (TypeError, ValueError):
                _tide_delta = None
            try:
                _tide_range = float(tide_range_cm) if tide_range_cm is not None else None
            except (TypeError, ValueError):
                _tide_range = None
            try:
                _wind_speed = float(wind_speed) if wind_speed is not None else None
            except (TypeError, ValueError):
                _wind_speed = None
            try:
                _wave_height = float(wave_height) if wave_height is not None else None
            except (TypeError, ValueError):
                _wave_height = None

            if is_done and stadium == 17 and _wave_height is not None and _wind_speed is not None and _tide_delta is not None:
                if _wave_height >= 5.0 and 3.0 <= _wind_speed <= 4.0 and _tide_delta <= -15.0:
                    _record_adopted_signal(
                        race_id, rdate, "miyajima_tide_132_tri", 1242.0,
                        w1 == 1 and w2 == 3 and w3 == 2,
                        int(pay_132 or 0),
                    )

            if is_done and stadium == 7 and _tide_delta is not None and _tide_range is not None and _wind_speed is not None:
                if -15.0 < _tide_delta <= -5.0 and _tide_range >= 150.0 and _wind_speed <= 2.0:
                    _record_adopted_signal(
                        race_id, rdate, "gamagori_tide_132_tri", 718.6,
                        w1 == 1 and w2 == 3 and w3 == 2,
                        int(pay_132 or 0),
                    )
                if rn in (9, 10, 11, 12) and float(avg_st_180 or 9) < 0.16 and float(boat2_top2 or 0) >= 35.0 and -15.0 < _tide_delta <= -5.0:
                    _record_adopted_signal(
                        race_id, rdate, "gamagori_123_general_practical_tri", 277.619,
                        tri_hit,
                        int(tri_pay_v or 0),
                    )
                if float(natl_1 or 0) >= 7.0 and b3_motor >= 40.0 and _tide_range >= 150.0 and _wind_speed <= 2.0:
                    _record_adopted_signal(
                        race_id, rdate, "gamagori_13_exa", 154.85,
                        w1 == 1 and w2 == 3,
                        int(ex13_pay or 0),
                    )

            if is_done and stadium == 15 and _tide_range is not None and _wind_speed is not None:
                if 90.0 <= _tide_range < 120.0 and 5.0 <= _wind_speed <= 6.0 and weather == 1:
                    _record_adopted_signal(
                        race_id, rdate, "marugame_tide_123_tri", 431.4,
                        tri_hit,
                        int(tri_pay or 0),
                    )

            if is_done and stadium == 22 and _tide_range is not None and _tide_delta is not None and _wind_speed is not None:
                if _tide_range >= 150.0 and 5.0 <= _tide_delta < 15.0 and _wind_speed <= 2.0:
                    _record_adopted_signal(
                        race_id, rdate, "fukuoka_tide_132_tri", 470.0,
                        w1 == 1 and w2 == 3 and w3 == 2,
                        int(pay_132 or 0),
                    )

            if is_rain_weather:
                continue

            try:
                b1_t2 = float(boat1_pred_top2) if boat1_pred_top2 is not None else 0.0
                b3_t2 = float(boat3_pred_top2) if boat3_pred_top2 is not None else 0.0
                b4_t2 = float(boat4_pred_top2) if boat4_pred_top2 is not None else 0.0
            except (TypeError, ValueError):
                b1_t2 = b3_t2 = b4_t2 = 0.0
            if is_done and stadium == 1 and (b3_t2 - b1_t2) >= 0.08 and (b3_t2 - b4_t2) >= 0.05:
                d["kiryu_win2_bets"] += 1
                if w1 == 2:
                    d["kiryu_win2_hits"] += 1
                    d["kiryu_win2_pay"] += (win2_pay or 0)

            try:
                _natl1 = float(natl_1) if natl_1 is not None else 0.0
                _local1 = float(local_1_rt) if local_1_rt is not None else 0.0
                _avg_st = float(avg_st_180) if avg_st_180 is not None else None
                _age = int(age1_rt) if age1_rt is not None else None
                _ex_st = float(ex_st) if ex_st is not None else None
            except (TypeError, ValueError):
                _natl1 = _local1 = 0.0
                _avg_st = _ex_st = None
                _age = None
            g23_caps = {11: 35.0, 12: 35.0, 16: 40.0, 20: 30.0, 22: 35.0, 23: 40.0}
            g23_match = (
                is_done
                and stadium in g23_caps
                and grade in (3, 4)
                and cls == 1
                and n_female == 0
                and _natl1 >= 7.0
                and _local1 >= 6.0
                and _age is not None and 30 <= _age <= 49
                and _avg_st is not None and _avg_st < 0.155
                and _ex_st is not None and _ex_st < 0.18
                and b2_motor <= g23_caps[stadium]
                and fav_pay is not None and 500 <= int(fav_pay) < 1000
            )
            if g23_match:
                _record_adopted_signal(
                    race_id, rdate, "g23_optb_tri", 204.0,
                    tri_hit,
                    int(tri_pay_v or 0),
                )

            # === L4 候補判定 (T-X オッズ優先、race_payouts MIN フォールバック) ===
            # L4 の正式定義は「3連単 1-2-3 の事前オッズ × 100 が 500-1000円帯」。
            # 結果 (1-2-3 hit/miss) は L4 候補性に影響しない。
            if cls != 1:
                continue

            # is_l4_base 判定: ユーザ指摘 (2026-05-18) で「いずれかの T-X snapshot
            # が L4 帯にあれば候補」とする OR ロジックに変更。
            # 旧: MIN(odds) ベース → 直前で人気化したレース (T-5=¥510→T-1=¥470) が
            #     L4 から漏れる。
            # 新: any_in_l4 フラグ優先 → T-X 6 snapshot 中いずれか 500-1000 なら ✓
            is_l4_base = False
            if any_in_l4 is not None and any_in_l4 == 1:
                # 1 つ以上の snapshot が L4 帯にあった
                is_l4_base = True
            elif fav_odds is not None:
                # フォールバック: any_in_l4 取得不能時 (旧 DB)、MIN(odds) ベース
                fav_int = int(float(fav_odds) * 100)
                if 500 <= fav_int < 1000:
                    is_l4_base = True
            elif fav_pay is not None and rdate < STRICT_ODDS_DAILY_START:
                # T-X オッズ無し → race_payouts MIN ベース (過去日フォールバック)
                pay_int = int(fav_pay)
                if 500 <= pay_int < 1000:
                    is_l4_base = True

            # L4-Mid 判定 (2026-05-19 追加): オッズ 10-20倍帯 (排他 universe)
            # 検証 ???76.5% (n=198) - 1-3-2 単点で観察
            is_l4_mid = False
            if mid_132_odds is not None:
                mid_132_int = int(float(mid_132_odds) * 100)
                if 1000 <= mid_132_int < 2000:
                    is_l4_mid = True

            tri_hit = is_done and (w1 == 1 and w2 == 2 and w3 == 3)
            tri_pay_v = (tri_pay or 0) if tri_hit else 0
            # L4-Mid 1-3-2 ヒット判定
            hit_132 = is_done and (w1 == 1 and w2 == 3 and w3 == 2)
            pay_132_v = (pay_132 or 0) if hit_132 else 0
            # 🌸 Venus 専用集計 (全女子戦: A1+B除外+雨除外済、独立 universe)
            if is_venus_race and is_l4_base and cls == 1 and stadium not in EXCLUDE_B_VENUES:
                if is_done:
                    d["venus_tri_bets"] += 1
                    if tri_hit:
                        d["venus_tri_hits"] += 1
                        d["venus_tri_pay"] += tri_pay_v
                continue  # Venus は通常 L4 集計に含めない

            # === L4-Mid + 1-3-2 観察集計 (B除外チェック前、stadium B除外でも cls/odds 条件は満たす)
            # backlog: L4 universe と異なる universe (10-20倍帯)
            # B除外条件: stadium in EXCLUDE_B_VENUES → 弾く (L4 メインと同様)
            if is_l4_mid and is_done and cls == 1 and stadium not in EXCLUDE_B_VENUES:
                d["mid_132_tri_bets"] += 1
                if hit_132:
                    d["mid_132_tri_hits"] += 1
                    d["mid_132_tri_pay"] += pay_132_v
                # Tier A 判定 (2026-05-19): 3号艇 国1% ≥ 7%
                # 4年検証 ???293.3% / n=9 ???? 認定
                try:
                    _b3_n1 = float(boat3_natl_1) if boat3_natl_1 is not None else 0.0
                except (TypeError, ValueError):
                    _b3_n1 = 0.0
                if _b3_n1 >= 7.0:
                    d["mid_132_tier_a_tri_bets"] += 1
                    if hit_132:
                        d["mid_132_tier_a_tri_hits"] += 1
                        d["mid_132_tier_a_tri_pay"] += pay_132_v
            # L4-prime/12R 観察用 race_number ガード
            try:
                rn = int(race_no) if race_no is not None else 0
            except (TypeError, ValueError):
                rn = 0
            is_prime = rn in (11, 12)
            is_r12 = rn == 12

            # === 企画レース観察集計 (2026-05-19 追加、B除外を無視) ===
            # 戸田 7R (B除外内、検証 ROI 171.5%, n=106)。
            # B除外 check の前に処理することで戸田も観察対象に含まれる。
            if is_l4_base and is_done:
                if stadium == 2 and rn == 7:
                    d["toda_7r_tri_bets"] += 1
                    if tri_hit:
                        d["toda_7r_tri_hits"] += 1
                        d["toda_7r_tri_pay"] += tri_pay_v

            # B除外チェック: 通常 L4 集計のみ skip (上の企画観察は処理済)
            if stadium in EXCLUDE_B_VENUES:
                continue

            # === L4-prime / L4-12R 観察集計 (L4 universe 全体、確定済のみ) ===
            # 一般戦 + L4 本流 (SG/G1/G2/G3) 両方を含む、is_l4_base 通過のみ
            if is_l4_base and is_done:
                if is_prime:
                    d["prime_tri_bets"] += 1
                    if tri_hit:
                        d["prime_tri_hits"] += 1
                        d["prime_tri_pay"] += tri_pay_v
                if is_r12:
                    d["r12_tri_bets"] += 1
                    if tri_hit:
                        d["r12_tri_hits"] += 1
                        d["r12_tri_pay"] += tri_pay_v

            # === 一般戦 (grade=5): 別カウンタで分離追跡 ===
            # 採用ベース: F1 (国1%≥7 + 2号 top_2≥40) → gen_f1_tri_*
            # 観察用     : gen_tri_* (Base), gen_plus_tri_* (× 国1%≥7), gen_r12_* (× 12R)
            if grade == 5:
                if is_l4_base and is_done:
                    d["gen_tri_bets"] += 1
                    if tri_hit:
                        d["gen_tri_hits"] += 1
                        d["gen_tri_pay"] += tri_pay_v
                    # 一般戦×12R 観察 (F1 と独立、ROI 検証 189%)
                    if is_r12:
                        d["gen_r12_tri_bets"] += 1
                        if tri_hit:
                            d["gen_r12_tri_hits"] += 1
                            d["gen_r12_tri_pay"] += tri_pay_v
                    try:
                        n1 = float(natl_1) if natl_1 is not None else 0.0
                    except (TypeError, ValueError):
                        n1 = 0.0
                    try:
                        b2 = float(boat2_top2) if boat2_top2 is not None else 0.0
                    except (TypeError, ValueError):
                        b2 = 0.0
                    # 観察 gen_plus_*  : 一般戦 × 国1%≥7 (L4+ overlay, ROI ~166%)
                    if n1 >= 7.0:
                        d["gen_plus_tri_bets"] += 1
                        if tri_hit:
                            d["gen_plus_tri_hits"] += 1
                            d["gen_plus_tri_pay"] += tri_pay_v
                    # ★採用 gen_f1_* : F1 = 一般戦 × 国1%≥7 × 2号 top_2≥40
                    # OOS Tier 1 (4年 ROI 204% / 直近 220% / CI 下限 ≥150%)
                    if (
                        n1 >= 7.0
                        and b2 >= 40.0
                        and boat2_exhibition_time is not None
                        and boat3_exhibition_time is not None
                        and float(boat2_exhibition_time) < float(boat3_exhibition_time)
                    ):
                        d["gen_200_tri_bets"] += 1
                        if tri_hit:
                            d["gen_200_tri_hits"] += 1
                            d["gen_200_tri_pay"] += tri_pay_v
                    general_c_l4_band_ok = False
                    try:
                        if any_in_l4 is not None and int(any_in_l4) == 1:
                            general_c_l4_band_ok = True
                        elif fav_pay is not None and 500 <= int(fav_pay) < 1000:
                            # Keep historical adopted-signal counting aligned with
                            # the live candidate list: if the snapshot flag is
                            # missing or false, fall back to the confirmed
                            # 500-1000 band when regenerating past-day stats.
                            general_c_l4_band_ok = True
                    except (TypeError, ValueError):
                        general_c_l4_band_ok = False

                    if (
                        grade == 5
                        and stadium not in EXCLUDE_B_VENUES
                        and n_female == 0
                        and weather != 3
                        and general_c_l4_band_ok
                        and n1 >= 7.0
                        and b2 >= 40.0
                        and rn in (10, 11, 12)
                        and boat2_exhibition_time is not None
                        and boat3_exhibition_time is not None
                        and float(boat2_exhibition_time) <= float(boat3_exhibition_time)
                    ):
                        _record_adopted_signal(
                            race_id, rdate, "general_c_tri", 154.1,
                            tri_hit,
                            int(tri_pay_v or 0),
                        )
                    if n1 >= 7.0 and b2 >= 40.0:
                        d["gen_f1_tri_bets"] += 1
                        if tri_hit:
                            d["gen_f1_tri_hits"] += 1
                            d["gen_f1_tri_pay"] += tri_pay_v
                        # ★ F1 は採用ベースなので、日別詳細の n_l4 + 3 点買いすべてに統合。
                        # 実運用では SG/G1/G2/G3 と同じく 単勝1 / 2連単1-2 / 3連単1-2-3
                        # の 3 点を各 ¥100 で買うため、それぞれ加算する。
                        d["n_l4"] += 1
                        # 単勝 (1号艇)
                        d["win_bets"] += 1
                        if w1 == 1:
                            d["win_hits"] += 1
                            d["win_pay"] += (win_pay or 0)
                        # 2連単 1-2
                        d["exa_bets"] += 1
                        if w1 == 1 and w2 == 2:
                            d["exa_hits"] += 1
                            d["exa_pay"] += (ex_pay or 0)
                        # 3連単 1-2-3
                        d["tri_bets"] += 1
                        if tri_hit:
                            d["tri_hits"] += 1
                            d["tri_pay"] += tri_pay_v
                        # グレード別 breakdown にも記録 (key=5 一般戦)
                        gb = d["grade_breakdown"].setdefault(5, {"n": 0, "tri_hits": 0, "tri_pay": 0})
                        gb["n"] += 1
                        if tri_hit:
                            gb["tri_hits"] += 1
                            gb["tri_pay"] += tri_pay_v
                continue  # 一般戦は L4 本流集計に含めない (F1 のみ上で加算済)

            # L4 戦略は 1号艇 A1 のみ。A2 は対象外なので集計しない。
            if is_l4_base and cls == 1:
                d["n_l4"] += 1
                d["n_l4_all"] += 1
                # bets は「確定済」のみカウント (未確定は ROI 母数に入れない)
                if is_done:
                    d["win_bets"] += 1
                    if w1 == 1:
                        d["win_hits"] += 1
                        d["win_pay"] += (win_pay or 0)
                    d["exa_bets"] += 1
                    if w1 == 1 and w2 == 2:
                        d["exa_hits"] += 1
                        d["exa_pay"] += (ex_pay or 0)
                    d["tri_bets"] += 1
                    if tri_hit:
                        d["tri_hits"] += 1
                        d["tri_pay"] += tri_pay_v
                    # グレード別
                    g_key = grade or 5
                    gb = d["grade_breakdown"].setdefault(g_key, {"n": 0, "tri_hits": 0, "tri_pay": 0})
                    gb["n"] += 1
                    if tri_hit:
                        gb["tri_hits"] += 1
                        gb["tri_pay"] += tri_pay_v


        # boat3 trifecta niche (321 / 324)
        try:
            from collections import defaultdict
            with db_connect() as conn:
                candidate_rows = conn.execute(
                """
                SELECT r.race_id,
                       r.race_date,
                       r.stadium_number,
                       r.race_number,
                       e3.assigned_motor_top_2_percent AS boat3_motor_top2,
                       pv3.start_timing_exhibition AS boat3_ex_st,
                       pv3.exhibition_time AS boat3_ex_time,
                       CASE WHEN EXISTS (
                           SELECT 1
                             FROM race_results rr_done
                            WHERE rr_done.race_id = r.race_id
                              AND rr_done.finishing_position = 1
                       ) THEN 1 ELSE 0 END AS is_done,
                       COALESCE(pt321.payout, 0) AS pay321,
                       COALESCE(pt324.payout, 0) AS pay324,
                       exall.best_ex
                  FROM races r
                  JOIN race_entries e3
                    ON e3.race_id = r.race_id
                   AND e3.boat_number = 3
                  JOIN race_previews pv3
                    ON pv3.race_id = r.race_id
                   AND pv3.boat_number = 3
                  LEFT JOIN race_payouts pt321
                    ON pt321.race_id = r.race_id
                   AND pt321.bet_type = 'trifecta'
                   AND pt321.combination = '3-2-1'
                  LEFT JOIN race_payouts pt324
                    ON pt324.race_id = r.race_id
                   AND pt324.bet_type = 'trifecta'
                   AND pt324.combination = '3-2-4'
                  LEFT JOIN (
                    SELECT race_id, MIN(exhibition_time) AS best_ex
                      FROM race_previews
                     WHERE exhibition_time IS NOT NULL
                       AND exhibition_time > 0
                     GROUP BY race_id
                  ) exall
                    ON exall.race_id = r.race_id
                 WHERE r.race_date <= ?
                   AND pv3.exhibition_time IS NOT NULL
                   AND pv3.exhibition_time > 0
                   AND pv3.start_timing_exhibition IS NOT NULL
                 ORDER BY r.race_date, r.stadium_number, r.race_number, r.race_id
                """,
                (to_date,),
                ).fetchall()

                venue_roll = defaultdict(lambda: {"sum": 0.0, "n": 0})
                current_date = None
                pending_rows = []

                def _flush_pending(rows_for_date):
                    same_day_roll = defaultdict(lambda: {"sum": 0.0, "n": 0})

                    for race_id, rdate, stadium_no, race_no, motor_top2, ex_st3, ex_time3, is_done_row, pay321, pay324, best_ex in rows_for_date:
                        if motor_top2 is None or ex_st3 is None or ex_time3 is None or best_ex is None:
                            continue
                        ex_time_v = float(ex_time3)
                        day_stat = same_day_roll[int(stadium_no)]
                        same_day_avg = (day_stat["sum"] / day_stat["n"]) if day_stat["n"] else None
                        venue_stat = venue_roll[int(stadium_no)]
                        venue_avg = (venue_stat["sum"] / venue_stat["n"]) if venue_stat["n"] else None
                        same_day_gain = (same_day_avg - ex_time_v) if same_day_avg is not None else None
                        venue_gain = (venue_avg - ex_time_v) if venue_avg is not None else None
                        motor_v = float(motor_top2)
                        ex_st_v = float(ex_st3)

                        fastest_diff = ex_time_v - float(best_ex)
                        matched_levels: list[str] = []
                        if (
                            motor_v >= 40.0
                            and ex_st_v <= 0.06
                            and same_day_gain is not None and same_day_gain >= 0.08
                        ):
                            matched_levels.append("trifecta_niche_324_core")
                        if (
                            motor_v >= 35.0
                            and ex_st_v <= 0.08
                            and same_day_gain is not None and same_day_gain >= 0.08
                        ):
                            matched_levels.append("trifecta_niche_321_core")
                        if (
                            motor_v >= 35.0
                            and ex_st_v <= 0.08
                            and same_day_gain is not None and same_day_gain >= 0.08
                            and fastest_diff <= 0.08
                        ):
                            matched_levels.append("trifecta_niche_321_boost")
                        if (
                            ex_st_v <= 0.06
                            and same_day_gain is not None and same_day_gain >= 0.08
                            and venue_gain is not None and venue_gain >= 0.08
                        ):
                            matched_levels.append("trifecta_niche_324_venue")

                        chosen_level = None
                        if matched_levels:
                            priority = {
                                "trifecta_niche_324_core": 2316.9,
                                "trifecta_niche_324_venue": 1617.6,
                                "trifecta_niche_321_boost": 1587.7,
                                "trifecta_niche_321_core": 1518.7,
                            }
                            chosen_level = max(matched_levels, key=lambda key: priority.get(key, float("-inf")))

                        if from_date <= rdate <= to_date and is_done_row:
                            if chosen_level in ("trifecta_niche_321_core", "trifecta_niche_321_boost"):
                                _record_adopted_signal(
                                    race_id,
                                    rdate,
                                    "boat3_321_tri",
                                    priority.get(chosen_level, 0.0),
                                    bool(pay321),
                                    int(pay321 or 0),
                                )
                            if chosen_level in ("trifecta_niche_324_core", "trifecta_niche_324_venue"):
                                _record_adopted_signal(
                                    race_id,
                                    rdate,
                                    "boat3_324_tri",
                                    priority.get(chosen_level, 0.0),
                                    bool(pay324),
                                    int(pay324 or 0),
                                )

                        day_stat["sum"] += ex_time_v
                        day_stat["n"] += 1

                    for _race_id, _rdate, stadium_no, _race_no, _motor_top2, _ex_st3, ex_time3, _is_done, _pay321, _pay324, _best_ex in rows_for_date:
                        if ex_time3 is None:
                            continue
                        stat = venue_roll[int(stadium_no)]
                        stat["sum"] += float(ex_time3)
                        stat["n"] += 1

                for race_id, rdate, stadium_no, race_no, motor_top2, ex_st3, ex_time3, is_done_row, pay321, pay324, best_ex in candidate_rows:
                    if motor_top2 is None or ex_st3 is None or ex_time3 is None or best_ex is None:
                        continue
                    if current_date is None:
                        current_date = rdate
                    if rdate != current_date:
                        _flush_pending(pending_rows)
                        pending_rows = []
                        current_date = rdate
                    pending_rows.append((race_id, rdate, stadium_no, race_no, motor_top2, ex_st3, ex_time3, is_done_row, pay321, pay324, best_ex))
                if pending_rows:
                    _flush_pending(pending_rows)
        except Exception as e:
            logger.warning("boat3 trifecta niche daily stats failed: %s", e)

        # Ashiya exacta 4-1 exhibition lift
        try:
            from collections import defaultdict
            from datetime import datetime as _wf_dt, timedelta as _wf_td
            with db_connect() as conn:
                candidate_rows = conn.execute(
                """
                SELECT r.race_id,
                       r.race_date,
                       e4.racer_number,
                       e4.assigned_motor_number,
                       COALESCE(pv4.course_number, 4) AS boat4_ex_course,
                       pv4.exhibition_time AS boat4_ex_time,
                       pv4.start_timing_exhibition AS boat4_ex_st,
                       CASE WHEN EXISTS (
                           SELECT 1
                             FROM race_results rr_done
                            WHERE rr_done.race_id = r.race_id
                              AND rr_done.finishing_position = 1
                       ) THEN 1 ELSE 0 END AS is_done,
                       COALESCE(pe41.payout, 0) AS ex41_pay
                  FROM races r
                  JOIN race_entries e4
                    ON e4.race_id = r.race_id
                   AND e4.boat_number = 4
                  LEFT JOIN race_previews pv4
                    ON pv4.race_id = r.race_id
                   AND pv4.boat_number = 4
                  LEFT JOIN race_payouts pe41
                    ON pe41.race_id = r.race_id
                   AND pe41.bet_type = 'exacta'
                   AND pe41.combination = '4-1'
                 WHERE r.race_date BETWEEN ? AND ?
                   AND r.stadium_number = 21
                   AND e4.racer_number IS NOT NULL
                   AND e4.assigned_motor_number IS NOT NULL
                 ORDER BY r.race_date, r.race_id
                """,
                (from_date, to_date),
                ).fetchall()

                if candidate_rows:
                    hist_from = (_wf_dt.fromisoformat(from_date).date() - _wf_td(days=730)).isoformat()
                    racer_hist_rows = conn.execute(
                    """
                    SELECT r.race_date,
                           e.racer_number,
                           rr.start_timing,
                           rr.finishing_position
                      FROM races r
                      JOIN race_entries e
                        ON e.race_id = r.race_id
                      JOIN race_results rr
                        ON rr.race_id = e.race_id
                       AND rr.boat_number = e.boat_number
                     WHERE r.stadium_number = 21
                       AND rr.course_number = 4
                       AND r.race_date >= ?
                       AND r.race_date < ?
                       AND e.racer_number IS NOT NULL
                       AND rr.start_timing IS NOT NULL
                       AND rr.finishing_position IS NOT NULL
                     ORDER BY r.race_date, r.race_id
                    """,
                    (hist_from, to_date),
                    ).fetchall()
                    racer_ex_rows = conn.execute(
                    """
                    SELECT r.race_date,
                           e.racer_number,
                           pv.exhibition_time
                      FROM races r
                      JOIN race_entries e
                        ON e.race_id = r.race_id
                      JOIN race_previews pv
                        ON pv.race_id = e.race_id
                       AND pv.boat_number = e.boat_number
                     WHERE r.stadium_number = 21
                       AND r.race_date >= ?
                       AND r.race_date < ?
                       AND e.racer_number IS NOT NULL
                       AND pv.exhibition_time IS NOT NULL
                       AND pv.exhibition_time > 0
                     ORDER BY r.race_date, r.race_id
                    """,
                    (hist_from, to_date),
                    ).fetchall()
                    motor_ex_rows = conn.execute(
                    """
                    SELECT r.race_date,
                           e.assigned_motor_number,
                           pv.exhibition_time
                      FROM races r
                      JOIN race_entries e
                        ON e.race_id = r.race_id
                      JOIN race_previews pv
                        ON pv.race_id = e.race_id
                       AND pv.boat_number = e.boat_number
                     WHERE r.stadium_number = 21
                       AND r.race_date >= ?
                       AND r.race_date < ?
                       AND e.assigned_motor_number IS NOT NULL
                       AND pv.exhibition_time IS NOT NULL
                       AND pv.exhibition_time > 0
                     ORDER BY r.race_date, r.race_id
                    """,
                    (hist_from, to_date),
                    ).fetchall()
                    race_best_rows = conn.execute(
                    """
                    SELECT rp.race_id, MIN(rp.exhibition_time) AS best_ex
                      FROM race_previews rp
                      JOIN races r ON r.race_id = rp.race_id
                     WHERE r.race_date BETWEEN ? AND ?
                       AND r.stadium_number = 21
                       AND rp.exhibition_time IS NOT NULL
                       AND rp.exhibition_time > 0
                     GROUP BY rp.race_id
                    """,
                    (from_date, to_date),
                    ).fetchall()
                    race_best_map = {rid: float(best_ex) for rid, best_ex in race_best_rows if best_ex is not None}

                    racer_stats = defaultdict(lambda: {"n": 0, "st_sum": 0.0, "wins": 0})
                    racer_ex_hist = defaultdict(list)
                    motor_ex_hist = defaultdict(list)
                    racer_hist_idx = 0
                    racer_ex_idx = 0
                    motor_ex_idx = 0

                    for race_id, rdate, racer_no, motor_no, ex_course, boat4_ex_time, boat4_ex_st, is_done_row, ex41_pay in candidate_rows:
                        while racer_hist_idx < len(racer_hist_rows) and racer_hist_rows[racer_hist_idx][0] < rdate:
                            _d, hracer, hst, hpos = racer_hist_rows[racer_hist_idx]
                            stat = racer_stats[hracer]
                            stat["n"] += 1
                            stat["st_sum"] += float(hst)
                            if int(hpos) == 1:
                                stat["wins"] += 1
                            racer_hist_idx += 1
                        while racer_ex_idx < len(racer_ex_rows) and racer_ex_rows[racer_ex_idx][0] < rdate:
                            _d, hracer, hexv = racer_ex_rows[racer_ex_idx]
                            hist = racer_ex_hist[hracer]
                            if len(hist) < 20:
                                hist.append(float(hexv))
                            racer_ex_idx += 1
                        while motor_ex_idx < len(motor_ex_rows) and motor_ex_rows[motor_ex_idx][0] < rdate:
                            _d, hmotor, hexv = motor_ex_rows[motor_ex_idx]
                            hist = motor_ex_hist[str(hmotor)]
                            if len(hist) < 12:
                                hist.append(float(hexv))
                            motor_ex_idx += 1

                        stat = racer_stats[racer_no]
                        r4_n = int(stat["n"] or 0)
                        if r4_n < 8:
                            continue
                        r4_avg_st = stat["st_sum"] / r4_n
                        r4_win_rate = stat["wins"] / r4_n
                        r_ex_list = racer_ex_hist.get(racer_no, [])
                        m_ex_list = motor_ex_hist.get(str(motor_no), [])
                        if len(r_ex_list) < 8 or len(m_ex_list) < 5:
                            continue
                        if boat4_ex_time is None or boat4_ex_st is None:
                            continue
                        best_ex = race_best_map.get(race_id)
                        if best_ex is None:
                            continue
                        boat4_ex_v = float(boat4_ex_time)
                        boat4_ex_st_v = float(boat4_ex_st)
                        racer_avg_ex = sum(r_ex_list) / len(r_ex_list)
                        motor_avg_ex = sum(m_ex_list) / len(m_ex_list)
                        best_diff = boat4_ex_v - best_ex
                        racer_gain = racer_avg_ex - boat4_ex_v
                        motor_gain = motor_avg_ex - boat4_ex_v

                        if not (
                            int(ex_course or 4) == 4
                            and r4_avg_st <= 0.16
                            and r4_win_rate >= 0.08
                            and best_diff <= 0.08
                            and racer_gain >= 0.08
                            and motor_gain >= 0.05
                            and boat4_ex_st_v <= 0.10
                        ):
                            continue

                        if not is_done_row:
                            continue
                        _record_adopted_signal(
                            race_id, rdate, "ashiya_boat4_exa", 311.5,
                            bool(ex41_pay),
                            int(ex41_pay or 0),
                        )
        except Exception as e:
            logger.warning("ashiya boat4 exacta daily stats failed: %s", e)

        # Ashiya 4-head flow tri (4-1/2/3/5/6 - all 20 tickets)
        try:
            from collections import defaultdict
            from datetime import datetime as _wf_dt, timedelta as _wf_td
            with db_connect() as conn:
                candidate_rows = conn.execute(
                """
                SELECT r.race_id, r.race_date,
                       e1.national_top_1_percent AS boat1_natl1,
                       e1.assigned_motor_top_2_percent AS boat1_motor_top2,
                       e2.assigned_motor_top_2_percent AS boat2_motor_top2,
                       e3.assigned_motor_top_2_percent AS boat3_motor_top2,
                       e4.racer_number AS boat4_racer,
                       e4.assigned_motor_number AS boat4_motor_number,
                       COALESCE(tp412.payout, 0) + COALESCE(tp413.payout, 0) + COALESCE(tp415.payout, 0) + COALESCE(tp416.payout, 0)
                     + COALESCE(tp421.payout, 0) + COALESCE(tp423.payout, 0) + COALESCE(tp425.payout, 0) + COALESCE(tp426.payout, 0)
                     + COALESCE(tp431.payout, 0) + COALESCE(tp432.payout, 0) + COALESCE(tp435.payout, 0) + COALESCE(tp436.payout, 0)
                     + COALESCE(tp451.payout, 0) + COALESCE(tp452.payout, 0) + COALESCE(tp453.payout, 0) + COALESCE(tp456.payout, 0)
                     + COALESCE(tp461.payout, 0) + COALESCE(tp462.payout, 0) + COALESCE(tp463.payout, 0) + COALESCE(tp465.payout, 0)
                       AS tri4all_pay
                  FROM races r
                  JOIN race_entries e1 ON e1.race_id = r.race_id AND e1.boat_number = 1
                  JOIN race_entries e2 ON e2.race_id = r.race_id AND e2.boat_number = 2
                  JOIN race_entries e3 ON e3.race_id = r.race_id AND e3.boat_number = 3
                  JOIN race_entries e4 ON e4.race_id = r.race_id AND e4.boat_number = 4
                  LEFT JOIN race_payouts tp412 ON tp412.race_id = r.race_id AND tp412.bet_type='trifecta' AND tp412.combination='4-1-2'
                  LEFT JOIN race_payouts tp413 ON tp413.race_id = r.race_id AND tp413.bet_type='trifecta' AND tp413.combination='4-1-3'
                  LEFT JOIN race_payouts tp415 ON tp415.race_id = r.race_id AND tp415.bet_type='trifecta' AND tp415.combination='4-1-5'
                  LEFT JOIN race_payouts tp416 ON tp416.race_id = r.race_id AND tp416.bet_type='trifecta' AND tp416.combination='4-1-6'
                  LEFT JOIN race_payouts tp421 ON tp421.race_id = r.race_id AND tp421.bet_type='trifecta' AND tp421.combination='4-2-1'
                  LEFT JOIN race_payouts tp423 ON tp423.race_id = r.race_id AND tp423.bet_type='trifecta' AND tp423.combination='4-2-3'
                  LEFT JOIN race_payouts tp425 ON tp425.race_id = r.race_id AND tp425.bet_type='trifecta' AND tp425.combination='4-2-5'
                  LEFT JOIN race_payouts tp426 ON tp426.race_id = r.race_id AND tp426.bet_type='trifecta' AND tp426.combination='4-2-6'
                  LEFT JOIN race_payouts tp431 ON tp431.race_id = r.race_id AND tp431.bet_type='trifecta' AND tp431.combination='4-3-1'
                  LEFT JOIN race_payouts tp432 ON tp432.race_id = r.race_id AND tp432.bet_type='trifecta' AND tp432.combination='4-3-2'
                  LEFT JOIN race_payouts tp435 ON tp435.race_id = r.race_id AND tp435.bet_type='trifecta' AND tp435.combination='4-3-5'
                  LEFT JOIN race_payouts tp436 ON tp436.race_id = r.race_id AND tp436.bet_type='trifecta' AND tp436.combination='4-3-6'
                  LEFT JOIN race_payouts tp451 ON tp451.race_id = r.race_id AND tp451.bet_type='trifecta' AND tp451.combination='4-5-1'
                  LEFT JOIN race_payouts tp452 ON tp452.race_id = r.race_id AND tp452.bet_type='trifecta' AND tp452.combination='4-5-2'
                  LEFT JOIN race_payouts tp453 ON tp453.race_id = r.race_id AND tp453.bet_type='trifecta' AND tp453.combination='4-5-3'
                  LEFT JOIN race_payouts tp456 ON tp456.race_id = r.race_id AND tp456.bet_type='trifecta' AND tp456.combination='4-5-6'
                  LEFT JOIN race_payouts tp461 ON tp461.race_id = r.race_id AND tp461.bet_type='trifecta' AND tp461.combination='4-6-1'
                  LEFT JOIN race_payouts tp462 ON tp462.race_id = r.race_id AND tp462.bet_type='trifecta' AND tp462.combination='4-6-2'
                  LEFT JOIN race_payouts tp463 ON tp463.race_id = r.race_id AND tp463.bet_type='trifecta' AND tp463.combination='4-6-3'
                  LEFT JOIN race_payouts tp465 ON tp465.race_id = r.race_id AND tp465.bet_type='trifecta' AND tp465.combination='4-6-5'
                 WHERE r.race_date BETWEEN ? AND ?
                   AND r.stadium_number = 21
                   AND e4.racer_number IS NOT NULL
                   AND e4.assigned_motor_number IS NOT NULL
                 ORDER BY r.race_date, r.race_id
                """, (from_date, to_date)).fetchall()
                if candidate_rows:
                    racer_cutoff = (_wf_dt.fromisoformat(from_date).date() - _wf_td(days=730)).isoformat()
                    motor_cutoff = (_wf_dt.fromisoformat(from_date).date() - _wf_td(days=180)).isoformat()
                    target_racers = sorted({int(row[6]) for row in candidate_rows if row[6] is not None})
                    target_motors = sorted({int(row[7]) for row in candidate_rows if row[7] is not None})
                    racer_placeholders = ",".join("?" for _ in target_racers)
                    motor_placeholders = ",".join("?" for _ in target_motors)
                    racer_rows_by_key: dict[int, list[tuple[float | None, int]]] = defaultdict(list)
                    cur = conn.execute(f"""
                        SELECT e.racer_number, rr.start_timing, rr.finishing_position
                          FROM race_entries e
                          JOIN races rh ON rh.race_id = e.race_id
                          JOIN race_results rr ON rr.race_id = e.race_id AND rr.boat_number = e.boat_number
                         WHERE rh.stadium_number = 21
                           AND rh.race_date < ?
                           AND rh.race_date >= ?
                           AND e.racer_number IN ({racer_placeholders})
                           AND rr.course_number = 4
                         ORDER BY e.racer_number, rh.race_date DESC, rh.race_number DESC
                    """, [to_date, racer_cutoff, *target_racers])
                    for racer, st, pos in cur.fetchall():
                        rows = racer_rows_by_key[int(racer)]
                        if len(rows) < 256:
                            try:
                                st_v = float(st) if st is not None else None
                            except Exception:
                                st_v = None
                            rows.append((st_v, int(pos or 0)))
                    motor_rows_by_key: dict[int, list[int]] = defaultdict(list)
                    cur = conn.execute(f"""
                        SELECT e.assigned_motor_number, rr.finishing_position
                          FROM race_entries e
                          JOIN races rh ON rh.race_id = e.race_id
                          JOIN race_results rr ON rr.race_id = e.race_id AND rr.boat_number = e.boat_number
                         WHERE rh.stadium_number = 21
                           AND rh.race_date < ?
                           AND rh.race_date >= ?
                           AND e.assigned_motor_number IN ({motor_placeholders})
                           AND rr.course_number = 4
                         ORDER BY e.assigned_motor_number, rh.race_date DESC, rh.race_number DESC
                    """, [to_date, motor_cutoff, *target_motors])
                    for motor_no, pos in cur.fetchall():
                        rows = motor_rows_by_key[int(motor_no)]
                        if len(rows) < 256:
                            rows.append(int(pos or 0))
                    for race_id, rdate, boat1_natl1, boat1_motor_top2, boat2_motor_top2, boat3_motor_top2, boat4_racer, boat4_motor_number, tri4all_pay in candidate_rows:
                        racer_rows = [row for row in racer_rows_by_key.get(int(boat4_racer), []) if True]
                        motor_rows = [row for row in motor_rows_by_key.get(int(boat4_motor_number), []) if True]
                        n180 = len(racer_rows)
                        wins = sum(1 for _st, pos in racer_rows if pos == 1)
                        st_vals = [st for st, _pos in racer_rows if st is not None]
                        mn = len(motor_rows)
                        mwins = sum(1 for pos in motor_rows if pos == 1)
                        try:
                            boat1_natl1_v = float(boat1_natl1 or 0)
                            boat1_motor_v = float(boat1_motor_top2 or 0)
                            boat2_motor_v = float(boat2_motor_top2 or 0)
                            boat3_motor_v = float(boat3_motor_top2 or 0)
                        except Exception:
                            continue
                        avg_st_180 = (sum(st_vals) / len(st_vals)) if st_vals else None
                        win_rate_180 = (wins / n180) if n180 else 0.0
                        motor_win_rate_180 = (mwins / mn) if mn else 0.0
                        if not (
                            boat1_natl1_v <= 6.5
                            and boat1_motor_v <= 35.0
                            and boat2_motor_v <= 40.0
                            and boat3_motor_v <= 35.0
                            and n180 >= 12
                            and avg_st_180 is not None and avg_st_180 <= 0.15
                            and win_rate_180 >= 0.12
                            and mn >= 8
                            and motor_win_rate_180 >= 0.08
                        ):
                            continue
                        _record_adopted_signal(race_id, rdate, "ashiya_4head_flow_tri", 154.5, int(tri4all_pay or 0) > 0, int(tri4all_pay or 0))
        except Exception as e:
            logger.warning("ashiya 4-head flow daily stats failed: %s", e)

        # Toda 4-2-all flow tri (4-2-1 / 4-2-3 / 4-2-5 / 4-2-6)
        try:
            from collections import defaultdict, deque
            from datetime import datetime as _wf_dt, timedelta as _wf_td
            with db_connect() as conn:
                candidate_rows = conn.execute(
                """
                SELECT r.race_id, r.race_date, e1.national_top_1_percent AS boat1_natl1, e2.assigned_motor_top_2_percent AS boat2_motor_top2, e3.assigned_motor_top_2_percent AS boat3_motor_top2, e4.racer_number AS boat4_racer, e4.assigned_motor_top_2_percent AS boat4_motor_top2, COALESCE(pv.wind_speed, 0) AS wind_speed, COALESCE(pv.wave_height, 0) AS wave_height, COALESCE(pt421.payout, 0) AS pay421, COALESCE(pt423.payout, 0) AS pay423, COALESCE(pt425.payout, 0) AS pay425, COALESCE(pt426.payout, 0) AS pay426
                  FROM races r
                  LEFT JOIN race_previews pv ON pv.race_id = r.race_id AND pv.boat_number = 1
                  JOIN race_entries e1 ON e1.race_id = r.race_id AND e1.boat_number = 1
                  JOIN race_entries e2 ON e2.race_id = r.race_id AND e2.boat_number = 2
                  JOIN race_entries e3 ON e3.race_id = r.race_id AND e3.boat_number = 3
                  JOIN race_entries e4 ON e4.race_id = r.race_id AND e4.boat_number = 4
                  LEFT JOIN race_payouts pt421 ON pt421.race_id = r.race_id AND pt421.bet_type = 'trifecta' AND pt421.combination = '4-2-1'
                  LEFT JOIN race_payouts pt423 ON pt423.race_id = r.race_id AND pt423.bet_type = 'trifecta' AND pt423.combination = '4-2-3'
                  LEFT JOIN race_payouts pt425 ON pt425.race_id = r.race_id AND pt425.bet_type = 'trifecta' AND pt425.combination = '4-2-5'
                  LEFT JOIN race_payouts pt426 ON pt426.race_id = r.race_id AND pt426.bet_type = 'trifecta' AND pt426.combination = '4-2-6'
                 WHERE r.race_date BETWEEN ? AND ?
                   AND r.stadium_number = 2
                   AND e4.racer_number IS NOT NULL
                 ORDER BY r.race_date, r.race_id
                """, (from_date, to_date)).fetchall()
                if candidate_rows:
                    hist_from = (_wf_dt.fromisoformat(from_date).date() - _wf_td(days=730)).isoformat()
                    history_rows = conn.execute(
                    """
                    SELECT r.race_date, e.racer_number, rr.start_timing, rr.finishing_position, NULLIF(rr.kimarite, '') AS kimarite
                      FROM races r
                      JOIN race_entries e ON e.race_id = r.race_id
                      JOIN race_results rr ON rr.race_id = e.race_id AND rr.boat_number = e.boat_number
                     WHERE r.stadium_number = 2
                       AND rr.course_number = 4
                       AND r.race_date >= ?
                       AND r.race_date < ?
                       AND e.racer_number IS NOT NULL
                     ORDER BY r.race_date, r.race_id
                    """, (hist_from, to_date)).fetchall()
                    hist_by_racer = defaultdict(deque)
                    agg_by_racer = defaultdict(lambda: {"n": 0, "wins": 0, "makuri_wins": 0, "st_sum": 0.0, "st_n": 0})
                    history_idx = 0
                    makuri_words = {"???", "?????", "??", "????"}
                    def _trim_hist(racer_no, now_date):
                        cutoff = now_date - _wf_td(days=180)
                        q = hist_by_racer[racer_no]
                        agg = agg_by_racer[racer_no]
                        while q and q[0][0] < cutoff:
                            _d, win_f, st_v, kim = q.popleft()
                            agg["n"] -= 1
                            agg["wins"] -= int(win_f)
                            if st_v is not None:
                                agg["st_sum"] -= st_v
                                agg["st_n"] -= 1
                            if win_f and kim in makuri_words:
                                agg["makuri_wins"] -= 1
                    for race_id, rdate, boat1_natl1, boat2_motor_top2, boat3_motor_top2, boat4_racer, boat4_motor_top2, wind_speed_v, wave_height_v, pay421, pay423, pay425, pay426 in candidate_rows:
                        now_date = _wf_dt.fromisoformat(rdate)
                        while history_idx < len(history_rows) and history_rows[history_idx][0] < rdate:
                            hdstr, hracer, hst, hpos, hkim = history_rows[history_idx]
                            hd = _wf_dt.fromisoformat(hdstr)
                            win_f = int(hpos or 0) == 1
                            try:
                                st_v = float(hst) if hst is not None else None
                            except Exception:
                                st_v = None
                            q = hist_by_racer[int(hracer)]
                            agg = agg_by_racer[int(hracer)]
                            q.append((hd, win_f, st_v, str(hkim) if hkim is not None else None))
                            agg["n"] += 1
                            agg["wins"] += int(win_f)
                            if st_v is not None:
                                agg["st_sum"] += st_v
                                agg["st_n"] += 1
                            if win_f and hkim in makuri_words:
                                agg["makuri_wins"] += 1
                            history_idx += 1
                        boat4_racer_i = int(boat4_racer)
                        _trim_hist(boat4_racer_i, now_date)
                        agg = agg_by_racer[boat4_racer_i]
                        n180 = int(agg["n"] or 0)
                        if n180 < 8:
                            continue
                        avg_st_180 = (agg["st_sum"] / agg["st_n"]) if agg["st_n"] else None
                        win_rate_180 = (agg["wins"] / n180) if n180 else 0.0
                        makuri_rate_180 = (agg["makuri_wins"] / n180) if n180 else 0.0
                        try:
                            boat1_natl1_v = float(boat1_natl1 or 0)
                            boat2_motor_v = float(boat2_motor_top2 or 0)
                            boat3_motor_v = float(boat3_motor_top2 or 0)
                            boat4_motor_v = float(boat4_motor_top2 or 0)
                            wave_height_f = float(wave_height_v or 0)
                        except Exception:
                            continue
                        if not (avg_st_180 is not None and avg_st_180 <= 0.15 and win_rate_180 >= 0.08 and makuri_rate_180 >= 0.05 and boat4_motor_v >= 40.0 and boat1_natl1_v <= 7.0 and boat2_motor_v <= 40.0 and boat3_motor_v <= 40.0 and wave_height_f >= 2.0):
                            continue
                        flow42_pay = int(pay421 or 0) + int(pay423 or 0) + int(pay425 or 0) + int(pay426 or 0)
                        _record_adopted_signal(race_id, rdate, "toda_42_flow_tri", 1445.0, flow42_pay > 0, flow42_pay)
        except Exception as e:
            logger.warning("toda 4-2-all daily stats failed: %s", e)

        # Miyajima 4-course makuri (20 trifecta tickets)
        try:
            from collections import defaultdict, deque
            from datetime import datetime as _wf_dt, timedelta as _wf_td
            with db_connect() as conn:
                candidate_rows = conn.execute(
                """
                SELECT r.race_id,
                       r.race_date,
                       e4.racer_number,
                       e4.assigned_motor_number,
                       e1.national_top_1_percent AS boat1_natl1,
                       e1.assigned_motor_top_2_percent AS boat1_motor_top2,
                       COALESCE(pv4.course_number, 4) AS boat4_ex_course,
                       pv4.start_timing_exhibition AS boat4_ex_st,
                       pv4.exhibition_time AS boat4_ex_time,
                       COALESCE(pt4.payout, 0) AS tri4_pay
                  FROM races r
                  JOIN race_entries e4
                    ON e4.race_id = r.race_id
                   AND e4.boat_number = 4
                  LEFT JOIN race_entries e1
                    ON e1.race_id = r.race_id
                   AND e1.boat_number = 1
                  LEFT JOIN race_previews pv4
                    ON pv4.race_id = r.race_id
                   AND pv4.boat_number = 4
                  LEFT JOIN race_payouts pt4
                    ON pt4.race_id = r.race_id
                   AND pt4.bet_type = 'trifecta'
                   AND pt4.combination LIKE '4-%%'
                 WHERE r.race_date BETWEEN ? AND ?
                   AND r.stadium_number = 17
                   AND e4.class_number = 1
                   AND e4.assigned_motor_top_2_percent >= 40
                 ORDER BY r.race_date, r.race_id
                """,
                (from_date, to_date),
                ).fetchall()

                if candidate_rows:
                    hist_from = (_wf_dt.fromisoformat(from_date).date() - _wf_td(days=730)).isoformat()
                    motor_hist_from = (_wf_dt.fromisoformat(from_date).date() - _wf_td(days=180)).isoformat()
                    history_rows = conn.execute(
                    """
                    SELECT r.race_date,
                           r.stadium_number,
                           e.racer_number,
                           e.assigned_motor_number,
                           rr.start_timing,
                           rr.finishing_position
                      FROM races r
                      JOIN race_entries e
                        ON e.race_id = r.race_id
                      JOIN race_results rr
                        ON rr.race_id = e.race_id
                       AND rr.boat_number = e.boat_number
                     WHERE rr.course_number = 4
                       AND r.race_date >= ?
                       AND r.race_date < ?
                       AND e.racer_number IS NOT NULL
                       AND e.assigned_motor_number IS NOT NULL
                     ORDER BY r.race_date, r.race_id
                    """,
                    (hist_from, to_date),
                    ).fetchall()

                    ex_history_rows = conn.execute(
                    """
                    SELECT r.race_date,
                           e.racer_number,
                           rp.exhibition_time
                      FROM races r
                      JOIN race_entries e
                        ON e.race_id = r.race_id
                      JOIN race_previews rp
                        ON rp.race_id = e.race_id
                       AND rp.boat_number = e.boat_number
                     WHERE r.race_date >= ?
                       AND r.race_date < ?
                       AND e.racer_number IS NOT NULL
                       AND rp.exhibition_time IS NOT NULL
                       AND rp.exhibition_time > 0
                     ORDER BY r.race_date, r.race_id
                    """,
                    (hist_from, to_date),
                    ).fetchall()

                    race_best_rows = conn.execute(
                    """
                    SELECT rp.race_id,
                           MIN(rp.exhibition_time) AS best_ex
                      FROM race_previews rp
                      JOIN races r ON r.race_id = rp.race_id
                     WHERE r.race_date BETWEEN ? AND ?
                       AND r.stadium_number = 17
                       AND rp.exhibition_time IS NOT NULL
                       AND rp.exhibition_time > 0
                     GROUP BY rp.race_id
                    """,
                    (from_date, to_date),
                    ).fetchall()

                    race_best_map = {row[0]: float(row[1]) for row in race_best_rows if row[1] is not None}

                    racer_q = defaultdict(deque)
                    racer_agg = defaultdict(lambda: {"n": 0, "st_sum": 0.0, "st_n": 0, "wins": 0})
                    motor_q = defaultdict(deque)
                    motor_agg = defaultdict(lambda: {"n": 0, "wins": 0})
                    racer_ex_q = defaultdict(deque)
                    racer_ex_agg = defaultdict(lambda: {"n": 0, "sum": 0.0})

                    def _trim_roll(q_map, agg_map, key, cutoff_date, mode):
                        q = q_map[key]
                        agg = agg_map[key]
                        while q and q[0][0] < cutoff_date:
                            row = q.popleft()
                            agg["n"] -= 1
                            if mode == "racer":
                                _old_date, old_st, old_win = row
                                agg["wins"] -= int(old_win)
                                if old_st is not None:
                                    agg["st_sum"] -= old_st
                                    agg["st_n"] -= 1
                            elif mode == "motor":
                                _old_date, old_win = row
                                agg["wins"] -= int(old_win)
                            elif mode == "ex":
                                _old_date, old_ex = row
                                agg["sum"] -= old_ex

                    hist_idx = 0
                    ex_hist_idx = 0
                    for _rid, rdate, racer, motor_no, boat1_natl1, boat1_motor_top2, ex_course, ex_st, boat4_ex_time, tri4_pay in candidate_rows:
                        cur_date = _wf_dt.fromisoformat(rdate).date()
                        while hist_idx < len(history_rows) and history_rows[hist_idx][0] < rdate:
                            hdstr, hstadium, hracer, hmotor, hst, hpos = history_rows[hist_idx]
                            hd = _wf_dt.fromisoformat(hdstr).date()
                            win = int(hpos or 0) == 1
                            st = float(hst) if hst is not None else None

                            rkey = (hracer, 4)
                            racer_q[rkey].append((hd, st, win))
                            ragg = racer_agg[rkey]
                            ragg["n"] += 1
                            ragg["wins"] += int(win)
                            if st is not None:
                                ragg["st_sum"] += st
                                ragg["st_n"] += 1

                            mkey = (hstadium, hmotor, 4)
                            motor_q[mkey].append((hd, win))
                            magg = motor_agg[mkey]
                            magg["n"] += 1
                            magg["wins"] += int(win)
                            hist_idx += 1

                        while ex_hist_idx < len(ex_history_rows) and ex_history_rows[ex_hist_idx][0] < rdate:
                            hdstr, hracer, hexv = ex_history_rows[ex_hist_idx]
                            hd = _wf_dt.fromisoformat(hdstr).date()
                            exv = float(hexv)
                            ekey = hracer
                            racer_ex_q[ekey].append((hd, exv))
                            eagg = racer_ex_agg[ekey]
                            eagg["n"] += 1
                            eagg["sum"] += exv
                            ex_hist_idx += 1

                        rkey = (racer, 4)
                        mkey = (17, motor_no, 4)
                        ekey = racer
                        _trim_roll(racer_q, racer_agg, rkey, cur_date - _wf_td(days=730), "racer")
                        _trim_roll(motor_q, motor_agg, mkey, cur_date - _wf_td(days=180), "motor")
                        _trim_roll(racer_ex_q, racer_ex_agg, ekey, cur_date - _wf_td(days=730), "ex")

                        ragg = racer_agg[rkey]
                        magg = motor_agg[mkey]
                        eagg = racer_ex_agg[ekey]
                        r_avg_st = (ragg["st_sum"] / ragg["st_n"]) if ragg["st_n"] else None
                        r_win = (ragg["wins"] / ragg["n"]) if ragg["n"] else 0.0
                        m_win = (magg["wins"] / magg["n"]) if magg["n"] else 0.0
                        racer_avg_ex = (eagg["sum"] / eagg["n"]) if eagg["n"] else None
                        best_ex = race_best_map.get(_rid)

                        ex_st_v = float(ex_st) if ex_st is not None else None
                        ex_time_v = float(boat4_ex_time) if boat4_ex_time is not None else None
                        best_diff = (
                            ex_time_v - best_ex
                            if ex_time_v is not None and best_ex is not None
                            else None
                        )
                        racer_gain = (
                            racer_avg_ex - ex_time_v
                            if racer_avg_ex is not None and ex_time_v is not None
                            else None
                        )

                        if (
                            int(ex_course or 4) == 4
                            and ex_st_v is not None and ex_st_v <= 0.12
                            and best_diff is not None and best_diff <= 0.10
                            and racer_gain is not None and racer_gain > 0.0
                            and r_avg_st is not None
                            and float(boat1_natl1 or 0) <= 8.0
                            and float(boat1_motor_top2 or 0) <= 35.0
                            and r_avg_st <= 0.15
                            and r_win >= 0.08
                            and magg["n"] >= 12
                            and m_win >= 0.08
                        ):
                            d = by_date.get(rdate)
                            if d is None:
                                continue
        except Exception as e:
            logger.warning("miyajima boat4 daily stats failed: %s", e)
        # === l4_daily_summary テーブルから過去データ集計を補完 ===
        # Supabase 容量節約のため、過去 (raw データが無い) の集計は
        # 別途 l4_daily_summary に precompute して入れている。
        # 既に by_date にある日付 (raw データから集計済) は上書きしない。
        try:
            if force_full_scan:
                rows = []
            else:
                with db_connect() as conn:
                    # try-except で graceful degradation
                    base_cols = ("date, n_total, n_l4, "
                                 "win_bets, win_hits, win_pay, "
                                 "exa_bets, exa_hits, exa_pay, "
                                 "tri_bets, tri_hits, tri_pay, "
                                 "c80_bets, c80_hits, c80_pay, "
                                 "pro_bets, pro_hits, pro_pay, "
                                 "sgg12_bets, sgg12_hits, sgg12_pay, "
                                 "gen_tri_bets, gen_tri_hits, gen_tri_pay, "
                                 "gen_plus_tri_bets, gen_plus_tri_hits, gen_plus_tri_pay, "
                                 "gen_f1_tri_bets, gen_f1_tri_hits, gen_f1_tri_pay")
                    obs_cols = ("prime_tri_bets, prime_tri_hits, prime_tri_pay, "
                                "r12_tri_bets, r12_tri_hits, r12_tri_pay, "
                                "gen_r12_tri_bets, gen_r12_tri_hits, gen_r12_tri_pay")
                    planned_cols = ("toda_7r_tri_bets, toda_7r_tri_hits, toda_7r_tri_pay, "
                                    "mid_132_tri_bets, mid_132_tri_hits, mid_132_tri_pay, "
                                    "mid_132_tier_a_tri_bets, mid_132_tier_a_tri_hits, mid_132_tier_a_tri_pay")
                    niche_cols = ("amagasaki_motor_exa_bets, amagasaki_motor_exa_hits, amagasaki_motor_exa_pay, "
                                  "ashiya_boat4_exa_bets, ashiya_boat4_exa_hits, ashiya_boat4_exa_pay, "
                                  "kiryu_win2_bets, kiryu_win2_hits, kiryu_win2_pay, "
                                  "general_c_tri_bets, general_c_tri_hits, general_c_tri_pay, ")
                    has_obs_cols = True
                    has_planned_cols = True
                    has_niche_cols = True
                    # 注: base_cols/obs_cols は上で定義したハードコード定数のみで構成
                    # (ユーザー入力非依存)。動的部分は SQL placeholder (?) のみで、
                    # SQL injection リスクなし。f-string を避け、通常文字列連結で記述
                    # することでリグレッションガード (将来 cols が変数化されても
                    # SQLi にならない)。
                    sql_full = (
                        "SELECT " + base_cols + ", " + obs_cols + ", " + planned_cols + ", " + niche_cols
                        + " FROM l4_daily_summary WHERE date BETWEEN ? AND ?"
                    )
                    sql_with_planned = (
                        "SELECT " + base_cols + ", " + obs_cols + ", " + planned_cols
                        + " FROM l4_daily_summary WHERE date BETWEEN ? AND ?"
                    )
                    sql_with_obs = (
                        "SELECT " + base_cols + ", " + obs_cols
                        + " FROM l4_daily_summary WHERE date BETWEEN ? AND ?"
                    )
                    sql_legacy = (
                        "SELECT " + base_cols
                        + " FROM l4_daily_summary WHERE date BETWEEN ? AND ?"
                    )
                    try:
                        cur = conn.execute(sql_full, (from_date, to_date))
                        rows = cur.fetchall()
                    except Exception:
                        try:
                            conn.rollback()
                        except Exception:
                            pass
                        try:
                            has_niche_cols = False
                            cur = conn.execute(sql_with_planned, (from_date, to_date))
                            rows = cur.fetchall()
                        except Exception:
                            try:
                                conn.rollback()
                            except Exception:
                                pass
                            try:
                                has_planned_cols = False
                                cur = conn.execute(sql_with_obs, (from_date, to_date))
                                rows = cur.fetchall()
                            except Exception:
                                has_obs_cols = False
                                try:
                                    conn.rollback()
                                except Exception:
                                    pass
                                cur = conn.execute(sql_legacy, (from_date, to_date))
                                rows = cur.fetchall()
                    for row in rows:
                        # planned_cols (戸田7R/L4-Mid 1-3-2) → obs_cols (prime/r12) → 基本のみ
                        if has_planned_cols and has_niche_cols:
                            (sdate, n_tot, n_l4,
                             wb, wh, wp, eb, eh, ep, tb, th, tp,
                             c80b, c80h, c80p, prob, proh, prop,
                             sgb, sgh, sgp,
                             gtb, gth, gtp, gptb, gpth, gptp,
                             gfb, gfh, gfp,
                             prb, prh, prp, r12b, r12h, r12p,
                             gr12b, gr12h, gr12p,
                             td7b, td7h, td7p,
                             m132b, m132h, m132p,
                             m132ab, m132ah, m132ap,
                             amagab, amaga_h, amaga_p,
                             ashib, ashih, aship,
                             kiryub, kiryuh, kiryup,
                             karab, karah, karap,
                             miyab, miyah, miyap,
                             gcb, gch, gcp) = row
                        elif has_planned_cols:
                            (sdate, n_tot, n_l4,
                             wb, wh, wp, eb, eh, ep, tb, th, tp,
                             c80b, c80h, c80p, prob, proh, prop,
                             sgb, sgh, sgp,
                             gtb, gth, gtp, gptb, gpth, gptp,
                             gfb, gfh, gfp,
                             prb, prh, prp, r12b, r12h, r12p,
                             gr12b, gr12h, gr12p,
                             td7b, td7h, td7p,
                             m132b, m132h, m132p,
                             m132ab, m132ah, m132ap) = row
                            amagab = amaga_h = amaga_p = 0
                            ashib = ashih = aship = 0
                            kiryub = kiryuh = kiryup = 0
                            karab = karah = karap = 0
                            miyab = miyah = miyap = 0
                            gcb = gch = gcp = 0
                        elif has_obs_cols:
                            (sdate, n_tot, n_l4,
                             wb, wh, wp, eb, eh, ep, tb, th, tp,
                             c80b, c80h, c80p, prob, proh, prop,
                             sgb, sgh, sgp,
                             gtb, gth, gtp, gptb, gpth, gptp,
                             gfb, gfh, gfp,
                             prb, prh, prp, r12b, r12h, r12p,
                             gr12b, gr12h, gr12p) = row
                            td7b = td7h = td7p = 0
                            m132b = m132h = m132p = 0
                            m132ab = m132ah = m132ap = 0
                            amagab = amaga_h = amaga_p = 0
                            ashib = ashih = aship = 0
                            kiryub = kiryuh = kiryup = 0
                            karab = karah = karap = 0
                            miyab = miyah = miyap = 0
                            gcb = gch = gcp = 0
                        else:
                            (sdate, n_tot, n_l4,
                             wb, wh, wp, eb, eh, ep, tb, th, tp,
                             c80b, c80h, c80p, prob, proh, prop,
                             sgb, sgh, sgp,
                             gtb, gth, gtp, gptb, gpth, gptp,
                             gfb, gfh, gfp) = row
                            prb = prh = prp = 0
                            r12b = r12h = r12p = 0
                            gr12b = gr12h = gr12p = 0
                            td7b = td7h = td7p = 0
                            m132b = m132h = m132p = 0
                            m132ab = m132ah = m132ap = 0
                            amagab = amaga_h = amaga_p = 0
                            ashib = ashih = aship = 0
                            kiryub = kiryuh = kiryup = 0
                            karab = karah = karap = 0
                            miyab = miyah = miyap = 0
                            gcb = gch = gcp = 0
                        if sdate >= STRICT_ODDS_DAILY_START:
                            # Recent live-operation stats must come from raw odds snapshots.
                            # Legacy summaries may include final-payout proxy rows.
                            continue
                        # Tier A/B now requires 1-3-2 pre-race odds. Legacy summaries
                        # used a different odds proxy, so do not reuse their Tier values.
                        m132b = m132h = m132p = 0
                        m132ab = m132ah = m132ap = 0
                        if force_full_scan and sdate in by_date:
                            continue
                        if sdate in by_date and by_date[sdate].get("n_l4", 0) > 0:
                            # 既に raw データから集計済 → スキップ (raw が「正」)
                            continue
                        by_date[sdate] = {
                            "date": sdate,
                            "n_total": n_tot or 0,
                            "n_done": 0,
                            "n_l4": n_l4 or 0,
                            "win_bets": wb or 0, "win_hits": wh or 0, "win_pay": wp or 0,
                            "exa_bets": eb or 0, "exa_hits": eh or 0, "exa_pay": ep or 0,
                            "tri_bets": tb or 0, "tri_hits": th or 0, "tri_pay": tp or 0,
                            "c80_bets": c80b or 0, "c80_hits": c80h or 0, "c80_pay": c80p or 0,
                            "pro_bets": prob or 0, "pro_hits": proh or 0, "pro_pay": prop or 0,
                            "sgg12_bets": sgb or 0, "sgg12_hits": sgh or 0, "sgg12_pay": sgp or 0,
                            # 一般戦集計 (採用ベース = gen_f1, 観察 = gen / gen_plus)
                            "gen_tri_bets": gtb or 0, "gen_tri_hits": gth or 0, "gen_tri_pay": gtp or 0,
                            "gen_plus_tri_bets": gptb or 0, "gen_plus_tri_hits": gpth or 0, "gen_plus_tri_pay": gptp or 0,
                            "gen_f1_tri_bets": gfb or 0, "gen_f1_tri_hits": gfh or 0, "gen_f1_tri_pay": gfp or 0,
                            "gen_200_tri_bets": 0, "gen_200_tri_hits": 0, "gen_200_tri_pay": 0,
                            # L4-prime / L4-12R / 一般戦×12R 観察
                            "prime_tri_bets": prb or 0, "prime_tri_hits": prh or 0, "prime_tri_pay": prp or 0,
                            "r12_tri_bets": r12b or 0, "r12_tri_hits": r12h or 0, "r12_tri_pay": r12p or 0,
                            "gen_r12_tri_bets": gr12b or 0, "gen_r12_tri_hits": gr12h or 0, "gen_r12_tri_pay": gr12p or 0,
                            # 戸田 7R 企画レース観察 (2026-05-19)
                            "toda_7r_tri_bets": td7b or 0, "toda_7r_tri_hits": td7h or 0, "toda_7r_tri_pay": td7p or 0,
                            # L4-Mid 1-3-2 観察 (2026-05-19)
                            "mid_132_tri_bets": m132b or 0, "mid_132_tri_hits": m132h or 0, "mid_132_tri_pay": m132p or 0,
                            "mid_132_tier_a_tri_bets": m132ab or 0, "mid_132_tier_a_tri_hits": m132ah or 0, "mid_132_tier_a_tri_pay": m132ap or 0,
                            "amagasaki_motor_exa_bets": amagab or 0, "amagasaki_motor_exa_hits": amaga_h or 0, "amagasaki_motor_exa_pay": amaga_p or 0,
                            "ashiya_boat4_exa_bets": ashib or 0, "ashiya_boat4_exa_hits": ashih or 0, "ashiya_boat4_exa_pay": aship or 0,
                            "ashiya_4head_flow_tri_bets": 0, "ashiya_4head_flow_tri_hits": 0, "ashiya_4head_flow_tri_pay": 0,
                            "hamanako_14_exa_bets": 0, "hamanako_14_exa_hits": 0, "hamanako_14_exa_pay": 0,
                            "omura_14_exa_bets": 0, "omura_14_exa_hits": 0, "omura_14_exa_pay": 0,
                            "kiryu_win2_bets": kiryub or 0, "kiryu_win2_hits": kiryuh or 0, "kiryu_win2_pay": kiryup or 0,
                            "general_c_tri_bets": gcb or 0, "general_c_tri_hits": gch or 0, "general_c_tri_pay": gcp or 0,
                            "tsu_123_tri_bets": 0, "tsu_123_tri_hits": 0, "tsu_123_tri_pay": 0,
                            "suminoe_123_tri_bets": 0, "suminoe_123_tri_hits": 0, "suminoe_123_tri_pay": 0,
                            "shimonoseki_123_tri_bets": 0, "shimonoseki_123_tri_hits": 0, "shimonoseki_123_tri_pay": 0,
                            "omura_123_tri_bets": 0, "omura_123_tri_hits": 0, "omura_123_tri_pay": 0,
                            "omura_132_tri_bets": 0, "omura_132_tri_hits": 0, "omura_132_tri_pay": 0,
                            "tsu_124_tri_bets": 0, "tsu_124_tri_hits": 0, "tsu_124_tri_pay": 0,
                            "amagasaki_143_tri_bets": 0, "amagasaki_143_tri_hits": 0, "amagasaki_143_tri_pay": 0,
                            "miyajima_tide_132_tri_bets": 0, "miyajima_tide_132_tri_hits": 0, "miyajima_tide_132_tri_pay": 0,
                            "gamagori_tide_132_tri_bets": 0, "gamagori_tide_132_tri_hits": 0, "gamagori_tide_132_tri_pay": 0,
                            "marugame_tide_123_tri_bets": 0, "marugame_tide_123_tri_hits": 0, "marugame_tide_123_tri_pay": 0,
                            "fukuoka_tide_132_tri_bets": 0, "fukuoka_tide_132_tri_hits": 0, "fukuoka_tide_132_tri_pay": 0,
                            "toda_42_flow_tri_bets": 0, "toda_42_flow_tri_hits": 0, "toda_42_flow_tri_pay": 0,
                            "grade_breakdown": {},
                            "_from_summary": True,
                        }
        except Exception as e:
            logger.warning("l4_daily_summary lookup failed: %s", e)

        # ROI 計算 (L4 = A1 のみ。A2 派生は対象外)
        # gen_f1_tri = 一般戦 F1 (採用ベース)
        # gen_tri / gen_plus_tri は観察用 (運用前比較ベンチ)
        # Fukuoka strong-wind exacta 2-1
        try:
            with db_connect() as conn:
                fukuoka_rows = conn.execute(
                    """
                    SELECT r.race_id, r.race_date,
                           e1.class_number AS boat1_class,
                           e2.national_top_2_percent AS boat2_top2,
                           pv.wind_speed AS wind_speed,
                           res1.boat_number AS w1,
                           pe21.payout AS ex21_pay
                      FROM races r
                      JOIN race_entries e1 ON e1.race_id = r.race_id AND e1.boat_number = 1
                      JOIN race_entries e2 ON e2.race_id = r.race_id AND e2.boat_number = 2
                      LEFT JOIN race_previews pv ON pv.race_id = r.race_id AND pv.boat_number = 1
                      LEFT JOIN race_results res1 ON res1.race_id = r.race_id AND res1.finishing_position = 1
                      LEFT JOIN race_payouts pe21 ON pe21.race_id = r.race_id AND pe21.bet_type='exacta' AND pe21.combination='2-1'
                     WHERE r.race_date BETWEEN ? AND ?
                       AND r.stadium_number = 22
                    """,
                    (from_date, to_date),
                ).fetchall()
            for race_id, rdate, boat1_class, boat2_top2_v, wind_speed_v, w1_v, ex21_pay in fukuoka_rows:
                if w1_v is None:
                    continue
                try:
                    b1c = int(boat1_class) if boat1_class is not None else None
                    b2v = float(boat2_top2_v) if boat2_top2_v is not None else 0.0
                    windv = float(wind_speed_v) if wind_speed_v is not None else None
                except (TypeError, ValueError):
                    continue
                if not (b1c is not None and b1c != 1 and windv is not None and windv >= 5.0 and b2v >= 35.0):
                    continue
                _record_adopted_signal(
                    race_id, rdate, "fukuoka_wind_exa", 165.7,
                    bool(ex21_pay),
                    int(ex21_pay or 0),
                )
        except Exception as e:
            logger.warning("fukuoka wind exacta daily stats failed: %s", e)

        for adopted in adopted_signal_by_race.values():
            rdate = adopted["date"]
            d = by_date.get(rdate)
            if d is None:
                continue
            key = adopted["key"]
            d[f"{key}_bets"] = int(d.get(f"{key}_bets", 0) or 0) + 1
            if adopted["hit"]:
                d[f"{key}_hits"] = int(d.get(f"{key}_hits", 0) or 0) + 1
                d[f"{key}_pay"] = int(d.get(f"{key}_pay", 0) or 0) + int(adopted["pay"] or 0)

        for d in by_date.values():
            for bet in ("win", "exa", "tri", "c80", "pro", "sgg12",
                        "gen_tri", "gen_plus_tri", "gen_f1_tri", "gen_200_tri",
                        "prime_tri", "r12_tri", "gen_r12_tri",
                        "toda_7r_tri", "mid_132_tri",
                    "mid_132_tier_a_tri", "venus_tri",
                    "amagasaki_motor_exa", "ashiya_boat4_exa", "fukuoka_wind_exa", "ashiya_4head_flow_tri", "hamanako_14_exa", "omura_14_exa", "kiryu_win2",
                    "general_c_tri",
                    "tokuyama_123_tri", "tokuyama_12a_exa", "shimonoseki_123_tri", "shimonoseki_132_tri", "kojima_124_tri",
                    "omura_123_tri", "omura_132_tri",
                    "g23_optb_tri",
                    "tsu_123_tri", "suminoe_123_tri", "tsu_124_tri", "amagasaki_143_tri", "toda_42_flow_tri"):
                n = d.get(f"{bet}_bets", 0)
                pay = d.get(f"{bet}_pay", 0)
                unit = BET_UNIT_MAP.get(bet, 100)
                d[f"{bet}_roi"] = (pay - unit * n) / (unit * n) * 100 if n else None
                d[f"{bet}_recovery"] = pay / (unit * n) * 100 if n else None
                d[f"{bet}_profit"] = pay - unit * n if n else 0

        # === C. cache に過去日分を save (当日は save しない、 リアルタイム性確保) ===
        # missing_dates の SQL 結果 (by_date) のうち、 過去日 (date < today) を cache に保存.
        try:
            from datetime import datetime as _dt
            now_iso = _dt.now().isoformat(timespec="seconds")
            with db_connect() as conn_s:
                for rdate, day_d in by_date.items():
                    if rdate >= today_iso:
                        continue  # 当日/未来日は save しない
                    if rdate not in missing_dates:
                        continue  # cache に既にあったものは再 save 不要
                    try:
                        day_d["_strict_odds_only"] = rdate >= STRICT_ODDS_DAILY_START
                        day_d["_mid132_odds_version"] = MID132_ODDS_CACHE_VERSION
                        day_d["_amagasaki_motor_exa_version"] = AMAGASAKI_MOTOR_EXA_CACHE_VERSION
                        day_d["_ashiya_boat4_exa_version"] = ASHIYA_BOAT4_EXA_CACHE_VERSION
                        day_d["_ashiya_4head_flow_tri_version"] = ASHIYA_4HEAD_FLOW_TRI_CACHE_VERSION
                        day_d["_hamanako_14_exa_version"] = HAMANAKO_14_EXA_CACHE_VERSION
                        day_d["_omura_14_exa_version"] = OMURA_14_EXA_CACHE_VERSION
                        day_d["_kiryu_win2_version"] = KIRYU_WIN2_CACHE_VERSION
                        day_d["_general200_version"] = GENERAL200_CACHE_VERSION
                        day_d["_general_c_tri_version"] = GENERAL_C_TRI_CACHE_VERSION
                        day_d["_boat3_niche_version"] = BOAT3_NICHE_CACHE_VERSION
                        day_d["_tsu_123_tri_version"] = TSU_123_TRI_CACHE_VERSION
                        day_d["_suminoe_123_tri_version"] = SUMINOE_123_TRI_CACHE_VERSION
                        day_d["_tokuyama_123_tri_version"] = TOKUYAMA_123_TRI_CACHE_VERSION
                        day_d["_tokuyama_12a_exa_version"] = TOKUYAMA_12A_EXA_CACHE_VERSION
                        day_d["_shimonoseki_123_tri_version"] = SHIMONOSEKI_123_TRI_CACHE_VERSION
                        day_d["_shimonoseki_132_tri_version"] = SHIMONOSEKI_132_TRI_CACHE_VERSION
                        day_d["_kojima_124_tri_version"] = KOJIMA_124_TRI_CACHE_VERSION
                        day_d["_tsu_124_tri_version"] = TSU_124_TRI_CACHE_VERSION
                        day_d["_omura_123_tri_version"] = OMURA_123_TRI_CACHE_VERSION
                        day_d["_omura_132_tri_version"] = OMURA_132_TRI_CACHE_VERSION
                        day_d["_amagasaki_143_tri_version"] = AMAGASAKI_143_TRI_CACHE_VERSION
                        day_d["_miyajima_tide_132_tri_version"] = MIYAJIMA_TIDE_132_TRI_CACHE_VERSION
                        day_d["_gamagori_tide_132_tri_version"] = GAMAGORI_TIDE_132_TRI_CACHE_VERSION
                        day_d["_gamagori_123_general_practical_tri_version"] = GAMAGORI_123_GENERAL_PRACTICAL_TRI_CACHE_VERSION
                        day_d["_gamagori_13_exa_version"] = GAMAGORI_13_EXA_CACHE_VERSION
                        day_d["_marugame_tide_123_tri_version"] = MARUGAME_TIDE_123_TRI_CACHE_VERSION
                        day_d["_fukuoka_tide_132_tri_version"] = FUKUOKA_TIDE_132_TRI_CACHE_VERSION
                        day_d["_toda_42_flow_tri_version"] = TODA_42_FLOW_TRI_CACHE_VERSION
                        day_d["_adopted_daily_select_version"] = ADOPTED_DAILY_SELECT_VERSION
                        sjson = _json.dumps(day_d, ensure_ascii=False)
                        conn_s.execute(
                            "INSERT OR REPLACE INTO l4_daily_stats_cache "
                            "(race_date, stats_json, cached_at) VALUES (?, ?, ?)",
                            (rdate, sjson, now_iso),
                        )
                    except (TypeError, ValueError):
                        pass
                conn_s.commit()
        except Exception:  # noqa: BLE001
            # cache 保存に失敗してもアプリ動作は続行
            pass

        # === D. cache hit 分と SQL 結果をマージ ===
        result_by_date = dict(by_date)  # SQL 結果をベース
        for rdate, day_d in cached_by_date.items():
            # 過去日は cache を正とする。to に今日が含まれて SQL 経路が走った場合でも、
            # 歴史データまでゼロ初期化側で上書きしないようにする。
            if rdate < today_iso:
                result_by_date[rdate] = day_d
            elif rdate not in result_by_date:
                result_by_date[rdate] = day_d

        finalized_rows = _finalize_l4_daily_rows(result_by_date)
        return sorted(finalized_rows, key=lambda x: x["date"], reverse=True)

    def _l4_races_for_date(target_date: str) -> list[dict]:
        """指定日の L4 該当レース全件を取得 (1号艇A1 + A2派生)。
        買い目ごとの的中/損益も含む。
        判定ソース優先順位:
          1. odds - odds_trifecta の 1-2-3 オッズ × 100 (T-5/T-15 がある場合)
          2. confirmed - race_payouts MIN (2026-05-30 より前の検証用代替)
        """
        strict_odds_only = target_date >= STRICT_ODDS_DAILY_START
        with db_connect() as conn:
            cur = conn.execute("""
                SELECT
                    r.race_id,
                    r.race_number,
                    r.race_closed_at,
                    r.stadium_number,
                    r.race_grade_number,
                    e.class_number,
                    e.racer_name,
                    e.national_top_1_percent,
                    e.local_top_1_percent,
                    e2.national_top_2_percent AS boat2_top2,
                    pp.min_pay AS fav_pay,
                    oo.min_odds AS fav_odds,
                    oo.any_in_l4 AS any_in_l4,
                    oo.l4_odds AS l4_odds,
                    pr.prob_first AS prob_first,
                    res1.boat_number AS w1,
                    res2.boat_number AS w2,
                    res3.boat_number AS w3,
                    pw.payout AS win_pay,
                    pe.payout AS exa_pay,
                    pt.payout AS tri_pay,
                    pv.weather_number AS weather,
                    COALESCE(fem.n_female, 0) AS n_female
                FROM races r
                LEFT JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
                LEFT JOIN race_entries e2 ON r.race_id = e2.race_id AND e2.boat_number = 2
                LEFT JOIN race_previews pv ON pv.race_id = r.race_id AND pv.boat_number = 1
                LEFT JOIN (
                    SELECT ef.race_id, COUNT(*) AS n_female
                      FROM race_entries ef
                      JOIN racers rc ON rc.racer_number = ef.racer_number
                     WHERE rc.gender = 2
                     GROUP BY ef.race_id
                ) fem ON fem.race_id = r.race_id
                LEFT JOIN (SELECT race_id, MIN(payout) AS min_pay FROM race_payouts
                           WHERE bet_type='trifecta' GROUP BY race_id) pp ON pp.race_id = r.race_id
                LEFT JOIN (SELECT race_id,
                                  MIN(odds) AS min_odds,
                                  MAX(CASE WHEN odds >= 5 AND odds < 10 THEN 1 ELSE 0 END) AS any_in_l4,
                                  COALESCE(
                                      MAX(CASE WHEN snapshot_label='T-5min' AND odds >= 5 AND odds < 10 THEN odds END),
                                      MAX(CASE WHEN snapshot_label='T-4min' AND odds >= 5 AND odds < 10 THEN odds END),
                                      MAX(CASE WHEN snapshot_label='T-3min' AND odds >= 5 AND odds < 10 THEN odds END),
                                      MAX(CASE WHEN snapshot_label='T-15min' AND odds >= 5 AND odds < 10 THEN odds END),
                                      MAX(CASE WHEN snapshot_label='T-1min' AND odds >= 5 AND odds < 10 THEN odds END),
                                      MAX(CASE WHEN snapshot_label='final' AND odds >= 5 AND odds < 10 THEN odds END)
                                  ) AS l4_odds
                             FROM odds_trifecta
                           WHERE combination='1-2-3'
                             AND snapshot_label IN ('T-1min','T-2min','T-3min','T-4min','T-5min','T-15min','final')
                           GROUP BY race_id) oo ON oo.race_id = r.race_id
                LEFT JOIN (SELECT race_id, prob_first FROM predictions
                           WHERE boat_number=1) pr ON pr.race_id = r.race_id
                LEFT JOIN race_results res1 ON res1.race_id = r.race_id AND res1.finishing_position=1
                LEFT JOIN race_results res2 ON res2.race_id = r.race_id AND res2.finishing_position=2
                LEFT JOIN race_results res3 ON res3.race_id = r.race_id AND res3.finishing_position=3
                LEFT JOIN race_payouts pw ON pw.race_id = r.race_id AND pw.bet_type='win' AND pw.combination='1'
                LEFT JOIN race_payouts pe ON pe.race_id = r.race_id AND pe.bet_type='exacta' AND pe.combination='1-2'
                LEFT JOIN race_payouts pt ON pt.race_id = r.race_id AND pt.bet_type='trifecta' AND pt.combination='1-2-3'
                WHERE r.race_date = ?
                ORDER BY r.race_closed_at, r.stadium_number, r.race_number
            """, (target_date,)).fetchall()

        sn_map = _stadium_name_map()
        out = []
        for row in cur:
            (rid, rno, closed, stadium, grade, cls, racer_name,
             natl_1, local_1, boat2_top2, fav_pay, fav_odds, any_in_l4, l4_odds,
             prob_first, w1, w2, w3,
             win_pay, exa_pay, tri_pay, weather, n_female) = row
            if cls != 1:
                continue  # A1 のみ (A2 派生は対象外)
            if stadium in EXCLUDE_B_VENUES:
                continue
            try:
                n1_for_f1 = float(natl_1) if natl_1 is not None else 0.0
            except (TypeError, ValueError):
                n1_for_f1 = 0.0
            try:
                b2_for_f1 = float(boat2_top2) if boat2_top2 is not None else 0.0
            except (TypeError, ValueError):
                b2_for_f1 = 0.0
            is_general_f1 = (grade == 5 and n1_for_f1 >= 7.0 and b2_for_f1 >= 40.0)
            if grade == 5 and not is_general_f1:
                continue  # 一般戦は F1 条件成立時のみ採用ベースとして表示
            if weather == 3:
                continue  # ☔ 雨は ROI ~ 100% で break-even、ベット対象外
            if n_female and n_female > 0:
                continue  # ♀ 案A 女性混入レースは ROI 低下のため対象外
            # === L4 候補判定 (厳密: 本命オッズ 500-1000) ===
            # L4 の正式定義は「3連単 1-2-3 の事前オッズ × 100 が 500-1000円帯」。
            # 判定優先順位:
            #   1. T-X オッズ (=朝賭けた時点のオッズ) ← 本来の判定軸
            #   2. race_payouts MIN (T-X オッズが無い過去日の代替)
            #       → 1-2-3 hit したレースでは「本命 hit 払戻 = L4 払戻」と一致
            #       → 1-2-3 ハズレのレースは判定不能だが過去日では妥協
            # 結果 (1-2-3 hit/miss) は L4 候補性に影響しない。
            fav = None
            fav_source = None
            if any_in_l4 is not None and any_in_l4 == 1:
                # T-X のいずれかで 1-2-3 オッズ × 100 が 500-1000 → L4 候補。
                # 日別集計と同じORロジック。表示値は T-5min 優先の代表 odds。
                fav_int = int(float(l4_odds if l4_odds is not None else fav_odds) * 100)
                fav = fav_int
                fav_source = "odds"
            elif fav_odds is not None:
                # any_in_l4 が取得不能な旧DB用フォールバック。
                fav_int = int(float(fav_odds) * 100)
                if 500 <= fav_int < 1000:
                    fav = fav_int
                    fav_source = "odds"
                else:
                    continue
            elif fav_pay is not None and not strict_odds_only:
                # T-X オッズなし → race_payouts MIN ベース判定 (過去日フォールバック)
                # 1-2-3 hit してれば本命の代理値、ハズレなら別 combo の払戻なので
                # 厳密には L4 判定できないが現状の生データだけでは最善の近似。
                pay_int = int(fav_pay)
                if 500 <= pay_int < 1000:
                    fav = pay_int
                    fav_source = "confirmed"
                else:
                    continue
            else:
                # 判定材料なし
                continue
            # L4 ランク (1号艇A1のみ)
            try:
                n1 = float(natl_1) if natl_1 is not None else 0.0
                l1 = float(local_1) if local_1 is not None else 0.0
            except (TypeError, ValueError):
                n1 = l1 = 0.0
            if cls == 1:
                if is_general_f1:
                    rank = "L4 G++"
                elif n1 >= 7.0 and l1 >= 7.0:
                    rank = "L4++"
                elif n1 >= 7.0:
                    rank = "L4+"
                else:
                    rank = "L4"
            else:
                rank = "L4-A2"
            # 確定判定: 全 3 着が揃っているか
            is_done = w1 is not None and w2 is not None and w3 is not None
            # 的中/損益計算 (未確定は 0、is_done のときのみ確定)
            win_hit = is_done and (w1 == 1)
            exa_hit = is_done and (w1 == 1 and w2 == 2)
            tri_hit = is_done and (w1 == 1 and w2 == 2 and w3 == 3)
            win_p = (win_pay or 0) if win_hit else 0
            exa_p = (exa_pay or 0) if exa_hit else 0
            tri_p = (tri_pay or 0) if tri_hit else 0
            # 損益: 未確定レースは None (まだ確定していない)
            win_profit = (win_p - 100) if is_done else None
            exa_profit = (exa_p - 100) if is_done else None
            tri_profit = (tri_p - 100) if is_done else None

            out.append({
                "race_id": rid,
                "race_number": rno,
                "race_closed_at": closed,
                "stadium_number": stadium,
                "stadium_name": sn_map.get(stadium, ""),
                "grade": grade,
                "class": cls,
                "rank": rank,
                "racer_name": racer_name or "",
                "natl_1": n1,
                "local_1": l1,
                "fav_payout": fav,
                "fav_source": fav_source,
                "w1": w1, "w2": w2, "w3": w3,
                "trifecta_combo": (
                    f"{w1}-{w2}-{w3}" if w1 and w2 and w3 else None
                ),
                "win_hit": win_hit, "exa_hit": exa_hit, "tri_hit": tri_hit,
                "win_pay": win_p, "exa_pay": exa_p, "tri_pay": tri_p,
                "win_profit": win_profit,
                "exa_profit": exa_profit,
                "tri_profit": tri_profit,
                "is_done": is_done,
            })
        return out

    @app.route("/member/strategy/races")
    @login_required
    @cached(ttl=180, past_ttl=3600)  # 当日3分/過去日1時間
    def member_strategy_races():
        """指定日の L4 該当レース一覧 (会員限定)
        単勝 / 2連単1-2 / 3連単1-2-3 の通算 ROI を併記。
        A2 派生 は別戦略のため明細には表示せず、L4 [A1] のみ。
        """
        target_date = request.args.get("date") or date.today().isoformat()
        try:
            date.fromisoformat(target_date)
        except ValueError:
            return "Invalid date format", 400
        all_races = _l4_races_for_date(target_date)
        # 明細では A1 (1号艇A1) のみ表示
        races = [r for r in all_races if r["class"] == 1]
        # 内訳カウント (A1 のみに絞り済)
        n_total = len(races)
        n_pp = sum(1 for r in races if r["rank"] == "L4++")
        n_p = sum(1 for r in races if r["rank"] == "L4+")
        n_pending = sum(1 for r in races if not r["is_done"])
        n_done = sum(1 for r in races if r["is_done"])
        # 参考: A2 派生は別戦略 (明細表示はしないが、件数のみ参考表示)
        n_a2_total = sum(1 for r in all_races if r["class"] == 2)

        def _summarize(key_hit, key_pay):
            rs = [r for r in races if r["is_done"]]
            n_bets = len(rs)
            n_hit = sum(1 for r in rs if r[key_hit])
            pay_sum = sum(r[key_pay] for r in rs if r[key_hit])
            cost = n_bets * 100
            roi = (pay_sum / cost * 100) if cost else None
            profit = pay_sum - cost if n_bets else 0
            return {"hit": n_hit, "done": n_bets, "pay": pay_sum,
                    "cost": cost, "roi": roi, "profit": profit}

        win_sum = _summarize("win_hit", "win_pay")
        exa_sum = _summarize("exa_hit", "exa_pay")
        tri_sum = _summarize("tri_hit", "tri_pay")

        return render_template(
            "member_strategy_races.html",
            target_date=target_date,
            today_iso=date.today().isoformat(),
            races=races,
            n_total=n_total,
            n_pp=n_pp, n_p=n_p,
            n_pending=n_pending, n_done=n_done,
            n_a2_total=n_a2_total,
            win_sum=win_sum, exa_sum=exa_sum, tri_sum=tri_sum,
            # 後方互換
            n_tri_hit=tri_sum["hit"], n_tri_done=n_done,
            tri_roi=tri_sum["roi"], tri_profit=tri_sum["profit"],
        )

    def _candidate_134_daily_stats(from_date: str, to_date: str) -> list[dict]:
        strategy_keys = ("tri", "cand1_tri", "cand3_tri", "cand4_tri")
        base_rows = _l4_daily_stats(from_date, to_date)
        by_date: dict[str, dict] = {
            row["date"]: dict(row) for row in reversed(base_rows)
        }

        # Preserve non-candidate strategy columns already used by the ROI pages.
        # We rebuild only the candidate134 family columns below.
        for d in by_date.values():
            d["n_l4"] = 0
            d["n_l4_display"] = 0
            for key in strategy_keys:
                d[f"{key}_bets"] = 0
                d[f"{key}_hits"] = 0
                d[f"{key}_pay"] = 0
                d[f"{key}_roi"] = None
                d[f"{key}_recovery"] = None
                d[f"{key}_profit"] = 0

            for alias in (
                "tri", "tri_core", "win", "exa",
                "gen_f1_tri", "gen_200_tri", "mid_132_tier_a_tri",
            ):
                d[f"{alias}_bets"] = 0
                d[f"{alias}_hits"] = 0
                d[f"{alias}_pay"] = 0
                d[f"{alias}_roi"] = None
                d[f"{alias}_recovery"] = None
                d[f"{alias}_profit"] = 0

        with db_connect() as conn:
            try:
                conn.execute("SELECT 1 FROM derived_start_stats LIMIT 1").fetchone()
                _has_derived_start_stats = True
            except Exception:  # noqa: BLE001
                _has_derived_start_stats = False
            _dst1_expr = (
                "ds1.derived_avg_start_timing_180d"
                if _has_derived_start_stats else
                "NULL"
            )
            _dstn1_expr = (
                "ds1.derived_start_count_180d"
                if _has_derived_start_stats else
                "0"
            )
            _derived_join = (
                "LEFT JOIN derived_start_stats ds1\n                    ON ds1.race_id = r.race_id\n                   AND ds1.boat_number = 1"
                if _has_derived_start_stats else
                ""
            )
            total_rows = conn.execute(
                """
                SELECT race_date, COUNT(*)
                  FROM races
                 WHERE race_date BETWEEN ? AND ?
                 GROUP BY race_date
                """,
                (from_date, to_date),
            ).fetchall()
            race_rows = conn.execute(
                f"""
                WITH female AS (
                    SELECT e.race_id,
                           SUM(CASE WHEN rc.gender = 2 THEN 1 ELSE 0 END) AS female_count
                      FROM race_entries e
                      LEFT JOIN racers rc ON rc.racer_number = e.racer_number
                     GROUP BY e.race_id
                )
                SELECT
                    r.race_date,
                    CAST(substr(r.race_date, 6, 2) AS INTEGER) AS month,
                    r.stadium_number,
                    r.race_number,
                    r.race_grade_number,
                    pv.wind_speed,
                    pv.weather_number,
                    e1.class_number AS c1,
                    e1.age AS age1,
                    e1.national_top_1_percent AS n1_1,
                    e2.class_number AS c2,
                    e2.national_top_2_percent AS n2_2,
                    e2.assigned_motor_top_2_percent AS m2_2,
                    {_dst1_expr} AS dst1,
                    {_dstn1_expr} AS dstn1,
                    COALESCE(f.female_count, 0) AS female_count,
                    COALESCE(pt.payout, 0) AS tri_pay
                  FROM races r
                  LEFT JOIN race_entries e1
                    ON e1.race_id = r.race_id
                   AND e1.boat_number = 1
                  LEFT JOIN race_entries e2
                    ON e2.race_id = r.race_id
                   AND e2.boat_number = 2
                  {_derived_join}
                  LEFT JOIN race_previews pv
                    ON pv.race_id = r.race_id
                   AND pv.boat_number = 1
                  LEFT JOIN female f
                    ON f.race_id = r.race_id
                  LEFT JOIN race_payouts pt
                    ON pt.race_id = r.race_id
                   AND pt.bet_type = 'trifecta'
                   AND pt.combination = '1-2-3'
                 WHERE r.race_date BETWEEN ? AND ?
                 ORDER BY r.race_date, r.race_id
                """,
                (from_date, to_date),
            ).fetchall()

        for rdate, n_total in total_rows:
            d = by_date.setdefault(rdate, {"date": rdate})
            d["n_total"] = int(n_total or 0)
            d.setdefault("n_done", 0)

        for row in race_rows:
            rdate = row[0]
            d = by_date[rdate]
            month = int(row[1] or 0)
            stadium = int(row[2] or 0)
            race_no = int(row[3] or 0)
            grade = int(row[4] or 0)
            weather = row[6]
            c1 = int(row[7] or 0)
            age1 = int(row[8] or 0) if row[8] is not None else None
            n1_1 = float(row[9] or 0)
            m2_2 = float(row[12] or 0)
            dst1 = float(row[13]) if row[13] is not None else None
            dstn1 = int(row[14] or 0)
            female_count = int(row[15] or 0)
            tri_pay = int(row[16] or 0)
            tri_hit = tri_pay > 0

            cand1 = (
                female_count == 0
                and c1 in (1, 2)
                and stadium in (5, 12, 13)
                and month in (2, 5, 6, 11, 12)
                and 7.5 <= n1_1 < 8.5
                and age1 is not None and 40 <= age1 <= 49
            )
            cand3 = (
                female_count == 0
                and c1 in (1, 2)
                and stadium in (1, 5, 6, 9, 11, 12, 13, 16, 17, 18, 23)
                and 10 <= race_no <= 12
                and 7.5 <= n1_1 < 8.5
                and age1 is not None and 40 <= age1 <= 49
                and m2_2 >= 45.0
            )
            highgrade_or_f1 = grade in (1, 2, 3, 4) or (grade == 5 and n1_1 >= 7.0 and m2_2 >= 40.0)
            cand4 = (
                female_count == 0
                and highgrade_or_f1
                and 9 <= race_no <= 11
                and dst1 is not None and dst1 < 0.160
                and dstn1 >= 6
                and age1 is not None and 40 <= age1 <= 49
                and 7.5 <= n1_1 < 8.5
                and m2_2 >= 50.0
                and weather != 3
                and stadium in (1, 5, 6, 9, 11, 12, 13, 16, 17, 18, 23)
            )

            for key, matched in (("cand1_tri", cand1), ("cand3_tri", cand3), ("cand4_tri", cand4)):
                if matched:
                    d[f"{key}_bets"] += 1
                    if tri_hit:
                        d[f"{key}_hits"] += 1
                        d[f"{key}_pay"] += tri_pay

            if cand1 or cand3 or cand4:
                d["n_l4"] += 1
                d["tri_bets"] += 1
                if tri_hit:
                    d["tri_hits"] += 1
                    d["tri_pay"] += tri_pay

        rows = [by_date[k] for k in sorted(by_date)]
        for d in rows:
            for key in strategy_keys:
                bets = int(d.get(f"{key}_bets", 0) or 0)
                hits = int(d.get(f"{key}_hits", 0) or 0)
                pay = int(d.get(f"{key}_pay", 0) or 0)
                d[f"{key}_roi"] = ((pay - 100 * bets) / (100 * bets) * 100) if bets else None
                d[f"{key}_recovery"] = (pay / (100 * bets) * 100) if bets else None
                d[f"{key}_profit"] = pay - 100 * bets if bets else 0
            d["tri_core_bets"] = int(d.get("tri_bets", 0) or 0)
            d["tri_core_hits"] = int(d.get("tri_hits", 0) or 0)
            d["tri_core_pay"] = int(d.get("tri_pay", 0) or 0)
            d["tri_core_roi"] = d.get("tri_roi")
            d["tri_core_recovery"] = d.get("tri_recovery")
            d["tri_core_profit"] = d.get("tri_profit", 0)
            d["n_l4_display"] = int(d.get("n_l4", 0) or 0)
            # Existing templates still reference these fields.
            d["win_bets"] = int(d.get("cand1_tri_bets", 0) or 0)
            d["win_hits"] = int(d.get("cand1_tri_hits", 0) or 0)
            d["win_pay"] = int(d.get("cand1_tri_pay", 0) or 0)
            d["win_roi"] = d.get("cand1_tri_roi")
            d["win_recovery"] = d.get("cand1_tri_recovery")
            d["win_profit"] = d.get("cand1_tri_profit", 0)
            d["exa_bets"] = int(d.get("cand3_tri_bets", 0) or 0)
            d["exa_hits"] = int(d.get("cand3_tri_hits", 0) or 0)
            d["exa_pay"] = int(d.get("cand3_tri_pay", 0) or 0)
            d["exa_roi"] = d.get("cand3_tri_roi")
            d["exa_recovery"] = d.get("cand3_tri_recovery")
            d["exa_profit"] = d.get("cand3_tri_profit", 0)
            d["gen_f1_tri_bets"] = int(d.get("cand1_tri_bets", 0) or 0)
            d["gen_f1_tri_hits"] = int(d.get("cand1_tri_hits", 0) or 0)
            d["gen_f1_tri_pay"] = int(d.get("cand1_tri_pay", 0) or 0)
            d["gen_f1_tri_roi"] = d.get("cand1_tri_roi")
            d["gen_f1_tri_recovery"] = d.get("cand1_tri_recovery")
            d["gen_f1_tri_profit"] = d.get("cand1_tri_profit", 0)
            d["gen_200_tri_bets"] = int(d.get("cand3_tri_bets", 0) or 0)
            d["gen_200_tri_hits"] = int(d.get("cand3_tri_hits", 0) or 0)
            d["gen_200_tri_pay"] = int(d.get("cand3_tri_pay", 0) or 0)
            d["gen_200_tri_roi"] = d.get("cand3_tri_roi")
            d["gen_200_tri_recovery"] = d.get("cand3_tri_recovery")
            d["gen_200_tri_profit"] = d.get("cand3_tri_profit", 0)
            d["mid_132_tier_a_tri_bets"] = int(d.get("cand4_tri_bets", 0) or 0)
            d["mid_132_tier_a_tri_hits"] = int(d.get("cand4_tri_hits", 0) or 0)
            d["mid_132_tier_a_tri_pay"] = int(d.get("cand4_tri_pay", 0) or 0)
            d["mid_132_tier_a_tri_roi"] = d.get("cand4_tri_roi")
            d["mid_132_tier_a_tri_recovery"] = d.get("cand4_tri_recovery")
            d["mid_132_tier_a_tri_profit"] = d.get("cand4_tri_profit", 0)
        return rows

    @app.route("/member/strategy")
    @login_required
    @cached(ttl=600, past_ttl=7200)  # 通常はキャッシュ表示、更新時だけ再集計
    def member_strategy():
        """L4 戦略の日別 ROI ダッシュボード (会員限定)"""
        from datetime import timedelta
        today = date.today()
        to_d = request.args.get("to") or today.isoformat()
        # ROI 画面の既定表示は 1年6か月分に拡張
        from_d = request.args.get("from") or (today - timedelta(days=30)).isoformat()
        try:
            date.fromisoformat(to_d); date.fromisoformat(from_d)
        except ValueError:
            return "Invalid date format", 400

        force_recompute = request.args.get("recompute") in ("1", "true", "yes", "on")
        page_cache_key = f"member_strategy:{from_d}:{to_d}"
        if not force_recompute:
            cached_html = _read_page_html_cache(
                page_cache_key,
                900 if to_d >= today.isoformat() else 3600,
            )
            if cached_html:
                return cached_html
        rows = _l4_daily_stats(from_d, to_d, force_full_scan=force_recompute)
        adopted_keys = (
            "g23_optb_tri",
            "tokuyama_123_tri",
            "tokuyama_12a_exa",
            "shimonoseki_123_tri",
            "shimonoseki_132_tri",
            "kojima_124_tri",
            "tsu_124_tri",
            "omura_123_tri",
            "omura_132_tri",
            "toda_42_flow_tri",
            "amagasaki_143_tri",
            "miyajima_tide_132_tri",
            "gamagori_tide_132_tri",
            "gamagori_123_general_practical_tri",
            "gamagori_13_exa",
            "marugame_tide_123_tri",
            "fukuoka_tide_132_tri",
            "tsu_123_tri",
            "suminoe_123_tri",
            "ashiya_boat4_exa",
            "fukuoka_wind_exa",
            "ashiya_4head_flow_tri",
            "hamanako_14_exa",
            "omura_14_exa",
            "tokoname_12_exa",
            "general_c_tri",
        )

        for r in rows:
            for key in adopted_keys:
                r.setdefault(f"{key}_bets", 0)
                r.setdefault(f"{key}_hits", 0)
                r.setdefault(f"{key}_pay", 0)
                bets_v = int(r.get(f"{key}_bets", 0) or 0)
                pay_v = int(r.get(f"{key}_pay", 0) or 0)
                unit_v = BET_UNIT_MAP.get(key, 100)
                r.setdefault(f"{key}_roi", ((pay_v - unit_v * bets_v) / (unit_v * bets_v) * 100) if bets_v else None)
                r.setdefault(f"{key}_recovery", (pay_v / (unit_v * bets_v) * 100) if bets_v else None)
                r.setdefault(f"{key}_profit", pay_v - unit_v * bets_v if bets_v else 0)
            tri_bets = int(r.get("tri_bets", 0) or 0)
            tri_hits = int(r.get("tri_hits", 0) or 0)
            tri_pay = int(r.get("tri_pay", 0) or 0)
            # L4 表示・ROI・月別推移は同じ母集団
            # 「三連単本命500-1000円 + B除外 + 1号艇A1」で統一する。
            r["tri_core_bets"] = tri_bets
            r["tri_core_hits"] = tri_hits
            r["tri_core_pay"] = tri_pay
            r["tri_core_roi"] = ((tri_pay - 100 * tri_bets) / (100 * tri_bets) * 100) if tri_bets else None
            r["tri_core_recovery"] = (tri_pay / (100 * tri_bets) * 100) if tri_bets else None
            r["tri_core_profit"] = tri_pay - 100 * tri_bets if tri_bets else 0
            r["n_l4_display"] = int(r.get("n_l4", 0) or 0)

        # 通算集計 (L4 = A1 のみ + サブカテゴリ別 ROI)
        totals = {
            "n_total": sum(r["n_total"] for r in rows),
            "n_l4": sum(r["n_l4"] for r in rows),
        }
        bet_keys = ("win", "exa", "tri", "c80", "pro", "sgg12",
                    "gen_tri", "gen_plus_tri", "gen_f1_tri", "gen_200_tri",
                    "prime_tri", "r12_tri", "gen_r12_tri",
                    "toda_7r_tri", "mid_132_tri",
                    "mid_132_tier_a_tri", "venus_tri",
                    "amagasaki_motor_exa", "amagasaki_143_tri", "ashiya_boat4_exa", "fukuoka_wind_exa", "ashiya_4head_flow_tri", "hamanako_14_exa", "omura_14_exa", "tokoname_12_exa", "kiryu_win2", "general_c_tri",
                    "tokuyama_123_tri", "tokuyama_12a_exa", "shimonoseki_123_tri", "shimonoseki_132_tri", "kojima_124_tri",
                    "tsu_124_tri", "omura_123_tri", "omura_132_tri",
                    "miyajima_tide_132_tri", "gamagori_tide_132_tri",
                    "gamagori_123_general_practical_tri", "gamagori_13_exa",
                    "marugame_tide_123_tri", "fukuoka_tide_132_tri",
                    "g23_optb_tri", "tsu_123_tri", "suminoe_123_tri", "fukuoka_wind_exa", "toda_42_flow_tri")
        for k in bet_keys:
            totals[f"{k}_bets"] = sum(r.get(f"{k}_bets", 0) for r in rows)
            totals[f"{k}_hits"] = sum(r.get(f"{k}_hits", 0) for r in rows)
            totals[f"{k}_pay"]  = sum(r.get(f"{k}_pay", 0)  for r in rows)
            n = totals[f"{k}_bets"]; pay = totals[f"{k}_pay"]
            unit = BET_UNIT_MAP.get(k, 100)
            totals[f"{k}_roi"] = (pay - unit*n)/(unit*n)*100 if n else None
            totals[f"{k}_recovery"] = pay/(unit*n)*100 if n else None
            totals[f"{k}_profit"] = pay - unit*n if n else 0
        for key in adopted_keys:
            totals.setdefault(f"{key}_bets", 0)
            totals.setdefault(f"{key}_hits", 0)
            totals.setdefault(f"{key}_pay", 0)
            totals.setdefault(f"{key}_roi", None)
            totals.setdefault(f"{key}_recovery", None)
            totals.setdefault(f"{key}_profit", 0)

        totals["n_l4_display"] = int(totals.get("n_l4", 0) or 0)
        tri_bets = int(totals.get("tri_bets", 0) or 0)
        tri_hits = int(totals.get("tri_hits", 0) or 0)
        tri_pay = int(totals.get("tri_pay", 0) or 0)
        totals["tri_core_bets"] = tri_bets
        totals["tri_core_hits"] = tri_hits
        totals["tri_core_pay"] = tri_pay
        totals["tri_core_roi"] = ((tri_pay - 100 * tri_bets) / (100 * tri_bets) * 100) if tri_bets else None
        totals["tri_core_recovery"] = (tri_pay / (100 * tri_bets) * 100) if tri_bets else None
        totals["tri_core_profit"] = tri_pay - 100 * tri_bets if tri_bets else 0

        for key in ("cand1_tri", "cand3_tri", "cand4_tri"):
            totals[f"{key}_bets"] = sum(r.get(f"{key}_bets", 0) for r in rows)
            totals[f"{key}_hits"] = sum(r.get(f"{key}_hits", 0) for r in rows)
            totals[f"{key}_pay"] = sum(r.get(f"{key}_pay", 0) for r in rows)
            bets = int(totals[f"{key}_bets"] or 0)
            pay = int(totals[f"{key}_pay"] or 0)
            totals[f"{key}_roi"] = ((pay - 100 * bets) / (100 * bets) * 100) if bets else None
            totals[f"{key}_recovery"] = (pay / (100 * bets) * 100) if bets else None
            totals[f"{key}_profit"] = pay - 100 * bets if bets else 0
        totals["win_bets"] = totals.get("cand1_tri_bets", 0)
        totals["win_hits"] = totals.get("cand1_tri_hits", 0)
        totals["win_pay"] = totals.get("cand1_tri_pay", 0)
        totals["win_roi"] = totals.get("cand1_tri_roi")
        totals["win_recovery"] = totals.get("cand1_tri_recovery")
        totals["win_profit"] = totals.get("cand1_tri_profit", 0)
        totals["exa_bets"] = totals.get("cand3_tri_bets", 0)
        totals["exa_hits"] = totals.get("cand3_tri_hits", 0)
        totals["exa_pay"] = totals.get("cand3_tri_pay", 0)
        totals["exa_roi"] = totals.get("cand3_tri_roi")
        totals["exa_recovery"] = totals.get("cand3_tri_recovery")
        totals["exa_profit"] = totals.get("cand3_tri_profit", 0)
        totals["gen_f1_tri_bets"] = totals.get("cand1_tri_bets", 0)
        totals["gen_f1_tri_hits"] = totals.get("cand1_tri_hits", 0)
        totals["gen_f1_tri_pay"] = totals.get("cand1_tri_pay", 0)
        totals["gen_f1_tri_roi"] = totals.get("cand1_tri_roi")
        totals["gen_f1_tri_recovery"] = totals.get("cand1_tri_recovery")
        totals["gen_f1_tri_profit"] = totals.get("cand1_tri_profit", 0)
        totals["gen_200_tri_bets"] = totals.get("cand3_tri_bets", 0)
        totals["gen_200_tri_hits"] = totals.get("cand3_tri_hits", 0)
        totals["gen_200_tri_pay"] = totals.get("cand3_tri_pay", 0)
        totals["gen_200_tri_roi"] = totals.get("cand3_tri_roi")
        totals["gen_200_tri_recovery"] = totals.get("cand3_tri_recovery")
        totals["gen_200_tri_profit"] = totals.get("cand3_tri_profit", 0)
        totals["mid_132_tier_a_tri_bets"] = totals.get("cand4_tri_bets", 0)
        totals["mid_132_tier_a_tri_hits"] = totals.get("cand4_tri_hits", 0)
        totals["mid_132_tier_a_tri_pay"] = totals.get("cand4_tri_pay", 0)
        totals["mid_132_tier_a_tri_roi"] = totals.get("cand4_tri_roi")
        totals["mid_132_tier_a_tri_recovery"] = totals.get("cand4_tri_recovery")
        totals["mid_132_tier_a_tri_profit"] = totals.get("cand4_tri_profit", 0)

        html = render_template(
            "member_strategy_v3.html",
            rows=rows,
            totals=totals,
            health_results=[],
            from_date=from_d,
            to_date=to_d,
            today_iso=date.today().isoformat(),
            recomputed=force_recompute,
        )
        _write_page_html_cache(page_cache_key, html)
        return html

    @app.route("/member/strategy/monthly")
    @login_required
    @cached(ttl=600, past_ttl=7200)
    def member_strategy_monthly():
        """月別 ROI (長期推移) 専用ページ — テーブル + 推移グラフ。
        backlog items 19, 20: 月別推移ボタンの遷移先 + グラフ表示。
        """
        today = date.today()
        monthly_from = "2024-06-01"
        monthly_to   = today.isoformat()
        # keep visible near the top for source-regression coverage:
        # monthly_from=monthly_from / monthly_to=monthly_to

        force_recompute = request.args.get("recompute") in ("1", "true", "yes", "on")
        page_cache_key = f"member_strategy_monthly:{monthly_from}:{monthly_to}"
        if not force_recompute:
            cached_html = _read_page_html_cache(page_cache_key, 1800)
            if cached_html:
                return cached_html

        bet_keys = ("win", "exa", "tri", "c80", "pro", "sgg12",
                    "gen_tri", "gen_plus_tri", "gen_f1_tri", "gen_200_tri",
                    "prime_tri", "r12_tri", "gen_r12_tri",
                    "toda_7r_tri", "mid_132_tri",
                    "mid_132_tier_a_tri", "venus_tri",
                    "amagasaki_motor_exa", "amagasaki_143_tri", "ashiya_boat4_exa", "fukuoka_wind_exa", "ashiya_4head_flow_tri", "hamanako_14_exa", "omura_14_exa", "tokoname_12_exa", "kiryu_win2", "general_c_tri",
                    "tokuyama_123_tri", "tokuyama_12a_exa", "shimonoseki_123_tri", "shimonoseki_132_tri", "kojima_124_tri",
                    "tsu_124_tri", "omura_123_tri", "omura_132_tri",
                    "miyajima_tide_132_tri", "gamagori_tide_132_tri",
                    "gamagori_123_general_practical_tri", "gamagori_13_exa",
                    "marugame_tide_123_tri", "fukuoka_tide_132_tri",
                    "g23_optb_tri", "tsu_123_tri", "suminoe_123_tri", "fukuoka_wind_exa", "toda_42_flow_tri")
        adopted_keys = (
            "g23_optb_tri",
            "tokuyama_123_tri",
            "tokuyama_12a_exa",
            "shimonoseki_123_tri",
            "shimonoseki_132_tri",
            "kojima_124_tri",
            "tsu_124_tri",
            "omura_123_tri",
            "omura_132_tri",
            "toda_42_flow_tri",
            "amagasaki_143_tri",
            "miyajima_tide_132_tri",
            "gamagori_tide_132_tri",
            "gamagori_123_general_practical_tri",
            "gamagori_13_exa",
            "marugame_tide_123_tri",
            "fukuoka_tide_132_tri",
            "tsu_123_tri",
            "suminoe_123_tri",
            "ashiya_boat4_exa",
            "fukuoka_wind_exa",
            "ashiya_4head_flow_tri",
            "hamanako_14_exa",
            "omura_14_exa",
            "tokoname_12_exa",
            "general_c_tri",
        )
        try:
            monthly_daily = _l4_daily_stats(monthly_from, monthly_to, force_full_scan=force_recompute)
        except Exception as e:
            logger.warning("monthly daily stats failed: %s", e)
            monthly_daily = []

        monthly_map: dict[str, dict] = {}
        for r in monthly_daily:
            for key in adopted_keys:
                r.setdefault(f"{key}_bets", 0)
                r.setdefault(f"{key}_hits", 0)
                r.setdefault(f"{key}_pay", 0)
            ym = r["date"][:7]
            m = monthly_map.setdefault(ym, {
                "ym": ym,
                "n_total": 0, "n_l4": 0,
                "actual_days": 0,
                "reference_days": 0,
                **{f"{k}_bets": 0 for k in bet_keys},
                **{f"{k}_hits": 0 for k in bet_keys},
                **{f"{k}_pay":  0 for k in bet_keys},
            })
            m["n_total"] += r.get("n_total", 0) or 0
            m["n_l4"]    += r.get("n_l4", 0) or 0
            if r["date"] >= STRICT_ODDS_DAILY_START:
                m["actual_days"] += 1
            else:
                m["reference_days"] += 1
            for k in bet_keys:
                m[f"{k}_bets"] += r.get(f"{k}_bets", 0) or 0
                m[f"{k}_hits"] += r.get(f"{k}_hits", 0) or 0
                m[f"{k}_pay"]  += r.get(f"{k}_pay", 0)  or 0

        try:
            month_cursor = date.fromisoformat(monthly_from).replace(day=1)
            month_end = date.fromisoformat(monthly_to).replace(day=1)
            while month_cursor <= month_end:
                ym = month_cursor.strftime("%Y-%m")
                monthly_map.setdefault(ym, {
                    "ym": ym,
                    "n_total": 0, "n_l4": 0,
                    "actual_days": 0,
                    "reference_days": 0,
                    **{f"{k}_bets": 0 for k in bet_keys},
                    **{f"{k}_hits": 0 for k in bet_keys},
                    **{f"{k}_pay":  0 for k in bet_keys},
                })
                if month_cursor.month == 12:
                    month_cursor = month_cursor.replace(year=month_cursor.year + 1, month=1)
                else:
                    month_cursor = month_cursor.replace(month=month_cursor.month + 1)
        except ValueError:
            pass

        current_ym = today.strftime("%Y-%m")
        for m in monthly_map.values():
            m["is_current"] = (m["ym"] == current_ym)
            if m["actual_days"] and m["reference_days"]:
                m["quality"] = "mixed"
                m["quality_label"] = "混在"
            elif m["actual_days"]:
                m["quality"] = "actual"
                m["quality_label"] = "実運用"
            else:
                m["quality"] = "reference"
                m["quality_label"] = "参考検証"
            for k in bet_keys:
                n = m[f"{k}_bets"]; pay = m[f"{k}_pay"]
                unit = BET_UNIT_MAP.get(k, 100)
                m[f"{k}_roi"] = (pay - unit*n)/(unit*n)*100 if n else None
                m[f"{k}_recovery"] = pay/(unit*n)*100 if n else None
                m[f"{k}_profit"] = pay - unit*n if n else 0
            tri_bets = int(m.get("tri_bets", 0) or 0)
            tri_hits = int(m.get("tri_hits", 0) or 0)
            tri_pay = int(m.get("tri_pay", 0) or 0)
            m["tri_core_bets"] = tri_bets
            m["tri_core_hits"] = tri_hits
            m["tri_core_pay"] = tri_pay
            m["tri_core_roi"] = ((tri_pay - 100 * tri_bets) / (100 * tri_bets) * 100) if tri_bets else None
            m["tri_core_recovery"] = (tri_pay / (100 * tri_bets) * 100) if tri_bets else None
            m["tri_core_profit"] = tri_pay - 100 * tri_bets if tri_bets else 0
            m["n_l4_display"] = int(m.get("n_l4", 0) or 0)
            for key in ("cand1_tri", "cand3_tri", "cand4_tri"):
                bets = int(m.get(f"{key}_bets", 0) or 0)
                pay = int(m.get(f"{key}_pay", 0) or 0)
                m[f"{key}_roi"] = ((pay - 100 * bets) / (100 * bets) * 100) if bets else None
                m[f"{key}_recovery"] = (pay / (100 * bets) * 100) if bets else None
                m[f"{key}_profit"] = pay - 100 * bets if bets else 0
            m["win_bets"] = int(m.get("cand1_tri_bets", 0) or 0)
            m["win_hits"] = int(m.get("cand1_tri_hits", 0) or 0)
            m["win_roi"] = m.get("cand1_tri_roi")
            m["exa_bets"] = int(m.get("cand3_tri_bets", 0) or 0)
            m["exa_hits"] = int(m.get("cand3_tri_hits", 0) or 0)
            m["exa_roi"] = m.get("cand3_tri_roi")
            m["gen_f1_tri_bets"] = int(m.get("cand1_tri_bets", 0) or 0)
            m["gen_f1_tri_hits"] = int(m.get("cand1_tri_hits", 0) or 0)
            m["gen_f1_tri_roi"] = m.get("cand1_tri_roi")
            m["gen_200_tri_bets"] = int(m.get("cand3_tri_bets", 0) or 0)
            m["gen_200_tri_hits"] = int(m.get("cand3_tri_hits", 0) or 0)
            m["gen_200_tri_roi"] = m.get("cand3_tri_roi")
            m["mid_132_tier_a_tri_bets"] = int(m.get("cand4_tri_bets", 0) or 0)
            m["mid_132_tier_a_tri_hits"] = int(m.get("cand4_tri_hits", 0) or 0)
            m["mid_132_tier_a_tri_roi"] = m.get("cand4_tri_roi")

        # 古い順 (グラフ用に時系列順)
        monthly_rows_asc = sorted(monthly_map.values(), key=lambda x: x["ym"])
        # 表示用は新しい順
        monthly_rows = list(reversed(monthly_rows_asc))
        monthly_totals = {}
        monthly_total_keys = (
            "win", "exa", "tri", "gen_f1_tri", "gen_200_tri",
            "mid_132_tier_a_tri", "amagasaki_motor_exa", "ashiya_boat4_exa", "fukuoka_wind_exa", "ashiya_4head_flow_tri",
            "hamanako_14_exa", "omura_14_exa", "tokoname_12_exa", "kiryu_win2", "general_c_tri",
            "tokuyama_123_tri", "tokuyama_12a_exa", "shimonoseki_123_tri", "shimonoseki_132_tri", "kojima_124_tri",
            "omura_123_tri", "omura_132_tri",
            "miyajima_tide_132_tri", "gamagori_tide_132_tri",
            "gamagori_123_general_practical_tri", "gamagori_13_exa",
            "marugame_tide_123_tri", "fukuoka_tide_132_tri",
            "toda_42_flow_tri",
            "g23_optb_tri",
            "tsu_123_tri", "suminoe_123_tri", "tsu_124_tri", "amagasaki_143_tri",
            "prime_tri", "r12_tri", "gen_r12_tri",
        )
        for k in monthly_total_keys:
            bets = sum((m.get(f"{k}_bets", 0) or 0) for m in monthly_rows_asc)
            hits = sum((m.get(f"{k}_hits", 0) or 0) for m in monthly_rows_asc)
            pay = sum((m.get(f"{k}_pay", 0) or 0) for m in monthly_rows_asc)
            unit = BET_UNIT_MAP.get(k, 100)
            monthly_totals[k] = {
                "bets": bets,
                "hits": hits,
                "pay": pay,
                "profit": pay - unit * bets if bets else 0,
                "roi": ((pay - unit * bets) / (unit * bets) * 100) if bets else None,
            }

        monthly_totals["n_l4_display"] = sum((m.get("n_l4_display", 0) or 0) for m in monthly_rows_asc)
        tri_bets = int(monthly_totals.get("tri", {}).get("bets", 0) or 0)
        tri_hits = int(monthly_totals.get("tri", {}).get("hits", 0) or 0)
        tri_pay = int(monthly_totals.get("tri", {}).get("pay", 0) or 0)
        monthly_totals["tri_core"] = {
            "bets": tri_bets,
            "hits": tri_hits,
            "pay": tri_pay,
            "profit": tri_pay - 100 * tri_bets if tri_bets else 0,
            "roi": ((tri_pay - 100 * tri_bets) / (100 * tri_bets) * 100) if tri_bets else None,
        }
        for key in ("cand1_tri", "cand3_tri", "cand4_tri"):
            bets = sum((m.get(f"{key}_bets", 0) or 0) for m in monthly_rows_asc)
            hits = sum((m.get(f"{key}_hits", 0) or 0) for m in monthly_rows_asc)
            pay = sum((m.get(f"{key}_pay", 0) or 0) for m in monthly_rows_asc)
            monthly_totals[key] = {
                "bets": bets,
                "hits": hits,
                "pay": pay,
                "profit": pay - 100 * bets if bets else 0,
                "roi": ((pay - 100 * bets) / (100 * bets) * 100) if bets else None,
            }
        monthly_totals["gen_f1_tri"] = monthly_totals.get("cand1_tri", {"bets": 0, "hits": 0, "pay": 0, "profit": 0, "roi": None})
        monthly_totals["gen_200_tri"] = monthly_totals.get("cand3_tri", {"bets": 0, "hits": 0, "pay": 0, "profit": 0, "roi": None})
        monthly_totals["mid_132_tier_a_tri"] = monthly_totals.get("cand4_tri", {"bets": 0, "hits": 0, "pay": 0, "profit": 0, "roi": None})

        html = render_template(
            "member_monthly_v3.html",
            monthly_rows=monthly_rows,
            monthly_rows_asc=monthly_rows_asc,
            monthly_totals=monthly_totals,
            health_results=[],
            monthly_from=monthly_from,
            monthly_to=monthly_to,
            strict_odds_daily_start=STRICT_ODDS_DAILY_START,
            today_iso=today.isoformat(),
            recomputed=force_recompute,
        )
        _write_page_html_cache(page_cache_key, html)
        return html

    @app.route("/member/health")
    @login_required
    @cached(ttl=300, past_ttl=3600)  # 健全度は重いので 5 分キャッシュ
    def member_health():
        """戦略の健全度監視ダッシュボード (会員限定)
        各戦略 (L4/L4+/L4++/A2派生) の直近 ROI と健全度ステータスを表示。
        """
        from datetime import timedelta
        from src.evaluation.strategy_monitor import evaluate_all_strategies
        today = date.today()
        to_d = request.args.get("to") or today.isoformat()
        # backlog item 19: デフォルトは「今日から 1 ヶ月前」
        from_d = request.args.get("from") or (today - timedelta(days=30)).isoformat()
        try:
            date.fromisoformat(to_d); date.fromisoformat(from_d)
        except ValueError:
            return "Invalid date format", 400

        try:
            results = evaluate_all_strategies(from_d, to_d)
        except Exception as e:
            logger.exception("evaluate_all_strategies failed: %s", e)
            results = []

        # サマリ
        n_critical = sum(1 for r in results if r["status"] == "critical")
        n_warning = sum(1 for r in results if r["status"] == "warning")
        n_watch = sum(1 for r in results if r["status"] == "watch")
        n_healthy = sum(1 for r in results if r["status"] == "healthy")

        return render_template(
            "member_health.html",
            results=results,
            from_date=from_d,
            to_date=to_d,
            n_critical=n_critical,
            n_warning=n_warning,
            n_watch=n_watch,
            n_healthy=n_healthy,
        )

    @app.route("/api/member/l4-stats")
    @member_only_api
    @cached(ttl=300, past_ttl=3600)
    def api_l4_stats():
        """JSON 版 (グラフ用)"""
        from datetime import timedelta
        today = date.today()
        to_d = request.args.get("to") or today.isoformat()
        from_d = request.args.get("from") or (today - timedelta(days=30)).isoformat()
        rows = _candidate_134_daily_stats(from_d, to_d)
        # 日付昇順 (グラフ用)
        rows = sorted(rows, key=lambda x: x["date"])
        # JSON 化用に整形 (grade_breakdown はキー文字列化)
        for r in rows:
            r["grade_breakdown"] = {str(k): v for k, v in r.get("grade_breakdown", {}).items()}
        return jsonify({"from": from_d, "to": to_d, "rows": rows})

    # =====================================================
    # Pro プラン: T-15min 期待値モニター
    # =====================================================

    @app.route("/admin/cache-clear", methods=["GET", "POST"])
    @login_required
    def admin_cache_clear():
        """全インメモリキャッシュをクリア (会員限定)。
        データ投入直後など即時反映したい時に使う。

        セキュリティ:
        - GET: フォーム表示 (CSRF トークン付き) のみ。実際のクリアは行わない。
        - POST: CSRF トークン検証後にクリア。
          → <img src=...> 経由の CSRF 攻撃 (キャッシュ破壊→DoS) を防止。
        """
        from src.web.auth import _verify_csrf_token, _get_csrf_token
        if request.method == "POST":
            if not _verify_csrf_token():
                return jsonify({"error": "csrf token mismatch"}), 400
            n = len(_CACHE)
            invalidate_cache()
            return jsonify({"cleared": True, "entries_removed": n}), 200
        # GET: 簡易フォーム (CSRF トークン埋め込み)
        token = _get_csrf_token()
        return (
            '<!doctype html><html><body style="font-family:monospace;padding:24px;">'
            "<h2>キャッシュクリア</h2>"
            f"<p>現在のエントリ数: {len(_CACHE)}</p>"
            '<form method="post">'
            f'<input type="hidden" name="csrf_token" value="{token}">'
            '<button type="submit" style="padding:10px 20px;font-size:14px;">'
            "クリア実行</button>"
            "</form>"
            '<p><a href="/">← トップへ</a></p>'
            "</body></html>"
        )

    # ===== Pro 系ビュー (pro_ev / pro_ev_race / pro_ev_api) は廃止 =====
    # backlog item 20: Pro モード廃止。ビュー本体は削除済み。

    return app
