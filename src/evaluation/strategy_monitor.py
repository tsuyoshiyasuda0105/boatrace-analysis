"""Strategy health monitoring helpers."""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from src.db.connection import connect as db_connect
from src.evaluation.l4_strategy import EXCLUDE_VENUES


STRATEGY_DEFINITIONS = {
    "L4_base": {
        "source": "legacy_sql",
        "name": "L4 Base",
        "baseline_recovery": 161.2,
        "description": "A1 1-course core L4 base condition.",
    },
    "L4_plus": {
        "source": "legacy_sql",
        "name": "L4+",
        "baseline_recovery": 188.2,
        "description": "L4 base plus stronger 1-boat national win profile.",
    },
    "L4_plus_plus": {
        "source": "legacy_sql",
        "name": "L4++",
        "baseline_recovery": 190.3,
        "description": "L4+ plus stronger local profile.",
    },
    "L4_a2": {
        "source": "legacy_sql",
        "name": "L4 A2",
        "baseline_recovery": 134.0,
        "description": "A2 1-course L4 branch.",
    },
    "gen_f1_tri": {
        "source": "cache",
        "name": "L4 G++ F1",
        "baseline_recovery": 204.0,
        "description": "General race F1 branch.",
        "bets_key": "gen_f1_tri_bets",
        "hits_key": "gen_f1_tri_hits",
        "pay_key": "gen_f1_tri_pay",
    },
    "gen_200_tri": {
        "source": "cache",
        "name": "General 200",
        "baseline_recovery": 151.9,
        "description": "General race 200 filter.",
        "bets_key": "gen_200_tri_bets",
        "hits_key": "gen_200_tri_hits",
        "pay_key": "gen_200_tri_pay",
    },
    "mid_132_tier_a_tri": {
        "source": "cache",
        "name": "Tier A",
        "baseline_recovery": 293.3,
        "description": "1-3-2 tier A branch.",
        "bets_key": "mid_132_tier_a_tri_bets",
        "hits_key": "mid_132_tier_a_tri_hits",
        "pay_key": "mid_132_tier_a_tri_pay",
    },
    "prime_tri": {
        "source": "cache",
        "name": "L4-prime",
        "baseline_recovery": 185.0,
        "description": "11R-12R focused branch.",
        "bets_key": "prime_tri_bets",
        "hits_key": "prime_tri_hits",
        "pay_key": "prime_tri_pay",
    },
    "r12_tri": {
        "source": "cache",
        "name": "L4-12R",
        "baseline_recovery": 193.0,
        "description": "12R only branch.",
        "bets_key": "r12_tri_bets",
        "hits_key": "r12_tri_hits",
        "pay_key": "r12_tri_pay",
    },
    "gen_r12_tri": {
        "source": "cache",
        "name": "General 12R",
        "baseline_recovery": 189.0,
        "description": "General race 12R branch.",
        "bets_key": "gen_r12_tri_bets",
        "hits_key": "gen_r12_tri_hits",
        "pay_key": "gen_r12_tri_pay",
    },
    "toda_7r_tri": {
        "source": "cache",
        "name": "Toda 7R",
        "baseline_recovery": 171.5,
        "description": "Toda 7R project.",
        "bets_key": "toda_7r_tri_bets",
        "hits_key": "toda_7r_tri_hits",
        "pay_key": "toda_7r_tri_pay",
    },
    "venus_tri": {
        "source": "cache",
        "name": "Venus L4",
        "baseline_recovery": 175.5,
        "description": "Venus series branch.",
        "bets_key": "venus_tri_bets",
        "hits_key": "venus_tri_hits",
        "pay_key": "venus_tri_pay",
    },
    "amagasaki_motor_exa": {
        "source": "cache",
        "name": "Amagasaki 1-4",
        "baseline_recovery": 180.7,
        "description": "Amagasaki motor exacta branch.",
        "bets_key": "amagasaki_motor_exa_bets",
        "hits_key": "amagasaki_motor_exa_hits",
        "pay_key": "amagasaki_motor_exa_pay",
    },
    "ashiya_boat4_exa": {
        "source": "cache",
        "name": "Ashiya 4-1",
        "baseline_recovery": 311.5,
        "description": "Ashiya 4-course exacta branch.",
        "bets_key": "ashiya_boat4_exa_bets",
        "hits_key": "ashiya_boat4_exa_hits",
        "pay_key": "ashiya_boat4_exa_pay",
    },
    "fukuoka_wind_exa": {
        "source": "cache",
        "name": "Fukuoka Wind 2-1",
        "baseline_recovery": 165.7,
        "description": "Fukuoka strong-wind exacta 2-1 branch.",
        "bets_key": "fukuoka_wind_exa_bets",
        "hits_key": "fukuoka_wind_exa_hits",
        "pay_key": "fukuoka_wind_exa_pay",
    },
    "kiryu_win2": {
        "source": "cache",
        "name": "Kiryu Win2",
        "baseline_recovery": None,
        "description": "Kiryu single-win sub-strategy.",
        "bets_key": "kiryu_win2_bets",
        "hits_key": "kiryu_win2_hits",
        "pay_key": "kiryu_win2_pay",
    },
    "karatsu_rain_exa": {
        "source": "cache",
        "name": "Karatsu Rain 1-2",
        "baseline_recovery": 208.1,
        "description": "Karatsu + rain + general + A1 + boat1 motor>=35.",
        "bets_key": "karatsu_rain_exa_bets",
        "hits_key": "karatsu_rain_exa_hits",
        "pay_key": "karatsu_rain_exa_pay",
    },
    "miyajima_boat4_tri": {
        "source": "cache",
        "name": "Miyajima 4-course",
        "baseline_recovery": None,
        "description": "Miyajima 4-course branch.",
        "bets_key": "miyajima_boat4_tri_bets",
        "hits_key": "miyajima_boat4_tri_hits",
        "pay_key": "miyajima_boat4_tri_pay",
    },
}


HEALTH_THRESHOLDS = {
    "rolling_window_days": 30,
    "warning_consecutive_loss_days": 10,
    "critical_consecutive_loss_days": 14,
    "critical_absolute_roi": 80.0,
    "healthy_baseline_ratio": 0.9,
    "watch_baseline_ratio": 0.7,
}


def _build_dense_daily(from_date: str, to_date: str, payload: dict[str, dict]) -> list[dict]:
    fd = datetime.fromisoformat(from_date).date()
    td = datetime.fromisoformat(to_date).date()
    out = []
    cur_d = fd
    while cur_d <= td:
        d_str = cur_d.isoformat()
        d = payload.get(d_str, {"n_bets": 0, "n_hits": 0, "payout_sum": 0})
        n_bets = int(d["n_bets"] or 0)
        n_hits = int(d["n_hits"] or 0)
        payout = int(d["payout_sum"] or 0)
        cost = n_bets * 100
        profit = payout - cost
        roi = (payout / cost * 100) if cost else None
        out.append(
            {
                "date": d_str,
                "n_bets": n_bets,
                "n_hits": n_hits,
                "payout_sum": payout,
                "profit": profit,
                "roi": roi,
            }
        )
        cur_d += timedelta(days=1)
    return out


def _fetch_strategy_daily_legacy(strategy: str, from_date: str, to_date: str) -> list[dict]:
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
        rows = conn.execute(sql, (from_date, to_date)).fetchall()

    daily = defaultdict(lambda: {"n_bets": 0, "n_hits": 0, "payout_sum": 0})
    for row in rows:
        (rdate, stadium, grade, cls, natl_1, local_1, fav_payout, tri_payout, is_hit) = row
        if stadium in EXCLUDE_VENUES:
            continue
        if not _match_strategy(strategy, cls, natl_1, local_1):
            continue
        daily[rdate]["n_bets"] += 1
        if is_hit and tri_payout:
            daily[rdate]["n_hits"] += 1
            daily[rdate]["payout_sum"] += tri_payout
    return _build_dense_daily(from_date, to_date, daily)


def _fetch_strategy_daily_cache(strategy: str, from_date: str, to_date: str) -> list[dict]:
    defn = STRATEGY_DEFINITIONS[strategy]
    with db_connect() as conn:
        rows = conn.execute(
            """
            SELECT race_date, stats_json
            FROM l4_daily_stats_cache
            WHERE race_date >= ? AND race_date <= ?
            ORDER BY race_date
            """,
            (from_date, to_date),
        ).fetchall()

    daily: dict[str, dict] = {}
    for race_date, stats_json in rows:
        try:
            stats = json.loads(stats_json) if stats_json else {}
        except Exception:
            stats = {}
        daily[race_date] = {
            "n_bets": int(stats.get(defn["bets_key"], 0) or 0),
            "n_hits": int(stats.get(defn["hits_key"], 0) or 0),
            "payout_sum": int(stats.get(defn["pay_key"], 0) or 0),
        }
    return _build_dense_daily(from_date, to_date, daily)


def fetch_strategy_daily(strategy: str, from_date: str, to_date: str) -> list[dict]:
    defn = STRATEGY_DEFINITIONS.get(strategy)
    if not defn:
        return []
    if defn.get("source") == "cache":
        return _fetch_strategy_daily_cache(strategy, from_date, to_date)
    return _fetch_strategy_daily_legacy(strategy, from_date, to_date)


def _match_strategy(strategy: str, cls, natl_1, local_1) -> bool:
    try:
        natl = float(natl_1) if natl_1 is not None else 0.0
        local = float(local_1) if local_1 is not None else 0.0
    except (TypeError, ValueError):
        natl = local = 0.0

    if strategy == "L4_base":
        return cls == 1 and not (natl >= 7.0)
    if strategy == "L4_plus":
        return cls == 1 and natl >= 7.0 and local < 7.0
    if strategy == "L4_plus_plus":
        return cls == 1 and natl >= 7.0 and local >= 7.0
    if strategy == "L4_a2":
        return cls == 2
    return False


def compute_rolling_roi(daily: list[dict], window: int) -> Optional[float]:
    if not daily:
        return None
    recent = daily[-window:] if window > 0 else daily
    total_bets = sum(d["n_bets"] for d in recent)
    total_payout = sum(d["payout_sum"] for d in recent)
    if total_bets == 0:
        return None
    return total_payout / (total_bets * 100) * 100


def compute_consecutive_loss_days(daily: list[dict]) -> int:
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
    if n_bets < 10 or baseline_roi <= 0:
        return None
    return (actual_roi - baseline_roi) / (baseline_roi * 0.2 / math.sqrt(n_bets / 30))


def evaluate_health(strategy: str, daily: list[dict]) -> dict:
    defn = STRATEGY_DEFINITIONS.get(strategy)
    if not defn:
        return {"status": "unknown", "status_emoji": "?"}

    baseline = defn["baseline_recovery"]
    roi_7 = compute_rolling_roi(daily, 7)
    roi_14 = compute_rolling_roi(daily, 14)
    roi_30 = compute_rolling_roi(daily, 30)
    roi_all = compute_rolling_roi(daily, 0)
    n_30 = sum(d["n_bets"] for d in daily[-30:])
    cons_loss = compute_consecutive_loss_days(daily)
    z = compute_z_score(roi_30, baseline, n_30) if (roi_30 is not None and baseline) else None
    ratio = roi_30 / baseline if (roi_30 is not None and baseline) else None

    status = "unknown"
    status_emoji = "?"
    reasons = []
    recommendation = ""

    if roi_30 is None or n_30 < 5:
        status = "insufficient"
        status_emoji = "I"
        reasons.append(f"30d sample too small (n={n_30})")
        recommendation = "Keep watching until more samples accumulate."
    elif cons_loss >= HEALTH_THRESHOLDS["critical_consecutive_loss_days"]:
        status = "critical"
        status_emoji = "C"
        reasons.append(f"consecutive loss days {cons_loss}")
        recommendation = "Pause and inspect recent race mix."
    elif roi_30 < HEALTH_THRESHOLDS["critical_absolute_roi"]:
        status = "critical"
        status_emoji = "C"
        reasons.append(f"30d ROI {roi_30:.1f}% < 80%")
        recommendation = "Pause and re-check drift drivers."
    elif cons_loss >= HEALTH_THRESHOLDS["warning_consecutive_loss_days"] or roi_30 < 100 or (
        ratio is not None and ratio < HEALTH_THRESHOLDS["watch_baseline_ratio"]
    ):
        status = "warning"
        status_emoji = "W"
        if cons_loss >= HEALTH_THRESHOLDS["warning_consecutive_loss_days"]:
            reasons.append(f"consecutive loss days {cons_loss}")
        if roi_30 < 100:
            reasons.append(f"30d ROI {roi_30:.1f}% < 100%")
        if ratio is not None and ratio < HEALTH_THRESHOLDS["watch_baseline_ratio"]:
            reasons.append(f"30d ROI {roi_30:.1f}% < 70% of baseline")
        recommendation = "Reduce confidence and inspect current filters."
    elif ratio is not None and ratio < HEALTH_THRESHOLDS["healthy_baseline_ratio"]:
        status = "watch"
        status_emoji = "V"
        reasons.append(f"30d ROI {roi_30:.1f}% below healthy baseline band")
        recommendation = "Watch a bit longer before changing the strategy."
    else:
        status = "healthy"
        status_emoji = "H"
        if ratio is not None:
            reasons.append(f"30d ROI {roi_30:.1f}% >= 90% of baseline")
        else:
            reasons.append(f"30d ROI {roi_30:.1f}%")
        recommendation = "Healthy. Keep running."

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


def evaluate_all_strategies(from_date: str, to_date: str) -> list[dict]:
    results = []
    for strategy in STRATEGY_DEFINITIONS:
        daily = fetch_strategy_daily(strategy, from_date, to_date)
        health = evaluate_health(strategy, daily)
        health["daily"] = daily
        results.append(health)
    return results


def print_health_summary(results: list[dict]):
    print(f"{'Strategy':<20} {'Status':<6} {'Baseline':>9} {'30dROI':>9} {'Ratio':>6} {'Loss':>5} {'n30':>6}")
    print("-" * 80)
    for r in results:
        baseline = r["baseline_recovery"]
        roi30 = r["roi_30d"]
        ratio = r["baseline_ratio"]
        roi30_str = f"{roi30:.1f}%" if roi30 is not None else "-"
        ratio_str = f"{ratio * 100:.0f}%" if ratio is not None else "-"
        baseline_str = f"{baseline:.1f}%" if baseline is not None else "-"
        print(
            f"{r['name']:<18} {r['status_emoji']} {r['status']:<6} "
            f"{baseline_str:>9} {roi30_str:>9} {ratio_str:>6} "
            f"{r['consecutive_loss_days']:>5} {r['n_bets_30d']:>6}"
        )
        for reason in r["reasons"]:
            print(f"  - {reason}")
        if r["recommendation"]:
            print(f"  * {r['recommendation']}")
