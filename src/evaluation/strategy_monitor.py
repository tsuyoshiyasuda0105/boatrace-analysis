"""戦略のリアルタイム性能監視 (drift detection)

各戦略 (L4 / L4+ / L4++ / A2派生 / ダイヤモンド) について:
  1. 日次 ROI を集計
  2. 直近 7/14/30 日のローリング指標
  3. 検証ベースライン (10ヶ月実測) からの乖離 (Z-score)
  4. 連続赤字日数
  5. 健全度ステータス (healthy / watch / warning / critical)
  6. "やめる判断" 推奨フラグ

判定基準 (戦略を停止すべきか):
  🟢 healthy : 30日 ROI ≥ baseline × 0.9
  🟡 watch   : 30日 ROI ≥ baseline × 0.7
  🟠 warning : 30日 ROI < baseline × 0.7 or 連続赤字 10日
  🔴 critical: 30日 ROI < 80%        or 連続赤字 14日 → 停止推奨

このモジュールは pure Python のみ (numpy 不要)。
"""
from __future__ import annotations

import math
import random
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Optional

from src.db.connection import connect as db_connect
from src.evaluation.l4_strategy import (
    EXCLUDE_VENUES,
    GRADE_CLASS_RULES,
    L4_DEFAULT_A1,
    RANK_PLUS_PLUS_RECOVERY,
    RANK_PLUS_RECOVERY,
    l4_rank,
    lookup_rule,
)

# ============================================================
# 戦略定義: それぞれの「検証ベースライン回収率」と「フィルター関数」
# ============================================================
STRATEGY_DEFINITIONS = {
    "L4_base": {
        "name": "L4 基本 (1号艇A1)",
        "baseline_recovery": 161.2,
        "description": "本命500-1000円 + B除外 + 1号艇A1 + 3連単1-2-3",
    },
    "L4_plus": {
        "name": "L4+ (国級)",
        "baseline_recovery": 188.2,
        "description": "L4 基本 + 1号艇国1% >= 7.0",
    },
    "L4_plus_plus": {
        "name": "L4++ (国×局)",
        "baseline_recovery": 190.3,
        "description": "L4 基本 + 1号艇国1% >= 7.0 ∧ 局1% >= 7.0",
    },
    "L4_a2": {
        "name": "L4 派生 A2",
        "baseline_recovery": 134.0,
        "description": "本命500-1000円 + B除外 + 1号艇A2 + 3連単1-2-3",
    },
}


# ============================================================
# 健全度判定の閾値
# ============================================================
HEALTH_THRESHOLDS = {
    "rolling_window_days": 30,
    "warning_consecutive_loss_days": 10,
    "critical_consecutive_loss_days": 14,
    "critical_absolute_roi": 80.0,      # 30日ROIがこの値未満で critical
    "healthy_baseline_ratio": 0.9,       # baseline × 0.9 以上で healthy
    "watch_baseline_ratio": 0.7,         # baseline × 0.7 以上で watch
}


# ============================================================
# データ集計
# ============================================================
def fetch_strategy_daily(strategy: str, from_date: str, to_date: str) -> list[dict]:
    """指定戦略の日次データを SQL で集計して返す。
    Returns: [{date, n_bets, n_hits, payout_sum, profit, roi}]
    """
    with db_connect() as conn:
        sql = """
            SELECT r.race_date,
                   r.stadium_number,
                   r.race_grade_number AS grade,
                   e.class_number AS cls,
                   e.national_top_1_percent AS natl_1,
                   e.local_top_1_percent AS local_1,
                   pp.payout AS fav_payout,
                   COALESCE(rp.payout, 0) AS tri_payout,
                   CASE WHEN rr1.boat_number = 1 AND rr2.boat_number = 2 AND rr3.boat_number = 3
                        THEN 1 ELSE 0 END AS is_hit
            FROM races r
            LEFT JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
            LEFT JOIN (
                SELECT race_id, MIN(payout) AS payout FROM race_payouts
                WHERE bet_type='trifecta' GROUP BY race_id
            ) pp ON r.race_id = pp.race_id
            LEFT JOIN race_payouts rp ON r.race_id = rp.race_id
                AND rp.bet_type='trifecta' AND rp.combination='1-2-3'
            LEFT JOIN race_results rr1 ON r.race_id = rr1.race_id AND rr1.finishing_position = 1
            LEFT JOIN race_results rr2 ON r.race_id = rr2.race_id AND rr2.finishing_position = 2
            LEFT JOIN race_results rr3 ON r.race_id = rr3.race_id AND rr3.finishing_position = 3
            WHERE r.race_date >= ? AND r.race_date <= ?
              AND pp.payout IS NOT NULL
              AND pp.payout >= 500 AND pp.payout < 1000
        """
        cur = conn.execute(sql, (from_date, to_date))
        rows = cur.fetchall()

    # 戦略フィルタ適用 + 日別集計
    daily = defaultdict(lambda: {"n_bets": 0, "n_hits": 0, "payout_sum": 0})
    for row in rows:
        (rdate, stadium, grade, cls, natl_1, local_1,
         fav_payout, tri_payout, is_hit) = row
        if stadium in EXCLUDE_VENUES:
            continue
        # 戦略別の追加フィルタ
        if not _match_strategy(strategy, cls, natl_1, local_1):
            continue
        daily[rdate]["n_bets"] += 1
        if is_hit and tri_payout:
            daily[rdate]["n_hits"] += 1
            daily[rdate]["payout_sum"] += tri_payout

    # date を補完してリスト化 (歯抜けの日は n=0 行を作る)
    fd = datetime.fromisoformat(from_date).date()
    td = datetime.fromisoformat(to_date).date()
    out = []
    cur_d = fd
    while cur_d <= td:
        d_str = cur_d.isoformat()
        d = daily.get(d_str, {"n_bets": 0, "n_hits": 0, "payout_sum": 0})
        n_bets = d["n_bets"]
        n_hits = d["n_hits"]
        payout = d["payout_sum"]
        cost = n_bets * 100
        profit = payout - cost
        roi = (payout / cost * 100) if cost else None
        out.append({
            "date": d_str,
            "n_bets": n_bets,
            "n_hits": n_hits,
            "payout_sum": payout,
            "profit": profit,
            "roi": roi,
        })
        cur_d += timedelta(days=1)
    return out


def _match_strategy(strategy: str, cls, natl_1, local_1) -> bool:
    """1号艇クラスと選手成績で戦略マッチを判定"""
    try:
        n = float(natl_1) if natl_1 is not None else 0.0
        l = float(local_1) if local_1 is not None else 0.0
    except (TypeError, ValueError):
        n = l = 0.0

    if strategy == "L4_base":
        # L4 基本 (1号艇A1) のうち plus/plus_plus でないもの = base のみ
        return cls == 1 and not (n >= 7.0)
    if strategy == "L4_plus":
        # plus のみ (plus_plus を除く)
        return cls == 1 and n >= 7.0 and l < 7.0
    if strategy == "L4_plus_plus":
        return cls == 1 and n >= 7.0 and l >= 7.0
    if strategy == "L4_a2":
        return cls == 2
    return False


# ============================================================
# ローリング指標 + 連続赤字
# ============================================================
def compute_rolling_roi(daily: list[dict], window: int) -> Optional[float]:
    """直近 N 日 (n_bets>0 の日のみ) の累積 ROI を返す"""
    if not daily:
        return None
    recent = daily[-window:] if window > 0 else daily
    total_bets = sum(d["n_bets"] for d in recent)
    total_payout = sum(d["payout_sum"] for d in recent)
    if total_bets == 0:
        return None
    return total_payout / (total_bets * 100) * 100


def compute_consecutive_loss_days(daily: list[dict]) -> int:
    """末尾から逆順に連続赤字 (profit < 0) 日数を返す。n=0 の日はスキップ。"""
    count = 0
    for d in reversed(daily):
        if d["n_bets"] == 0:
            continue
        if d["profit"] < 0:
            count += 1
        else:
            break
    return count


def compute_z_score(actual_roi: float, baseline_roi: float, n_bets: int) -> Optional[float]:
    """ベースラインからの z-score (簡易版: ベルヌーイ分散仮定)"""
    if n_bets < 10:
        return None
    # ROI を 1点100円賭けた時の payout 率に変換
    p = baseline_roi / 100  # 期待値倍率
    # 1ベット当たりの payout の標準偏差 (実測ボラを使うのが厳密だが、
    # 簡易にベルヌーイ近似: hit_rate × variance(平均的な payout))
    # ここでは ROI 自体の標準誤差を payout の分散の二乗平均近似で推定
    # シンプルに ROI 差を baseline の20% 単位で評価
    if baseline_roi <= 0:
        return None
    return (actual_roi - baseline_roi) / (baseline_roi * 0.2 / math.sqrt(n_bets / 30))


# ============================================================
# 健全度ステータス判定
# ============================================================
def evaluate_health(strategy: str, daily: list[dict]) -> dict:
    """戦略の健全度を評価。Returns: {status, status_emoji, ...}"""
    defn = STRATEGY_DEFINITIONS.get(strategy)
    if not defn:
        return {"status": "unknown", "status_emoji": "❓"}

    baseline = defn["baseline_recovery"]

    # 各 window の累積 ROI
    roi_7 = compute_rolling_roi(daily, 7)
    roi_14 = compute_rolling_roi(daily, 14)
    roi_30 = compute_rolling_roi(daily, 30)
    roi_all = compute_rolling_roi(daily, 0)

    # 直近30日のサンプル数
    n_30 = sum(d["n_bets"] for d in daily[-30:])

    # 連続赤字
    cons_loss = compute_consecutive_loss_days(daily)

    # Z-score (baseline からの乖離)
    z = compute_z_score(roi_30, baseline, n_30) if roi_30 is not None else None

    # ベースライン比
    ratio = roi_30 / baseline if (roi_30 is not None and baseline > 0) else None

    # 判定
    th = HEALTH_THRESHOLDS
    status = "unknown"
    status_emoji = "❓"
    reasons = []
    recommendation = ""

    if roi_30 is None or n_30 < 5:
        status = "insufficient"
        status_emoji = "⚪"
        reasons.append(f"直近30日のサンプル不足 (n={n_30})")
        recommendation = "もう少しデータが溜まるまで判定不可"
    else:
        # critical
        if cons_loss >= th["critical_consecutive_loss_days"]:
            status = "critical"
            status_emoji = "🔴"
            reasons.append(f"連続赤字 {cons_loss} 日 ≥ 14")
            recommendation = "停止推奨。市場構造の変化を疑う。"
        elif roi_30 < th["critical_absolute_roi"]:
            status = "critical"
            status_emoji = "🔴"
            reasons.append(f"30日 ROI {roi_30:.1f}% < 80%")
            recommendation = "停止推奨。回収率が大幅に低下。"
        # warning
        elif (cons_loss >= th["warning_consecutive_loss_days"]
              or (ratio is not None and ratio < th["watch_baseline_ratio"])):
            status = "warning"
            status_emoji = "🟠"
            if cons_loss >= th["warning_consecutive_loss_days"]:
                reasons.append(f"連続赤字 {cons_loss} 日 ≥ 10")
            if ratio is not None and ratio < th["watch_baseline_ratio"]:
                reasons.append(f"30日 ROI {roi_30:.1f}% < baseline × 0.7 = {baseline*0.7:.1f}%")
            recommendation = "監視継続。資金縮小を検討。"
        # watch
        elif ratio is not None and ratio < th["healthy_baseline_ratio"]:
            status = "watch"
            status_emoji = "🟡"
            reasons.append(f"30日 ROI {roi_30:.1f}% < baseline × 0.9 = {baseline*0.9:.1f}%")
            recommendation = "通常運用継続、要観察。"
        # healthy
        else:
            status = "healthy"
            status_emoji = "🟢"
            reasons.append(f"30日 ROI {roi_30:.1f}% ≥ baseline × 0.9")
            recommendation = "順調。継続。"

    return {
        "strategy": strategy,
        "name": defn["name"],
        "description": defn["description"],
        "baseline_recovery": baseline,
        "status": status,
        "status_emoji": status_emoji,
        "reasons": reasons,
        "recommendation": recommendation,
        "roi_7d": roi_7,
        "roi_14d": roi_14,
        "roi_30d": roi_30,
        "roi_all": roi_all,
        "n_bets_30d": n_30,
        "consecutive_loss_days": cons_loss,
        "z_score": z,
        "baseline_ratio": ratio,
    }


# ============================================================
# 全戦略一括評価
# ============================================================
def evaluate_all_strategies(from_date: str, to_date: str) -> list[dict]:
    """全戦略を評価して結果リストを返す"""
    results = []
    for strategy in STRATEGY_DEFINITIONS:
        daily = fetch_strategy_daily(strategy, from_date, to_date)
        health = evaluate_health(strategy, daily)
        health["daily"] = daily  # ダッシュボード用にチャート化できる
        results.append(health)
    return results


# ============================================================
# CLI 用ヘルパー (print)
# ============================================================
def print_health_summary(results: list[dict]):
    """各戦略の健全度サマリをコンソール出力"""
    print(f"{'戦略':<20} {'状態':<6} {'baseline':>9} {'30日ROI':>9} {'比率':>6} "
          f"{'連敗':>5} {'n_30':>6}")
    print("-" * 80)
    for r in results:
        bl = r["baseline_recovery"]
        roi30 = r["roi_30d"]
        ratio = r["baseline_ratio"]
        roi30_str = f"{roi30:.1f}%" if roi30 is not None else "-"
        ratio_str = f"{ratio*100:.0f}%" if ratio is not None else "-"
        print(f"{r['name']:<18} {r['status_emoji']} {r['status']:<6} "
              f"{bl:>7.1f}% {roi30_str:>9} {ratio_str:>6} "
              f"{r['consecutive_loss_days']:>5} {r['n_bets_30d']:>6}")
        for reason in r["reasons"]:
            print(f"  └ {reason}")
        if r["recommendation"]:
            print(f"  → {r['recommendation']}")
