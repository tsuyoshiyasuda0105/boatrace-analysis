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
            try:
                qs = request.query_string.decode("utf-8") if request else ""
            except Exception:
                qs = ""
            key = f"{fn.__name__}:{args}:{kwargs}:{qs}"
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

    # 全テンプレートに today_iso を自動注入 (BOATRACE WEB リンク等で使用)
    @app.context_processor
    def _inject_today():
        return {"today_iso_global": date.today().isoformat()}

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

        # ===== 起動後バックグラウンドで今日の三連単キャッシュを温める =====
        # /api/ev-races の初回コールド ~40 秒のレイテンシ (predict_date + per_winner)
        # を完全に隠す。ユーザの初回アクセスは即キャッシュヒット。
        # 失敗してもアプリは動く (例: モデル未ロード時)。
        if predictor.artifact is not None:
            import threading as _th
            def _bg_warm_today():
                try:
                    today = date.today().isoformat()
                    logger.info("[bg-warm] starting trifecta cache warm for %s", today)
                    t0 = time.time()
                    predictor.predict_date(today)
                    n = predictor.warm_trifecta_cache(today)
                    logger.info("[bg-warm] completed %s: %d races in %.1fs",
                                today, n, time.time()-t0)
                except Exception as e:
                    logger.warning("[bg-warm] failed: %s", e)
            _th.Thread(target=_bg_warm_today, daemon=True, name="bg-warm-today").start()

    @app.route("/healthz")
    def healthz():
        return {"status": "ok", "model_loaded": predictor.artifact is not None}

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
    @cached(ttl=60, past_ttl=3600)  # 今日60秒/過去日1時間キャッシュ
    def races():
        target_date = request.args.get("date") or date.today().isoformat()
        races_list = _races_for_date(target_date)
        if not races_list:
            return render_template(
                "index.html",
                target_date=target_date,
                today_iso=date.today().isoformat(),
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
            today_iso=date.today().isoformat(),
            stadium_groups=sorted(stadium_groups.values(),
                                  key=lambda g: g["stadium_number"]),
            empty=False,
        )

    @app.route("/race/<race_id>")
    @login_required
    @cached(ttl=60, past_ttl=3600)
    def race_detail(race_id: str):
        # 過去レース (race_date が今日より前) は 1時間キャッシュ、当日は 60秒
        # cached デコレータは request.args["date"] を見るが、/race/<id> には
        # クエリ無しなので race_id から日付を取り出して past_ttl を有効化する
        # (cached 内部の effective_ttl をオーバーライドできないため、副作用なし)
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
    @member_only_api
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

        # オッズと race_id を **1 クエリで** 取得 (旧実装は race_id 取得→各レースで再クエリ)
        with db_connect() as conn:
            rows = conn.execute("""
                SELECT o.race_id, o.combination, o.odds
                  FROM odds_trifecta o
                  JOIN races r ON o.race_id = r.race_id
                 WHERE r.race_date = ? AND o.snapshot_label = ?
            """, (target_date, snapshot)).fetchall()
        odds_by_race: dict[str, dict[str, float]] = {}
        for rid, comb, o in rows:
            try:
                odds_by_race.setdefault(rid, {})[comb] = float(o)
            except (TypeError, ValueError):
                continue
        race_ids = list(odds_by_race.keys())

        # 三連単 joint 確率を **1 回の batch** で全レース計算 → 個別 cache に格納
        # (個別 find_value_bets_for_race は cache を hit するだけになる)
        try:
            predictor.warm_trifecta_cache(target_date)
        except Exception as e:
            logger.warning("warm_trifecta_cache failed: %s", e)

        # decay_table も 1 回だけロード (各レース計算で使い回し)
        try:
            from src.analysis.decay_factor import load_decay_table
            decay_table_shared = load_decay_table()
        except Exception:
            decay_table_shared = None

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
                    odds_lookup=odds_by_race.get(rid),
                    decay_table=decay_table_shared,
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

    @app.route("/api/race/<race_id>")
    @member_only_api
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

    @app.route("/api/odds-123-timeline")
    @member_only_api
    @cached(ttl=20)  # 20秒キャッシュ (JS 側 60秒ポーリング、複数タブ/ユーザ間で再利用)
    def odds_123_timeline():
        """指定日の各レースの '1-2-3' 三連単オッズ推移を返す。
        odds_scheduler が T-5min..T-1min で毎分スナップショットを残す前提。
        本日お金を入れる候補レース欄で締切までのオッズ変動を可視化するため、
        ブラウザ側で 30 秒ごとに再取得して描画する。
        20 秒キャッシュ: 同時アクセスで Supabase に同じクエリが集中するのを防ぐ。
        """
        target_date = request.args.get("date") or date.today().isoformat()
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
        return jsonify({"date": target_date, "odds": result})

    @app.route("/api/market-signals")
    @member_only_api
    @cached(ttl=300)  # 5分キャッシュ
    def market_signals_for_date():
        """指定日のレース一覧で「市場非効率ベース +EV」シグナルを返す。
        判定優先度 (L4 戦略の定義に合わせ T-X 1-2-3 オッズを最優先):
          1. T-1min / T-2min / T-3min / T-4min / T-5min / T-15min 1-2-3 オッズ × 100
             (= 朝賭けた時点の本命金額。1-2-3 ハズレ後の race_payouts より優先)
          2. final 払戻 MIN (上記オッズが無い過去日のフォールバック)
        各レースのトリフェクタ1番人気の払戻を見て +EV/-EV ゾーンを判定
        """
        target_date = request.args.get("date") or date.today().isoformat()

        # ★パフォーマンス最適化 (backlog item 11):
        # 旧実装は 8 個の SQL × 3 個の db_connect() で Supabase 往復が
        # 重く 9.6 秒かかっていた。下記で単一接続 + クエリ統合化。
        #   - 6 個の snapshot_label 別ループ → IN 句 1 クエリ
        #   - all_race_info / morning_pred / final_payout / course1_stats
        #     を同じ conn で実行
        from datetime import datetime, timedelta as _td
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
            _td_dt = datetime.fromisoformat(target_date).date()
            cutoff_date_iso = (_td_dt - _td(days=COURSE1_WINDOW_DAYS)).isoformat()
        except Exception:
            cutoff_date_iso = "1900-01-01"

        results: dict[str, dict] = {}
        all_race_info: dict[str, dict] = {}
        morning_pred: dict[str, float] = {}
        course1_stats: dict[str, tuple[float, int]] = {}

        # T-X snapshot の優先度 (小さいほど優先)
        _SNAP_PRIORITY = {
            "T-1min": 1, "T-2min": 2, "T-3min": 3, "T-4min": 4,
            "T-5min": 5, "T-15min": 6,
        }

        try:
            with db_connect() as conn:
                # === 1. T-X 1-2-3 オッズ (IN 句 1 クエリ統合) ===
                try:
                    cur = conn.execute("""
                        SELECT r.race_id, o.snapshot_label, o.odds * 100 AS min_payout
                          FROM races r
                          JOIN odds_trifecta o ON r.race_id = o.race_id
                         WHERE r.race_date = ?
                           AND o.combination = '1-2-3'
                           AND o.snapshot_label IN ('T-1min','T-2min','T-3min','T-4min','T-5min','T-15min')
                    """, (target_date,))
                    # priority が小さい snapshot を優先採用
                    for rid, label, mp in cur.fetchall():
                        if not mp:
                            continue
                        prio = _SNAP_PRIORITY.get(label, 99)
                        existing = results.get(rid)
                        if existing is None or _SNAP_PRIORITY.get(existing["source"], 99) > prio:
                            results[rid] = {"min_payout": int(mp), "source": label}
                except Exception as e:
                    err = str(e).lower()
                    if "snapshot_label" in err or "undefinedcolumn" in err or "column" in err:
                        try:
                            conn.rollback()
                        except Exception:
                            pass
                    else:
                        raise

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
                    cur = conn.execute("""
                        SELECT r.race_id, r.stadium_number, r.race_grade_number,
                               r.race_number,
                               e.class_number,
                               e.national_top_1_percent, e.local_top_1_percent,
                               e.avg_start_timing, e.age,
                               pv.weather_number, pv.start_timing_exhibition,
                               e2.national_top_2_percent AS boat2_top2
                        FROM races r
                        LEFT JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
                        LEFT JOIN race_entries e2 ON e2.race_id = r.race_id AND e2.boat_number = 2
                        LEFT JOIN race_previews pv ON pv.race_id = r.race_id AND pv.boat_number = 1
                        WHERE r.race_date = ?
                    """, (target_date,))
                    for (rid, stadium, grade, race_no, cls, natl1, loc1,
                         avg_st, age, weather, ex_st, boat2_top2) in cur.fetchall():
                        all_race_info[rid] = {
                            "stadium": stadium, "grade": grade,
                            "race_number": race_no,
                            "class": cls,
                            "natl_1": natl1, "local_1": loc1,
                            "avg_st": avg_st, "age": age,
                            "weather": weather, "ex_st": ex_st,
                            "boat2_top2": boat2_top2,
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
                # 旧実装は別 connection を開いていた → 同一 conn に統合
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
        except Exception as e:
            logger.exception("market-signals setup failed: %s", e)

        EXCLUDE_B = set(_LOSING_VENUES.keys()) | set(_QUESTIONABLE_VENUES.keys())

        def _l4_rank(natl_1, local_1):
            """1号艇選手の成績から L4 のサブランク判定 (単一情報源を委譲)"""
            return _l4_rank_shared(natl_1, local_1)

        def _evaluate_l4(stadium, grade, cls, mp_int, natl_1=None, local_1=None,
                         race_id=None, avg_st=None, age=None, ex_st=None,
                         boat2_top2=None, race_number=None):
            """確定オッズベース L4 マーク判定 (L4+ / L4++ ランク付き)

            一般戦 (grade=5) は F1 条件
              「1号艇 国1%≥7 ∧ 2号艇 国2連率≥40」
            を満たす場合のみ採用候補 (is_reference=False)。
            それ以外の一般戦は従来通り参考扱い (is_reference=True)。
            OOS Tier 1 認定 (4年 ROI 204% / CI 下限 ≥150%)。
            """
            in_500_1000 = mp_int is not None and 500 <= mp_int < 1000
            b_excluded = stadium not in EXCLUDE_B if stadium is not None else False
            if not (in_500_1000 and b_excluded):
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
                    if n1 >= 7.0 and b2 >= 40.0:
                        # ★ F1 該当: 採用候補 (本日候補/メール対象)
                        base = {"level": "general_f1",
                                "label": "🌟L4 G++ (一般×国1%≥7×2号40)",
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
            if is_l4_pro(avg_st, age, ex_st):
                base["is_l4_pro"] = True
                base["recovery_l4_pro"] = L4_PRO_RECOVERY
                _ex_part = f", 展示ST={float(ex_st):.2f}" if ex_st is not None else " (展示前)"
                base["label_l4_pro"] = (
                    f"🔥L4 PRO (ST={float(avg_st):.2f}, "
                    f"{int(age)}歳{_ex_part})"
                )

            # ▼ L4-prime / L4-12R / 一般戦×12R 観察フラグ (3 ヶ月実績で採用判断)
            # base が確定したレース (= L4 universe 通過済) なら race_number で判定
            try:
                rn = int(race_number) if race_number is not None else 0
            except (TypeError, ValueError):
                rn = 0
            if rn in (11, 12):
                base["is_obs_prime"] = True
            if rn == 12:
                base["is_obs_r12"] = True
                if grade == 5:
                    base["is_obs_gen_r12"] = True
            return base

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
            b_excluded = stadium not in EXCLUDE_B if stadium is not None else False
            if not b_excluded:
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
                elif grade == 4:
                    base = {"level": "morning_G3", "label": "🌅🎯朝L4 G3候補",
                            "recovery": 149.2, "n": 195}
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
                        base = {"level": "morning_general_f1",
                                "label": "🌅🌟朝L4 G++ 候補",
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
            if is_l4_pro(avg_st, age, ex_st):
                base["is_l4_pro"] = True
                base["recovery_l4_pro"] = L4_PRO_RECOVERY
                _ex_part = f", 展示ST={float(ex_st):.2f}" if ex_st is not None else " (展示前)"
                base["label_l4_pro"] = (
                    f"🔥L4 PRO (ST={float(avg_st):.2f}, "
                    f"{int(age)}歳{_ex_part})"
                )
            # 参考レース (一般戦) と F1 一般戦は rank 補正をスキップ (固有の検証値を保持)
            if rec_override is not None and not base.get("is_reference") and not base.get("is_f1"):
                base["recovery"] = rec_override
                base["label"] = f"{rank_emoji}{base['label']} ({rank_label})"

            # ▼ L4-prime / L4-12R / 一般戦×12R 観察フラグ
            try:
                rn = int(race_number) if race_number is not None else 0
            except (TypeError, ValueError):
                rn = 0
            if rn in (11, 12):
                base["is_obs_prime"] = True
            if rn == 12:
                base["is_obs_r12"] = True
                if grade == 5:
                    base["is_obs_gen_r12"] = True
            return base

        signals = []
        # 当日全レースを走査 (確定済 → L4、未確定 → 朝L4候補)
        # all_race_info が空 (DB エラー等) なら results のレースだけ処理
        race_iterable = all_race_info if all_race_info else {rid: {} for rid in results}
        WEATHER_LABEL = {1:"☀️ 晴", 2:"🌤️ 曇", 3:"☔ 雨", 4:"❄️ 雪"}
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
            boat2_top2 = info.get("boat2_top2")
            is_rain = (weather == 3)
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

                l4 = _evaluate_l4(stadium, grade, cls, mp, natl_1, local_1, race_id=rid,
                                  avg_st=avg_st, age=age, ex_st=ex_st,
                                  boat2_top2=boat2_top2, race_number=race_no_info)
                # ☔ 雨レースは L4 候補から除外 (ROI 100% で break-even)
                # ただし最近のレースで「これから ROI 100% かもしれない」と分かるよう
                # バッジは出すが is_rain=True で本日候補リストから除外
                if l4 and is_rain:
                    l4["is_rain"] = True
                    l4["is_reference"] = True  # 候補リスト除外フラグ
                    l4["label"] = f"☔{l4['label']} (雨除外)"

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
                if morning_l4:
                    if is_rain:
                        morning_l4["is_rain"] = True
                        morning_l4["is_reference"] = True
                        morning_l4["label"] = f"☔{morning_l4['label']} (雨除外)"
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
            # 別途、日別の総レース数を取得 (確定有無に関わらず)
            # _l4_daily_stats の n_total が「確定済のみ」だと
            # 当日朝のように 1 件しか確定してない時 156→1 と見えてしまう
            cur_n = conn.execute("""
                SELECT race_date, COUNT(*) FROM races
                 WHERE race_date BETWEEN ? AND ? GROUP BY race_date
            """, (from_date, to_date)).fetchall()
            n_total_by_date = {row[0]: row[1] for row in cur_n}

            # 集計 SQL: race_payouts/odds_trifecta/predictions 全部 LEFT JOIN し、
            # confirmed / odds / morning_miss / morning の 4 ソースで L4 判定する
            cur = conn.execute("""
                SELECT
                    r.race_date,
                    r.stadium_number,
                    r.race_grade_number,
                    r.race_number,
                    e.class_number,
                    e.national_top_1_percent AS natl_1,
                    e2.national_top_2_percent AS boat2_top2,
                    pp.min_pay AS fav_pay,
                    oo.min_odds AS fav_odds,
                    pr.prob_first AS prob_first,
                    res1.boat_number AS w1,
                    res2.boat_number AS w2,
                    res3.boat_number AS w3,
                    pw.payout AS win_pay,
                    pe.payout AS exacta_pay,
                    pt.payout AS tri_pay,
                    pv.weather_number AS weather
                FROM races r
                LEFT JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
                LEFT JOIN race_entries e2 ON e2.race_id = r.race_id AND e2.boat_number = 2
                LEFT JOIN (SELECT race_id, MIN(payout) AS min_pay FROM race_payouts
                           WHERE bet_type='trifecta' GROUP BY race_id) pp ON pp.race_id = r.race_id
                LEFT JOIN (SELECT race_id, MIN(odds) AS min_odds FROM odds_trifecta
                           WHERE combination='1-2-3'
                             AND snapshot_label IN ('T-1min','T-2min','T-3min','T-4min','T-5min','T-15min','final')
                           GROUP BY race_id) oo ON oo.race_id = r.race_id
                LEFT JOIN (SELECT race_id, prob_first FROM predictions
                           WHERE boat_number=1) pr ON pr.race_id = r.race_id
                LEFT JOIN race_previews pv ON pv.race_id = r.race_id AND pv.boat_number = 1
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
                # L4-prime / L4-12R / 一般戦×12R 観察 (3 ヶ月実績で採用判断)
                # prime_*   = L4 universe × 11-12R 限定 (ROI 検証 185%)
                # r12_*     = L4 universe × 12R のみ (ROI 検証 193%)
                # gen_r12_* = 一般戦 × 12R 限定 (ROI 検証 189%)
                "prime_tri_bets": 0, "prime_tri_hits": 0, "prime_tri_pay": 0,
                "r12_tri_bets": 0, "r12_tri_hits": 0, "r12_tri_pay": 0,
                "gen_r12_tri_bets": 0, "gen_r12_tri_hits": 0, "gen_r12_tri_pay": 0,
                "grade_breakdown": {},
            }

        for row in cur:
            (rdate, stadium, grade, race_no, cls, natl_1, boat2_top2,
             fav_pay, fav_odds, prob_first,
             w1, w2, w3, win_pay, ex_pay, tri_pay, weather) = row
            # ☔ 雨除外フィルタ: weather_number=3 (雨) のレースは
            # backtest で ROI 100.8% (break-even) のためベット候補から除外。
            # weather NULL (= 直前情報未取得 or 古いデータ) は通常通り集計。
            if weather == 3:
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
                # L4-prime / L4-12R / 一般戦×12R 観察
                "prime_tri_bets": 0, "prime_tri_hits": 0, "prime_tri_pay": 0,
                "r12_tri_bets": 0, "r12_tri_hits": 0, "r12_tri_pay": 0,
                "gen_r12_tri_bets": 0, "gen_r12_tri_hits": 0, "gen_r12_tri_pay": 0,
                "grade_breakdown": {},
            })
            # 確定済 (race_payouts trifecta あり) ならカウント
            is_done = w1 is not None and w2 is not None and w3 is not None
            if is_done:
                d["n_done"] += 1

            # === L4 候補判定 (T-X オッズ優先、race_payouts MIN フォールバック) ===
            # L4 の正式定義は「3連単 1-2-3 の事前オッズ × 100 が 500-1000円帯」。
            # 結果 (1-2-3 hit/miss) は L4 候補性に影響しない。
            if stadium in EXCLUDE_B_VENUES:
                continue
            if cls != 1:
                continue

            # is_l4_base 判定は grade 関係なく必要 (一般戦の分離追跡用)
            is_l4_base = False
            if fav_odds is not None:
                # T-X 1-2-3 オッズ ベース (朝賭けた時点の本命金額)
                fav_int = int(float(fav_odds) * 100)
                if 500 <= fav_int < 1000:
                    is_l4_base = True
            elif fav_pay is not None:
                # T-X オッズ無し → race_payouts MIN ベース (過去日フォールバック)
                pay_int = int(fav_pay)
                if 500 <= pay_int < 1000:
                    is_l4_base = True

            tri_hit = is_done and (w1 == 1 and w2 == 2 and w3 == 3)
            tri_pay_v = (tri_pay or 0) if tri_hit else 0
            # L4-prime/12R 観察用 race_number ガード
            try:
                rn = int(race_no) if race_no is not None else 0
            except (TypeError, ValueError):
                rn = 0
            is_prime = rn in (11, 12)
            is_r12 = rn == 12

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
                    if n1 >= 7.0 and b2 >= 40.0:
                        d["gen_f1_tri_bets"] += 1
                        if tri_hit:
                            d["gen_f1_tri_hits"] += 1
                            d["gen_f1_tri_pay"] += tri_pay_v
                continue  # 一般戦は L4 本流集計に含めない

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

        # === l4_daily_summary テーブルから過去データ集計を補完 ===
        # Supabase 容量節約のため、過去 (raw データが無い) の集計は
        # 別途 l4_daily_summary に precompute して入れている。
        # 既に by_date にある日付 (raw データから集計済) は上書きしない。
        try:
            with db_connect() as conn:
                # L4-prime/12R 観察カラムは新カラム。古い DB では COLUMN 存在しないため
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
                has_obs_cols = True
                try:
                    cur = conn.execute(
                        f"SELECT {base_cols}, {obs_cols} FROM l4_daily_summary "
                        f"WHERE date BETWEEN ? AND ?", (from_date, to_date))
                    rows = cur.fetchall()
                except Exception:
                    # ALTER TABLE 未実行の DB → 旧カラムのみ
                    has_obs_cols = False
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    cur = conn.execute(
                        f"SELECT {base_cols} FROM l4_daily_summary "
                        f"WHERE date BETWEEN ? AND ?", (from_date, to_date))
                    rows = cur.fetchall()
                for row in rows:
                    if has_obs_cols:
                        (sdate, n_tot, n_l4,
                         wb, wh, wp, eb, eh, ep, tb, th, tp,
                         c80b, c80h, c80p, prob, proh, prop,
                         sgb, sgh, sgp,
                         gtb, gth, gtp, gptb, gpth, gptp,
                         gfb, gfh, gfp,
                         prb, prh, prp, r12b, r12h, r12p,
                         gr12b, gr12h, gr12p) = row
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
                        # L4-prime / L4-12R / 一般戦×12R 観察
                        "prime_tri_bets": prb or 0, "prime_tri_hits": prh or 0, "prime_tri_pay": prp or 0,
                        "r12_tri_bets": r12b or 0, "r12_tri_hits": r12h or 0, "r12_tri_pay": r12p or 0,
                        "gen_r12_tri_bets": gr12b or 0, "gen_r12_tri_hits": gr12h or 0, "gen_r12_tri_pay": gr12p or 0,
                        "grade_breakdown": {},
                        "_from_summary": True,
                    }
        except Exception as e:
            logger.warning("l4_daily_summary lookup failed: %s", e)

        # ROI 計算 (L4 = A1 のみ。A2 派生は対象外)
        # gen_f1_tri = 一般戦 F1 (採用ベース)
        # gen_tri / gen_plus_tri は観察用 (運用前比較ベンチ)
        for d in by_date.values():
            for bet in ("win", "exa", "tri", "c80", "pro", "sgg12",
                        "gen_tri", "gen_plus_tri", "gen_f1_tri",
                        "prime_tri", "r12_tri", "gen_r12_tri"):
                n = d.get(f"{bet}_bets", 0)
                pay = d.get(f"{bet}_pay", 0)
                d[f"{bet}_roi"] = (pay - 100 * n) / (100 * n) * 100 if n else None
                d[f"{bet}_recovery"] = pay / (100 * n) * 100 if n else None
                d[f"{bet}_profit"] = pay - 100 * n if n else 0

        return sorted(by_date.values(), key=lambda x: x["date"], reverse=True)

    def _l4_races_for_date(target_date: str) -> list[dict]:
        """指定日の L4 該当レース全件を取得 (1号艇A1 + A2派生)。
        買い目ごとの的中/損益も含む。
        判定ソース優先順位:
          1. confirmed - race_payouts MIN (確定後の実際の本命配当)
          2. odds - odds_trifecta の 1-2-3 オッズ × 100 (T-5/T-15 がある場合)
          3. morning - predictions テーブルの prob_first (朝予測のみ、オッズ未取得時)
        """
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
                    pp.min_pay AS fav_pay,
                    oo.min_odds AS fav_odds,
                    pr.prob_first AS prob_first,
                    res1.boat_number AS w1,
                    res2.boat_number AS w2,
                    res3.boat_number AS w3,
                    pw.payout AS win_pay,
                    pe.payout AS exa_pay,
                    pt.payout AS tri_pay,
                    pv.weather_number AS weather
                FROM races r
                LEFT JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
                LEFT JOIN race_previews pv ON pv.race_id = r.race_id AND pv.boat_number = 1
                LEFT JOIN (SELECT race_id, MIN(payout) AS min_pay FROM race_payouts
                           WHERE bet_type='trifecta' GROUP BY race_id) pp ON pp.race_id = r.race_id
                LEFT JOIN (SELECT race_id, MIN(odds) AS min_odds FROM odds_trifecta
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
             natl_1, local_1, fav_pay, fav_odds, prob_first, w1, w2, w3,
             win_pay, exa_pay, tri_pay, weather) = row
            if cls != 1:
                continue  # A1 のみ (A2 派生は対象外)
            if stadium in EXCLUDE_B_VENUES:
                continue
            if grade == 5:
                continue  # 一般戦は回収率が低いため対象外 (147.7%)
            if weather == 3:
                continue  # ☔ 雨は ROI ~ 100% で break-even、ベット対象外
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
            if fav_odds is not None:
                # T-X 1-2-3 オッズ × 100 が 500-1000 → L4 候補 (結果と無関係)
                fav_int = int(float(fav_odds) * 100)
                if 500 <= fav_int < 1000:
                    fav = fav_int
                    fav_source = "odds"
                else:
                    continue
            elif fav_pay is not None:
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
                if n1 >= 7.0 and l1 >= 7.0:
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

    @app.route("/member/strategy")
    @login_required
    @cached(ttl=180, past_ttl=3600)  # 当日3分/期間終端が過去なら1時間
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

        # 通算集計 (L4 = A1 のみ + サブカテゴリ別 ROI)
        totals = {
            "n_total": sum(r["n_total"] for r in rows),
            "n_l4": sum(r["n_l4"] for r in rows),
        }
        bet_keys = ("win", "exa", "tri", "c80", "pro", "sgg12",
                    "gen_tri", "gen_plus_tri", "gen_f1_tri",
                    "prime_tri", "r12_tri", "gen_r12_tri")
        for k in bet_keys:
            totals[f"{k}_bets"] = sum(r.get(f"{k}_bets", 0) for r in rows)
            totals[f"{k}_hits"] = sum(r.get(f"{k}_hits", 0) for r in rows)
            totals[f"{k}_pay"]  = sum(r.get(f"{k}_pay", 0)  for r in rows)
            n = totals[f"{k}_bets"]; pay = totals[f"{k}_pay"]
            totals[f"{k}_roi"] = (pay - 100*n)/(100*n)*100 if n else None
            totals[f"{k}_recovery"] = pay/(100*n)*100 if n else None
            totals[f"{k}_profit"] = pay - 100*n if n else 0

        # ===== 月別 ROI (長期推移、固定範囲 2025-07-01 〜 今日) =====
        # daily 集計の from/to とは独立して、データある全期間を月別集約
        monthly_from = "2025-07-01"
        monthly_to   = today.isoformat()
        try:
            monthly_daily = _l4_daily_stats(monthly_from, monthly_to)
        except Exception as e:
            logger.warning("monthly daily stats failed: %s", e)
            monthly_daily = []

        monthly_map: dict[str, dict] = {}
        for r in monthly_daily:
            ym = r["date"][:7]  # YYYY-MM
            m = monthly_map.setdefault(ym, {
                "ym": ym,
                "n_total": 0, "n_l4": 0,
                **{f"{k}_bets": 0 for k in bet_keys},
                **{f"{k}_hits": 0 for k in bet_keys},
                **{f"{k}_pay":  0 for k in bet_keys},
            })
            m["n_total"] += r.get("n_total", 0) or 0
            m["n_l4"]    += r.get("n_l4", 0) or 0
            for k in bet_keys:
                m[f"{k}_bets"] += r.get(f"{k}_bets", 0) or 0
                m[f"{k}_hits"] += r.get(f"{k}_hits", 0) or 0
                m[f"{k}_pay"]  += r.get(f"{k}_pay", 0)  or 0
        # ROI 計算
        current_ym = today.strftime("%Y-%m")
        for m in monthly_map.values():
            m["is_current"] = (m["ym"] == current_ym)
            for k in bet_keys:
                n = m[f"{k}_bets"]; pay = m[f"{k}_pay"]
                m[f"{k}_roi"] = (pay - 100*n)/(100*n)*100 if n else None
                m[f"{k}_recovery"] = pay/(100*n)*100 if n else None
                m[f"{k}_profit"] = pay - 100*n if n else 0
        monthly_rows = sorted(monthly_map.values(), key=lambda x: x["ym"], reverse=True)

        return render_template(
            "member_strategy.html",
            rows=rows,
            totals=totals,
            monthly_rows=monthly_rows,
            from_date=from_d,
            to_date=to_d,
            today_iso=date.today().isoformat(),
        )

    @app.route("/member/health")
    @login_required
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

    @app.route("/admin/cache-clear", methods=["GET", "POST"])
    @login_required
    def admin_cache_clear():
        """全インメモリキャッシュをクリア (会員限定)。
        データ投入直後など即時反映したい時に使う。"""
        n = len(_CACHE)
        invalidate_cache()
        return jsonify({"cleared": True, "entries_removed": n}), 200

    # ===== Pro 系ビュー (pro_ev / pro_ev_race / pro_ev_api) は廃止 =====
    # backlog item 20: Pro モード廃止。ビュー本体は削除済み。

    return app
