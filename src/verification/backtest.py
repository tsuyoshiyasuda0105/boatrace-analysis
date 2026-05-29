"""候補手法をローカル DB で検証 (バックテスト) する。

method dict を受け取り、conditions を SQL に変換、
過去全期間の対象レースで「ベット型」を打った場合の
n_races / 勝率 / 平均配当 / 合計払戻 / ROI / 損益 / Tier を計算する。

Tier 判定 (保守的):
  tier_1 : ROI ≥ 150% かつ n ≥ 100  (採用候補 — 強い優位性)
  tier_2 : ROI 120-150% かつ n ≥ 50  (観察 — 標本追加で本採用検討)
  tier_3 : ROI 100-120% かつ n ≥ 30  (参考 — 控除負け回避水準)
  discard: ROI < 100% (期待値マイナス)
  insufficient_sample : n < 30 (信頼区間が広すぎる)
"""
from __future__ import annotations

import math
import os
import sqlite3
from typing import Any

import config


def _conn():
    """DATABASE_URL があれば Supabase に接続、無ければローカル SQLite。
    odds_trifecta は Supabase にしか書かれていないため、本当の検証は
    Supabase 接続必須。"""
    if os.getenv("DATABASE_URL", "").strip():
        try:
            from src.db.connection import connect as db_connect
            return db_connect()
        except Exception:  # noqa: BLE001
            pass
    return sqlite3.connect(config.DB_PATH)


def _build_where(cond: dict) -> tuple[str, list[Any], list[str]]:
    """conditions dict から WHERE 句、引数リスト、追加 JOIN 句を構築。"""
    clauses: list[str] = []
    args: list[Any] = []
    extra_joins: list[str] = []

    if cond.get("stadium"):
        ph = ",".join("?" * len(cond["stadium"]))
        clauses.append(f"r.stadium_number IN ({ph})")
        args.extend(cond["stadium"])

    if cond.get("exclude_b_venues"):
        # extract.EXCLUDE_B を import すると循環するので明示的に書く
        excl = [2, 4, 7, 8, 10, 19, 21, 24]
        ph = ",".join("?" * len(excl))
        clauses.append(f"r.stadium_number NOT IN ({ph})")
        args.extend(excl)

    if cond.get("race_number"):
        ph = ",".join("?" * len(cond["race_number"]))
        clauses.append(f"r.race_number IN ({ph})")
        args.extend(cond["race_number"])

    if cond.get("racer_class"):
        ph = ",".join("?" * len(cond["racer_class"]))
        clauses.append(f"e1.class_number IN ({ph})")
        args.extend(cond["racer_class"])

    if cond.get("racer_avg_st_max") is not None:
        clauses.append("e1.avg_start_timing <= ?")
        args.append(float(cond["racer_avg_st_max"]))

    if cond.get("boat1_natl_1_min") is not None:
        clauses.append("e1.national_top_1_percent >= ?")
        args.append(float(cond["boat1_natl_1_min"]))

    if cond.get("boat1_local_1_min") is not None:
        clauses.append("e1.local_top_1_percent >= ?")
        args.append(float(cond["boat1_local_1_min"]))

    if cond.get("boat1_local_2_min") is not None:
        clauses.append("e1.local_top_2_percent >= ?")
        args.append(float(cond["boat1_local_2_min"]))

    if cond.get("boat1_motor_top2_min") is not None:
        clauses.append("e1.assigned_motor_top_2_percent >= ?")
        args.append(float(cond["boat1_motor_top2_min"]))

    if cond.get("boat1_motor_top3_min") is not None:
        clauses.append("e1.assigned_motor_top_3_percent >= ?")
        args.append(float(cond["boat1_motor_top3_min"]))

    if cond.get("boat2_top2_min") is not None:
        extra_joins.append(
            "LEFT JOIN race_entries e2 ON e2.race_id=r.race_id AND e2.boat_number=2")
        clauses.append("e2.national_top_2_percent >= ?")
        args.append(float(cond["boat2_top2_min"]))

    if cond.get("boat3_natl_1_min") is not None:
        extra_joins.append(
            "LEFT JOIN race_entries e3 ON e3.race_id=r.race_id AND e3.boat_number=3")
        clauses.append("e3.national_top_1_percent >= ?")
        args.append(float(cond["boat3_natl_1_min"]))

    if cond.get("weather_exclude"):
        ph = ",".join("?" * len(cond["weather_exclude"]))
        clauses.append(f"(pv.weather_number IS NULL OR pv.weather_number NOT IN ({ph}))")
        args.extend(cond["weather_exclude"])

    if cond.get("wind_speed_min") is not None:
        clauses.append("(pv.wind_speed IS NULL OR pv.wind_speed >= ?)")
        args.append(float(cond["wind_speed_min"]))

    # オッズ帯 (1-2-3 のいずれかのスナップが帯に入ったか)
    if cond.get("odds_min") is not None or cond.get("odds_max") is not None:
        omin = float(cond.get("odds_min") or 0)
        omax = float(cond.get("odds_max") or 99999)
        clauses.append(
            "EXISTS (SELECT 1 FROM odds_trifecta o WHERE o.race_id=r.race_id "
            "AND o.combination='1-2-3' AND o.odds >= ? AND o.odds < ? "
            "AND o.snapshot_label IN ('T-5min','T-4min','T-3min','T-2min','T-1min','final'))"
        )
        args.append(omin)
        args.append(omax)

    # 決まり手 (1着の決まり手で絞り込み)
    if cond.get("kimarite"):
        clauses.append(
            "EXISTS (SELECT 1 FROM race_results rr WHERE rr.race_id=r.race_id "
            "AND rr.finishing_position=1 AND rr.kimarite=?)"
        )
        args.append(cond["kimarite"])

    return (" AND ".join(clauses) if clauses else "1=1"), args, extra_joins


def unsupported_conditions(cond: dict) -> list[str]:
    """現在 SQL に落とせない条件 (= バックテスト不完全) を返す。"""
    unsupp: list[str] = []
    if cond.get("wind_direction"):
        unsupp.append("wind_direction (要・会場別追い風方向マッピング)")
    if cond.get("course"):
        unsupp.append("course (要・進入コースとbetパターン整合)")
    if cond.get("finish_pattern") in ("head_fix",):
        unsupp.append(f"finish_pattern={cond['finish_pattern']} (頭固定は bet_combo 化必要)")
    return unsupp


def _tier(roi: float, n: int) -> str:
    if n < 30:
        return "insufficient_sample"
    if roi >= 150 and n >= 100:
        return "tier_1"
    if roi >= 120 and n >= 50:
        return "tier_2"
    if roi >= 100 and n >= 30:
        return "tier_3"
    return "discard"


def backtest_method(method: dict, max_races: int = 500_000) -> dict:
    """method 1 件を DB で検証して結果 dict を返す。"""
    cond = method.get("conditions", {})
    where, args, extra_joins = _build_where(cond)
    joins_str = "\n          ".join(extra_joins)

    bet_type = cond.get("bet_type", "trifecta")
    finish_pat = cond.get("finish_pattern")
    if finish_pat and "-" in finish_pat:
        bet_combo = finish_pat
    else:
        bet_combo = "1-2-3"

    sql_total = f"""
        SELECT COUNT(DISTINCT r.race_id)
          FROM races r
          LEFT JOIN race_entries e1 ON e1.race_id=r.race_id AND e1.boat_number=1
          LEFT JOIN race_previews pv ON pv.race_id=r.race_id AND pv.boat_number=1
          {joins_str}
         WHERE {where}
    """
    sql_hits = f"""
        SELECT COUNT(*) , COALESCE(SUM(pp.payout), 0)
          FROM races r
          LEFT JOIN race_entries e1 ON e1.race_id=r.race_id AND e1.boat_number=1
          LEFT JOIN race_previews pv ON pv.race_id=r.race_id AND pv.boat_number=1
          {joins_str}
          JOIN race_payouts pp ON pp.race_id=r.race_id
                              AND pp.bet_type=? AND pp.combination=?
         WHERE {where}
    """

    conn = _conn()
    try:
        n_total = conn.execute(sql_total, args).fetchone()[0] or 0
        if n_total == 0:
            return {
                "n_races": 0,
                "tier": "insufficient_sample",
                "error": "条件にマッチするレースが 0 件",
                "bet_type": bet_type,
                "bet_combo": bet_combo,
                "unsupported": unsupported_conditions(cond),
            }
        if n_total > max_races:
            # 過剰な広範囲条件 (= 条件絞り込み弱) は警告
            pass
        row = conn.execute(sql_hits, [bet_type, bet_combo] + args).fetchone()
        n_hits = row[0] or 0
        sum_payout = int(row[1] or 0)
    finally:
        conn.close()

    hit_rate = (n_hits / n_total * 100) if n_total else 0.0
    roi = (sum_payout / (100 * n_total) * 100) if n_total else 0.0
    profit = sum_payout - 100 * n_total
    avg_pay = (sum_payout / n_hits) if n_hits else 0.0
    # 簡易 95% Wilson 区間 (勝率の標準誤差ベース、ROI 区間ではない近似)
    se_pct = math.sqrt(max(hit_rate * (100 - hit_rate), 0) / max(n_total, 1))
    ci_low = max(0.0, hit_rate - 1.96 * se_pct)
    ci_high = min(100.0, hit_rate + 1.96 * se_pct)

    return {
        "n_races": n_total,
        "n_hits": n_hits,
        "hit_rate": hit_rate,
        "hit_rate_ci_low": ci_low,
        "hit_rate_ci_high": ci_high,
        "sum_payout": sum_payout,
        "avg_payout_on_hit": avg_pay,
        "roi": roi,
        "profit": profit,
        "bet_type": bet_type,
        "bet_combo": bet_combo,
        "tier": _tier(roi, n_total),
        "unsupported": unsupported_conditions(cond),
    }
