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

import logging
import os
import time
from datetime import date
from typing import Optional, Any

from datetime import timedelta

from flask import Flask, abort, jsonify, redirect, render_template, request, session, url_for

import config
from src.db.connection import connect as db_connect
from src.web.auth import (
    is_member, is_pro, login_required, member_only_api, pro_only_api, pro_required,
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
            try:
                qs = request.query_string.decode("utf-8") if request else ""
            except Exception:
                qs = ""
            key = f"{fn.__name__}:{args}:{kwargs}:{qs}"
            now = time.time()
            # 過去日リクエストは長期キャッシュ
            effective_ttl = ttl
            try:
                if request:
                    req_date = request.args.get("date", "")
                    if req_date and req_date < date.today().isoformat():
                        effective_ttl = past_ttl
            except Exception:
                pass
            if key in _CACHE:
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


def _format_race_id(race_id: str) -> tuple[str, int, int]:
    """'YYYYMMDD-SS-RR' → (date_str, stadium, race_no)"""
    parts = race_id.split("-")
    return parts[0], int(parts[1]), int(parts[2])


def _stadium_name_map() -> dict[int, str]:
    with db_connect() as conn:
        rows = conn.execute("SELECT stadium_number, name FROM stadiums").fetchall()
    return {n: name for n, name in rows}


def _race_basic_info(race_id: str) -> Optional[dict]:
    with db_connect() as conn:
        row = conn.execute("""
            SELECT r.race_id, r.race_date, r.stadium_number, r.race_number,
                   r.race_grade_number, r.race_title, r.race_subtitle,
                   r.race_closed_at, s.name AS stadium_name
              FROM races r
              JOIN stadiums s ON r.stadium_number = s.stadium_number
             WHERE r.race_id = ?
        """, (race_id,)).fetchone()
    if not row:
        return None
    keys = ["race_id", "race_date", "stadium_number", "race_number",
            "race_grade_number", "race_title", "race_subtitle",
            "race_closed_at", "stadium_name"]
    return dict(zip(keys, row))


def _races_for_date(target_date: str) -> list[dict]:
    """N+1 クエリ問題を排除: サブクエリを LEFT JOIN + GROUP BY に置換。
    168 サブクエリ -> 1 集約クエリで 5-10 倍高速化。"""
    with db_connect() as conn:
        rows = conn.execute("""
            SELECT r.race_id, r.stadium_number, r.race_number, r.race_closed_at,
                   s.name AS stadium_name,
                   COALESCE(COUNT(res.boat_number), 0) AS results_count
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
                       e.assigned_motor_top_2_percent,
                       pv.exhibition_time, pv.start_timing_exhibition,
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
        "assigned_motor_top_2_percent", "exhibition_time", "start_timing_exhibition",
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


# Bootstrap CI で確実マイナスと検証された会場 (v0.2 検証)
_LOSING_VENUES = {2: "戸田", 7: "蒲郡", 10: "三国", 21: "芦屋"}
_QUESTIONABLE_VENUES = {4: "平和島", 8: "常滑", 19: "下関", 24: "大村"}


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
                "title": "💎 超本命レース",
                "msg": f"三連単1番人気 ¥{min_payout:,} (<500円帯)。2026検証 ROI +18.45% (CI +15.3%~+21.7%, P>0=100%)",
            }
        elif min_payout < 1000:
            result = {
                "favorite_trifecta_payout": min_payout,
                "tier": "confident",
                "expected_roi": 0.2741,
                "title": "💎💎 完全 +EV レース",
                "msg": f"三連単1番人気 ¥{min_payout:,} (500-1000円帯)。2026検証 ROI +27.41% (CI +25.3%~+29.6%, P>0=100%)。3連単1-2-3で +44.23%",
            }
        elif min_payout < 2000:
            result = {
                "favorite_trifecta_payout": min_payout,
                "tier": "moderate",
                "expected_roi": 0.1792,
                "title": "💎 やや本命 +EV",
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

                # L4 派生: A2 でも 134% (副推奨)
                if in_500_1000 and exclude_b and cls == 2:
                    extras.append({
                        "label": "📈 L4 A2 派生 (3連単1-2-3)",
                        "msg": "A2 1号艇 + B除外 + 本命500-1k で 3連単 1-2-3 = 検証 回収率 134.0% (CI 120.4%-148.9%, n=1,645)",
                        "bet": "3連単 1-2-3 を 100円 (税引前トントン)",
                        "expected_roi": 1.340,
                    })

                # L2 派生: 全クラス でも 145.7% (フィルタ緩め版)
                if in_500_1000 and exclude_b and cls not in (1, 2):
                    extras.append({
                        "label": "📈 L2 (3連単1-2-3)",
                        "msg": "B除外 + 本命500-1k で 3連単 1-2-3 = 検証 回収率 145.7% (CI 137.6%-154.1%, n=4,971)",
                        "bet": "3連単 1-2-3 を 100円",
                        "expected_roi": 1.457,
                    })

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

        # 【朝L4 A2 派生】prob_first 0.55-0.75 + A2 + B除外 (やや弱め)
        if 0.55 <= p1 < 0.75 and cls == 2 and b_excluded:
            return {
                "tier": "morning_l4_a2",
                "expected_roi": 0.34,
                "title": "🌅 朝L4 A2 派生候補",
                "msg": (f"モデル予測 1号艇A2 1着率 {p1*100:.1f}% + B除外。"
                        f"L4 A2派生 (検証 回収率 134%) の候補"),
                "is_morning": True,
                "favorite_trifecta_payout": None,
            }

        # 旧来の予測ベース判定 (A1/A2 ではないが 1号艇が強い)
        if p1 >= 0.80:
            return {
                "favorite_trifecta_payout": None,
                "tier": "predicted_confident",
                "expected_roi": 0.25,
                "title": "💎 (予測) 完全 +EV ゾーン候補",
                "msg": f"モデル予測 1号艇1着率 {p1*100:.1f}%。三連単1番人気が500-2000円帯になる可能性大。+EV ゾーン候補。実際の final odds で確定を",
                "is_morning": True,
            }
        if p1 >= 0.70:
            return {
                "favorite_trifecta_payout": None,
                "tier": "predicted_moderate",
                "expected_roi": 0.15,
                "title": "🎯 (予測) +EV 候補",
                "msg": f"モデル予測 1号艇1着率 {p1*100:.1f}%。1k-2k帯+EV ゾーン候補",
                "is_morning": True,
            }
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
        "boats": {},   # {boat_number: {exhibition_time, start_timing_exhibition, course_number, tilt_adjustment, weight_adjustment}}
    }
    with db_connect() as conn:
        rows = conn.execute("""
            SELECT boat_number, weather_number, wind_speed, wind_direction_number,
                   wave_height, temperature, water_temperature,
                   course_number, exhibition_time, start_timing_exhibition,
                   weight_adjustment, tilt_adjustment
              FROM race_previews
             WHERE race_id = ?
             ORDER BY boat_number
        """, (race_id,)).fetchall()
    if not rows:
        return out
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
    return out


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
    _DEFAULT_PRO = "dev-pro"  # noqa: S105
    if is_production and config.WEB_SESSION_SECRET == _DEFAULT_SECRET:
        logger.critical(
            "SECURITY: WEB_SESSION_SECRET is using DEFAULT value in production. "
            "Set BOATRACE_WEB_SECRET environment variable to a long random string."
        )
    if is_production:
        for pw_name, pw_val, default in [
            ("BOATRACE_MEMBER_PASSWORD", config.WEB_MEMBER_PASSWORD, _DEFAULT_MEMBER),
            ("BOATRACE_PRO_PASSWORD", config.WEB_PRO_PASSWORD, _DEFAULT_PRO),
        ]:
            if pw_val == default:
                logger.critical(
                    "SECURITY: %s is using DEFAULT value in production. "
                    "Set this env var to a strong password (16+ chars).",
                    pw_name,
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
        if path in ("/login", "/pro/login", "/logout") or "/api/" in path:
            # API は短時間 private キャッシュ (ブラウザのみ、CDN 経由しない)
            if "/api/" in path and not path.startswith(("/login", "/pro/")):
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
            # 静的ファイル: 1日キャッシュ + immutable
            response.headers["Cache-Control"] = "public, max-age=86400, immutable"
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
                    response.headers["Cache-Control"] = "public, max-age=3600"
                else:
                    response.headers["Cache-Control"] = "public, max-age=60"
            except Exception:
                response.headers["Cache-Control"] = "public, max-age=60"
        return response

    # robots.txt と sitemap.xml は最低限のレスポンスを返す
    # (ZAP の robots.txt パッシブスキャンで CSP/HSTS が無いと言われないように)
    @app.route("/robots.txt")
    def robots_txt():
        return ("User-agent: *\nDisallow: /login\nDisallow: /pro/\nDisallow: /api/\n",
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

    # テンプレートから is_member() / is_pro() を呼べるように
    app.jinja_env.globals["is_member"] = is_member
    app.jinja_env.globals["is_pro"] = is_pro

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

    @app.route("/healthz")
    def healthz():
        return {"status": "ok", "model_loaded": predictor.artifact is not None}

    @app.route("/")
    def index():
        target = request.args.get("date") or date.today().isoformat()
        return redirect(url_for("races", date=target))

    @app.route("/races")
    @cached(ttl=60, past_ttl=3600)  # 今日60秒/過去日1時間キャッシュ
    def races():
        target_date = request.args.get("date") or date.today().isoformat()
        races_list = _races_for_date(target_date)
        if not races_list:
            return render_template(
                "index.html",
                target_date=target_date,
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

        return render_template(
            "index.html",
            target_date=target_date,
            stadium_groups=sorted(stadium_groups.values(),
                                  key=lambda g: g["stadium_number"]),
            empty=False,
        )

    @app.route("/race/<race_id>")
    def race_detail(race_id: str):
        info = _race_basic_info(race_id)
        if not info:
            abort(404)

        try:
            preds = _race_predictions(predictor, race_id)
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
            return render_template(
                "race.html",
                info=info,
                preds=[],
                error=user_msg,
                racer_names={},
                trifecta_pw=[],
                trifecta_unified=[],
                conditions={},
            )

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

        return render_template(
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
            error=None,
        )

    @app.route("/api/race/<race_id>/value-bets")
    @member_only_api
    def race_value_bets(race_id: str):
        info = _race_basic_info(race_id)
        if not info:
            return jsonify({"error": "not found"}), 404
        snapshot = request.args.get("snapshot", "T-5min")
        thr = float(request.args.get("ev", "0.0"))
        try:
            r = predictor.find_value_bets_for_race(
                info["race_date"], race_id, snapshot_label=snapshot, ev_threshold=thr
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

    @app.route("/api/ev-races")
    @cached(ttl=180)  # 3分キャッシュ (EV計算は重い)
    def ev_races_for_date():
        """指定日の EV+ レース一覧 (UI のマーク表示用)
        snapshot: T-15min / T-5min / T-1min / final / auto (利用可能な最良に自動選択)
        """
        target_date = request.args.get("date") or date.today().isoformat()
        snapshot = request.args.get("snapshot", "auto")
        thr = float(request.args.get("ev", "0.0"))

        # auto モード: T-15min → T-5min → T-1min → final の順で利用可能なものを選択
        if snapshot == "auto":
            with db_connect() as conn:
                avail = {row[0] for row in conn.execute("""
                    SELECT DISTINCT o.snapshot_label FROM odds_trifecta o
                      JOIN races r ON o.race_id=r.race_id
                     WHERE r.race_date=? AND o.snapshot_label IS NOT NULL
                """, (target_date,)).fetchall()}
            for fb in ["T-15min", "T-5min", "T-1min", "final"]:
                if fb in avail:
                    snapshot = fb
                    break
            if snapshot == "auto":
                snapshot = "final"

        with db_connect() as conn:
            rows = conn.execute("""
                SELECT DISTINCT o.race_id
                  FROM odds_trifecta o
                  JOIN races r ON o.race_id = r.race_id
                 WHERE r.race_date = ? AND o.snapshot_label = ?
            """, (target_date, snapshot)).fetchall()
        race_ids = [r[0] for r in rows]
        ev_marks: dict[str, dict] = {}
        n_positive = 0
        for rid in race_ids:
            try:
                # 全レースの best EV を取得 (ev_threshold=-1.0 で全候補)
                # min_prob=0.05 で長尾過大評価を除外
                r = predictor.find_value_bets_for_race(
                    target_date, rid,
                    snapshot_label=snapshot,
                    ev_threshold=-1.0,
                    min_prob=0.05,
                    max_odds=100.0,
                )
                if r and r.get("value_bets"):
                    best = r["value_bets"][0]
                    ev_marks[rid] = {
                        "best_ev": best["adj_ev"],
                        "best_combo": best["combination"],
                        "best_prob": best["prob"],
                        "best_odds": best["odds"],
                        "n": sum(1 for v in r["value_bets"] if v["adj_ev"] >= thr),
                    }
                    if best["adj_ev"] >= thr:
                        n_positive += 1
            except Exception as e:
                logger.warning("ev-races failed for %s: %s", rid, e)
        return jsonify({"date": target_date, "snapshot": snapshot,
                        "n_marked": n_positive,
                        "n_total": len(ev_marks),
                        "ev_threshold": thr, "marks": ev_marks})

    @app.route("/api/race/<race_id>/whatif", methods=["POST"])
    def race_whatif(race_id: str):
        info = _race_basic_info(race_id)
        if not info:
            return jsonify({"error": "race not found"}), 404
        # 本番 Render では heavy compute 不可。ローカル用機能。
        if os.environ.get("RENDER") or os.environ.get("DISABLE_LIVE_PREDICT"):
            return jsonify({
                "error": "what-if simulation is unavailable on hosted env (memory limit). "
                         "Use local dev environment.",
            }), 503
        target_date = info["race_date"]
        overrides = (request.get_json(silent=True) or {}).get("overrides", {})
        try:
            result = predictor.predict_whatif(target_date, race_id, overrides)
        except Exception as e:
            logger.exception("whatif failed: %s", race_id)
            return jsonify({"error": str(e)}), 500
        if result is None:
            return jsonify({"error": "no data"}), 404
        names = _racer_names(race_id)
        for b in result["boats"]:
            b["racer_name"] = names.get(b["boat_number"])
        return jsonify(result)

    @app.route("/api/race/<race_id>")
    @cached(ttl=300)  # 5分キャッシュ
    def race_api(race_id: str):
        info = _race_basic_info(race_id)
        if not info:
            return jsonify({"error": "not found"}), 404
        try:
            preds = _race_predictions(predictor, race_id)
        except Exception as e:
            return jsonify({"error": str(e)}), 500
        return jsonify({"info": info, "predictions": preds})

    @app.route("/api/market-signals")
    @cached(ttl=300)  # 5分キャッシュ
    def market_signals_for_date():
        """指定日のレース一覧で「市場非効率ベース +EV」シグナルを返す。
        判定優先度:
          1. final オッズ (確定後)
          2. T-5min オッズ (締切5分前)
          3. T-15min オッズ (締切15分前)
        各レースのトリフェクタ1番人気の払戻を見て +EV/-EV ゾーンを判定
        """
        target_date = request.args.get("date") or date.today().isoformat()

        results: dict[str, dict] = {}
        with db_connect() as conn:
            # final 払戻 (確定済レース)
            cur = conn.execute("""
                SELECT r.race_id, MIN(pp.payout) as min_payout, 'final' as src
                FROM races r
                JOIN race_payouts pp ON r.race_id = pp.race_id AND pp.bet_type = 'trifecta'
                WHERE r.race_date = ?
                GROUP BY r.race_id
            """, (target_date,))
            for rid, mp, src in cur.fetchall():
                if mp:
                    results[rid] = {"min_payout": mp, "source": src}

            # T-5min / T-15min オッズ (未確定レース)
            # snapshot_label 列が未マイグレーションの環境 (Supabase など) では skip
            try:
                for snap_label in ["T-5min", "T-15min"]:
                    cur = conn.execute("""
                        SELECT r.race_id, MIN(o.odds) * 100 as min_payout
                        FROM races r
                        JOIN odds_trifecta o ON r.race_id = o.race_id
                        WHERE r.race_date = ? AND o.snapshot_label = ?
                        GROUP BY r.race_id
                    """, (target_date, snap_label))
                    for rid, mp in cur.fetchall():
                        if mp and rid not in results:
                            results[rid] = {"min_payout": int(mp), "source": snap_label}
            except Exception as e:
                # UndefinedColumn 等 (Supabase 側スキーマ未更新)。
                # final 払戻のみで縮退判定する。
                err = str(e).lower()
                if "snapshot_label" in err or "undefinedcolumn" in err or "column" in err:
                    # Postgres は失敗したトランザクションを ABORT 状態にするので rollback
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                else:
                    raise

        # === 朝判定用: 当日全レースの基本情報 + predictions を取得 ===
        # 例外は全て吸収し、本機能が使えなくても /api/market-signals は 200 を返す
        all_race_info: dict[str, dict] = {}
        morning_pred: dict[str, float] = {}
        try:
            with db_connect() as conn:
                try:
                    cur = conn.execute("""
                        SELECT r.race_id, r.stadium_number, r.race_grade_number,
                               e.class_number
                        FROM races r
                        LEFT JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
                        WHERE r.race_date = ?
                    """, (target_date,))
                    for rid, stadium, grade, cls in cur.fetchall():
                        all_race_info[rid] = {"stadium": stadium, "grade": grade, "class": cls}
                except Exception as e:
                    logger.warning("all_race_info query failed: %s", e)
                    try:
                        conn.rollback()
                    except Exception:
                        pass

                # predictions テーブルから 1号艇の prob_first を取得
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
        except Exception as e:
            logger.exception("morning L4 setup failed: %s", e)

        EXCLUDE_B = set(_LOSING_VENUES.keys()) | set(_QUESTIONABLE_VENUES.keys())

        def _evaluate_l4(stadium, grade, cls, mp_int):
            """確定オッズベース L4 マーク判定"""
            in_500_1000 = mp_int is not None and 500 <= mp_int < 1000
            b_excluded = stadium not in EXCLUDE_B if stadium is not None else False
            if not (in_500_1000 and b_excluded):
                return None
            if cls == 1:
                if grade == 1:
                    return {"level": "SG", "label": "👑L4 SG×A1",
                            "recovery": 258.2, "bet": "3連単 1-2-3", "n": 40}
                elif grade == 2:
                    return {"level": "G1", "label": "👑L4 G1×A1",
                            "recovery": 242.8, "bet": "3連単 1-2-3", "n": 227}
                elif grade == 3:
                    return {"level": "G2", "label": "👑L4 G2×A1",
                            "recovery": 242.7, "bet": "3連単 1-2-3", "n": 30}
                elif grade == 5:
                    return {"level": "general", "label": "🎯L4 一般戦×A1",
                            "recovery": 147.7, "bet": "3連単 1-2-3", "n": 1776}
                else:
                    return {"level": "default", "label": "🎯L4 A1",
                            "recovery": 160.8, "bet": "3連単 1-2-3", "n": 2210}
            elif cls == 2:
                return {"level": "a2", "label": "📈L4派生 A2",
                        "recovery": 134.0, "bet": "3連単 1-2-3", "n": 1645}
            return None

        def _evaluate_morning_l4(stadium, grade, cls, prob_first):
            """朝判定用 L4 候補マーク (prob_first ベース)"""
            if prob_first is None:
                return None
            b_excluded = stadium not in EXCLUDE_B if stadium is not None else False
            if not b_excluded:
                return None
            # 1号艇 A1 + prob_first 0.65-0.85 → 500-1000帯候補
            if cls == 1 and 0.65 <= prob_first < 0.85:
                if grade == 1:
                    return {"level": "morning_SG", "label": "🌅👑朝L4 SG候補",
                            "recovery": 258.2, "bet": "3連単 1-2-3 (確定後)", "n": 40,
                            "is_morning": True, "prob_first": prob_first}
                elif grade == 2:
                    return {"level": "morning_G1", "label": "🌅👑朝L4 G1候補",
                            "recovery": 242.8, "bet": "3連単 1-2-3 (確定後)", "n": 227,
                            "is_morning": True, "prob_first": prob_first}
                elif grade == 3:
                    return {"level": "morning_G2", "label": "🌅👑朝L4 G2候補",
                            "recovery": 242.7, "bet": "3連単 1-2-3 (確定後)", "n": 30,
                            "is_morning": True, "prob_first": prob_first}
                elif grade == 5:
                    return {"level": "morning_general", "label": "🌅🎯朝L4 一般戦候補",
                            "recovery": 147.7, "bet": "3連単 1-2-3 (確定後)", "n": 1776,
                            "is_morning": True, "prob_first": prob_first}
                else:
                    return {"level": "morning_default", "label": "🌅🎯朝L4 候補",
                            "recovery": 160.8, "bet": "3連単 1-2-3 (確定後)", "n": 2210,
                            "is_morning": True, "prob_first": prob_first}
            # 1号艇 A2 + prob_first 0.55-0.75 → A2 派生候補
            elif cls == 2 and 0.55 <= prob_first < 0.75:
                return {"level": "morning_a2", "label": "🌅📈朝L4 A2候補",
                        "recovery": 134.0, "bet": "3連単 1-2-3 (確定後)", "n": 1645,
                        "is_morning": True, "prob_first": prob_first}
            return None

        signals = []
        # 当日全レースを走査 (確定済 → L4、未確定 → 朝L4候補)
        # all_race_info が空 (DB エラー等) なら results のレースだけ処理
        race_iterable = all_race_info if all_race_info else {rid: {} for rid in results}
        for rid, info in race_iterable.items():
            stadium = info.get("stadium")
            grade = info.get("grade")
            cls = info.get("class")
            data = results.get(rid)

            if data:
                # === 確定オッズあり (L4 マーク) ===
                mp = data["min_payout"]
                src = data["source"]
                if mp < 500:
                    tier, expected_roi, title = "ultra_confident", 0.1845, "💎 超本命"
                elif mp < 1000:
                    tier, expected_roi, title = "confident", 0.2741, "💎💎 完全+EV"
                elif mp < 2000:
                    tier, expected_roi, title = "moderate", 0.1792, "💎 やや本命"
                elif mp < 5000:
                    tier, expected_roi, title = "split", -0.0859, "拮抗"
                elif mp < 10000:
                    tier, expected_roi, title = "wild", -0.4310, "荒れ寄り"
                else:
                    tier, expected_roi, title = "chaos", -0.7354, "波乱"

                l4 = _evaluate_l4(stadium, grade, cls, mp)

                signals.append({
                    "race_id": rid,
                    "tier": tier,
                    "min_payout": mp,
                    "source": src,
                    "expected_roi": expected_roi,
                    "title": title,
                    "is_positive_ev": expected_roi > 0,
                    "l4": l4,
                })
            else:
                # === 未確定 (朝判定) → 予測ベース L4 候補 ===
                prob_first = morning_pred.get(rid)
                morning_l4 = _evaluate_morning_l4(stadium, grade, cls, prob_first)
                if morning_l4:
                    signals.append({
                        "race_id": rid,
                        "tier": "morning_l4",
                        "min_payout": None,
                        "source": "morning_predict",
                        "expected_roi": (morning_l4["recovery"] - 100) / 100,
                        "title": morning_l4["label"],
                        "is_positive_ev": morning_l4["recovery"] >= 130,
                        "l4": morning_l4,
                    })

        return jsonify({
            "date": target_date,
            "n_races": len(signals),
            "n_positive_ev": sum(1 for s in signals if s["is_positive_ev"]),
            "n_l4": sum(1 for s in signals if s["l4"]),
            "n_morning_l4": sum(1 for s in signals if s.get("l4") and s["l4"].get("is_morning")),
            "signals": {s["race_id"]: s for s in signals},
        })

    # =====================================================
    # 会員プラン: L4 戦略 日別 ROI ダッシュボード
    # =====================================================

    EXCLUDE_B_VENUES = {2, 7, 10, 21, 4, 8, 19, 24}

    def _l4_daily_stats(from_date: str, to_date: str) -> list[dict]:
        """日別の L4 戦略統計を集計。
        L4 条件: 三連単本命 500-1000 + B除外 + 1号艇A1
        集計対象: 単勝 / 2連単1-2 / 3連単1-2-3
        """
        with db_connect() as conn:
            cur = conn.execute("""
                SELECT
                    r.race_date,
                    r.stadium_number,
                    r.race_grade_number,
                    e.class_number,
                    pp.min_pay AS fav,
                    res1.boat_number AS w1,
                    res2.boat_number AS w2,
                    res3.boat_number AS w3,
                    pw.payout AS win_pay,
                    pe.payout AS exacta_pay,
                    pt.payout AS tri_pay
                FROM races r
                LEFT JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
                JOIN (SELECT race_id, MIN(payout) AS min_pay FROM race_payouts
                      WHERE bet_type='trifecta' GROUP BY race_id) pp ON pp.race_id = r.race_id
                LEFT JOIN race_results res1 ON res1.race_id = r.race_id AND res1.finishing_position=1
                LEFT JOIN race_results res2 ON res2.race_id = r.race_id AND res2.finishing_position=2
                LEFT JOIN race_results res3 ON res3.race_id = r.race_id AND res3.finishing_position=3
                LEFT JOIN race_payouts pw ON pw.race_id = r.race_id AND pw.bet_type='win' AND pw.combination='1'
                LEFT JOIN race_payouts pe ON pe.race_id = r.race_id AND pe.bet_type='exacta' AND pe.combination='1-2'
                LEFT JOIN race_payouts pt ON pt.race_id = r.race_id AND pt.bet_type='trifecta' AND pt.combination='1-2-3'
                WHERE r.race_date BETWEEN ? AND ?
                ORDER BY r.race_date
            """, (from_date, to_date)).fetchall()

        # 日別に集計
        by_date: dict[str, dict] = {}
        for row in cur:
            (rdate, stadium, grade, cls, fav, w1, w2, w3,
             win_pay, ex_pay, tri_pay) = row
            d = by_date.setdefault(rdate, {
                "date": rdate,
                "n_total": 0,
                "n_l4": 0,
                "win_bets": 0, "win_hits": 0, "win_pay": 0,
                "exa_bets": 0, "exa_hits": 0, "exa_pay": 0,
                "tri_bets": 0, "tri_hits": 0, "tri_pay": 0,
                "grade_breakdown": {},  # grade -> {n, hits, pay}
            })
            d["n_total"] += 1
            # L4 条件
            if (fav and 500 <= fav < 1000
                and stadium not in EXCLUDE_B_VENUES
                and cls == 1):
                d["n_l4"] += 1
                # 単勝
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
                if w1 == 1 and w2 == 2 and w3 == 3:
                    d["tri_hits"] += 1
                    d["tri_pay"] += (tri_pay or 0)
                # グレード別集計
                g_key = grade or 5
                gb = d["grade_breakdown"].setdefault(g_key, {
                    "n": 0, "tri_hits": 0, "tri_pay": 0
                })
                gb["n"] += 1
                if w1 == 1 and w2 == 2 and w3 == 3:
                    gb["tri_hits"] += 1
                    gb["tri_pay"] += (tri_pay or 0)

        # ROI 計算
        for d in by_date.values():
            for bet in ("win", "exa", "tri"):
                n = d[f"{bet}_bets"]
                pay = d[f"{bet}_pay"]
                d[f"{bet}_roi"] = (pay - 100 * n) / (100 * n) * 100 if n else None
                d[f"{bet}_recovery"] = pay / (100 * n) * 100 if n else None
                d[f"{bet}_profit"] = pay - 100 * n if n else 0

        return sorted(by_date.values(), key=lambda x: x["date"], reverse=True)

    @app.route("/member/strategy")
    @login_required
    def member_strategy():
        """L4 戦略の日別 ROI ダッシュボード (会員限定)"""
        from datetime import timedelta
        today = date.today()
        to_d = request.args.get("to") or today.isoformat()
        from_d = request.args.get("from") or (today - timedelta(days=30)).isoformat()
        try:
            date.fromisoformat(to_d); date.fromisoformat(from_d)
        except ValueError:
            return "Invalid date format", 400

        rows = _l4_daily_stats(from_d, to_d)

        # 通算集計
        totals = {
            "n_total": sum(r["n_total"] for r in rows),
            "n_l4": sum(r["n_l4"] for r in rows),
            "win_bets": sum(r["win_bets"] for r in rows),
            "win_hits": sum(r["win_hits"] for r in rows),
            "win_pay": sum(r["win_pay"] for r in rows),
            "exa_bets": sum(r["exa_bets"] for r in rows),
            "exa_hits": sum(r["exa_hits"] for r in rows),
            "exa_pay": sum(r["exa_pay"] for r in rows),
            "tri_bets": sum(r["tri_bets"] for r in rows),
            "tri_hits": sum(r["tri_hits"] for r in rows),
            "tri_pay": sum(r["tri_pay"] for r in rows),
        }
        for bet in ("win", "exa", "tri"):
            n = totals[f"{bet}_bets"]
            pay = totals[f"{bet}_pay"]
            totals[f"{bet}_roi"] = (pay - 100*n)/(100*n)*100 if n else None
            totals[f"{bet}_recovery"] = pay/(100*n)*100 if n else None
            totals[f"{bet}_profit"] = pay - 100*n if n else 0

        return render_template(
            "member_strategy.html",
            rows=rows,
            totals=totals,
            from_date=from_d,
            to_date=to_d,
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
        rows = _l4_daily_stats(from_d, to_d)
        # 日付昇順 (グラフ用)
        rows = sorted(rows, key=lambda x: x["date"])
        # JSON 化用に整形 (grade_breakdown はキー文字列化)
        for r in rows:
            r["grade_breakdown"] = {str(k): v for k, v in r.get("grade_breakdown", {}).items()}
        return jsonify({"from": from_d, "to": to_d, "rows": rows})

    # =====================================================
    # Pro プラン: T-15min 期待値モニター
    # =====================================================

    @app.route("/pro/ev")
    @pro_required
    def pro_ev():
        target_date = request.args.get("date") or date.today().isoformat()
        snapshot = request.args.get("snapshot") or "T-15min"

        # 指定 snapshot に該当データが無ければ final にフォールバック
        with db_connect() as conn:
            cnt = conn.execute("""
                SELECT COUNT(DISTINCT o.race_id) FROM odds_trifecta o
                  JOIN races r ON o.race_id=r.race_id
                 WHERE r.race_date=? AND o.snapshot_label=?
            """, (target_date, snapshot)).fetchone()[0]
        if cnt == 0:
            snapshot = "final"  # 暫定: T-15min 未蓄積期は final で代替

        # 該当日 + snapshot のあるレースを取得
        with db_connect() as conn:
            rows = conn.execute("""
                SELECT DISTINCT r.race_id, r.stadium_number, r.race_number,
                       r.race_closed_at, s.name AS stadium_name,
                       (SELECT COUNT(*) FROM race_results WHERE race_id = r.race_id) AS results_count
                  FROM races r
                  JOIN stadiums s ON r.stadium_number = s.stadium_number
                  JOIN odds_trifecta o ON o.race_id = r.race_id
                 WHERE r.race_date = ?
                   AND o.snapshot_label = ?
                 ORDER BY r.race_closed_at, r.stadium_number
            """, (target_date, snapshot)).fetchall()
        keys = ["race_id", "stadium_number", "race_number", "race_closed_at",
                "stadium_name", "results_count"]
        races = [dict(zip(keys, r)) for r in rows]

        # 各レースの best EV を計算
        # min_prob=0.05 でモデル長尾過大評価を除外 (True Value Bet 検証で判明)
        ev_min = float(request.args.get("ev_min", "-1.0"))
        for r in races:
            try:
                vb = predictor.find_value_bets_for_race(
                    target_date, r["race_id"], snapshot_label=snapshot,
                    ev_threshold=-1.0, max_odds=100.0, min_prob=0.05,
                )
                if vb and vb.get("value_bets"):
                    best = vb["value_bets"][0]
                    r["best_combo"] = best["combination"]
                    r["best_ev"] = best["adj_ev"]
                    r["best_prob"] = best["prob"]
                    r["best_odds"] = best["odds"]
                    r["n_positive_ev"] = sum(1 for v in vb["value_bets"]
                                              if v["adj_ev"] >= 0)
                else:
                    r["best_combo"] = None
                    r["best_ev"] = None
                    r["n_positive_ev"] = 0
            except Exception as e:
                logger.warning("pro_ev calc failed for %s: %s", r["race_id"], e)
                r["best_ev"] = None
                r["n_positive_ev"] = 0

            # 会場警告
            sn = r["stadium_number"]
            if sn in _LOSING_VENUES:
                r["venue_warning"] = "danger"
            elif sn in _QUESTIONABLE_VENUES:
                r["venue_warning"] = "caution"
            else:
                r["venue_warning"] = None

        # フィルター適用
        if ev_min > -1.0:
            races = [r for r in races if (r["best_ev"] or -99) >= ev_min]

        return render_template(
            "pro_ev.html",
            target_date=target_date,
            races=races,
            snapshot=snapshot,
            ev_min=ev_min,
            n_total=len(rows),
            n_filtered=len(races),
        )

    @app.route("/pro/ev/race/<race_id>")
    @pro_required
    def pro_ev_race(race_id: str):
        info = _race_basic_info(race_id)
        if not info:
            abort(404)
        snapshot = request.args.get("snapshot", "T-15min")
        target_date = info["race_date"]

        # T-15min が無ければ自動 fallback (T-5min → final)
        with db_connect() as conn:
            avail = {
                row[0] for row in conn.execute(
                    "SELECT DISTINCT snapshot_label FROM odds_trifecta WHERE race_id=?",
                    (race_id,)
                ).fetchall()
            }
        if snapshot not in avail:
            for fb in ["T-15min", "T-5min", "T-1min", "final"]:
                if fb in avail:
                    snapshot = fb
                    break

        try:
            vb = predictor.find_value_bets_for_race(
                target_date, race_id, snapshot_label=snapshot,
                ev_threshold=-1.0, max_odds=200.0, min_prob=0.03,
            )
        except Exception as e:
            logger.exception("pro_ev_race failed: %s", race_id)
            vb = None

        names = _racer_names(race_id)
        try:
            preds = _race_predictions(predictor, race_id)
        except Exception:
            preds = []

        # 戦略タグ
        sn = info["stadium_number"]
        venue_warning = None
        if sn in _LOSING_VENUES:
            venue_warning = {
                "level": "danger", "venue": _LOSING_VENUES[sn],
                "msg": "Bootstrap CI で確実マイナスと検証済の会場",
            }
        elif sn in _QUESTIONABLE_VENUES:
            venue_warning = {
                "level": "caution", "venue": _QUESTIONABLE_VENUES[sn],
                "msg": "ROI 弱マイナス会場 (慎重)",
            }

        # Kelly 比率の参考計算 (kelly = (b*p - (1-p)) / b, b=odds-1)
        if vb and vb.get("value_bets"):
            for bet in vb["value_bets"]:
                p, o = bet["prob"], bet["odds"]
                if o > 1.0 and p > 0:
                    b = o - 1.0
                    kelly = (b * p - (1 - p)) / b
                    bet["kelly"] = max(0.0, kelly) * config.KELLY_FRACTION
                else:
                    bet["kelly"] = 0.0
                # フェアオッズ (controlled for takeout=25%)
                bet["fair_odds"] = (1.0 / p) if p > 0 else None

        return render_template(
            "pro_ev_race.html",
            info=info,
            preds=preds,
            racer_names=names,
            value_bet=vb,
            snapshot=snapshot,
            venue_warning=venue_warning,
        )

    @app.route("/api/pro/ev/<race_id>")
    @pro_only_api
    def pro_ev_api(race_id: str):
        info = _race_basic_info(race_id)
        if not info:
            return jsonify({"error": "not found"}), 404
        snapshot = request.args.get("snapshot", "T-15min")
        try:
            vb = predictor.find_value_bets_for_race(
                info["race_date"], race_id, snapshot_label=snapshot,
                ev_threshold=-1.0, max_odds=500.0,
            )
        except Exception as e:
            return jsonify({"error": str(e)}), 500
        if not vb:
            return jsonify({"race_id": race_id, "snapshot": snapshot,
                            "available": False})
        return jsonify({"race_id": race_id, "snapshot": snapshot,
                        "available": True, **vb})

    return app
