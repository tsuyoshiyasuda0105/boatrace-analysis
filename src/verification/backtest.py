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
import sqlite3
from typing import Any

import config


def _conn():
    return sqlite3.connect(config.DB_PATH)


def _build_where(cond: dict) -> tuple[str, list[Any]]:
    """conditions dict から WHERE 句と引数リストを構築。
    無視する条件 (まだ実装していないもの) は build_where_unsupported に記録。
    """
    clauses: list[str] = []
    args: list[Any] = []

    if cond.get("stadium"):
        ph = ",".join("?" * len(cond["stadium"]))
        clauses.append(f"r.stadium_number IN ({ph})")
        args.extend(cond["stadium"])

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

    if cond.get("weather_exclude"):
        ph = ",".join("?" * len(cond["weather_exclude"]))
        clauses.append(f"(pv.weather_number IS NULL OR pv.weather_number NOT IN ({ph}))")
        args.extend(cond["weather_exclude"])

    if cond.get("wind_speed_min") is not None:
        clauses.append("(pv.wind_speed IS NULL OR pv.wind_speed >= ?)")
        args.append(float(cond["wind_speed_min"]))

    return (" AND ".join(clauses) if clauses else "1=1"), args


def unsupported_conditions(cond: dict) -> list[str]:
    """現在 SQL に落とせない条件 (= バックテスト不完全) を返す。"""
    unsupp: list[str] = []
    if cond.get("wind_direction"):
        # 風向は会場ごとに「追い風」が何向なのか異なる → ヒューリスティック必要
        unsupp.append("wind_direction (要・会場別追い風方向マッピング)")
    if cond.get("course"):
        # course は race_results.course_number で判定可だがベットの「頭」と
        # 関連付ける必要があり、bet_pattern に依存 → 今後対応
        unsupp.append("course (要・bet_pattern との整合)")
    if cond.get("finish_pattern") in ("makuri", "head_fix"):
        unsupp.append(f"finish_pattern={cond['finish_pattern']} (要・決まり手 or 着順条件化)")
    if cond.get("odds_min") is not None or cond.get("odds_max") is not None:
        # 1-2-3 オッズ範囲はバックテスト時に T-5min 等で参照可
        unsupp.append("odds range (要・odds_trifecta join)")
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
    where, args = _build_where(cond)

    bet_type = cond.get("bet_type", "trifecta")
    finish_pat = cond.get("finish_pattern")
    # 着順パターン文字列 "1-2-3" 等が finish_pat にあればそのまま combination に使う
    if finish_pat and "-" in finish_pat:
        bet_combo = finish_pat
    else:
        # デフォルト: 3連単 1-2-3 (L4 戦略系のデフォルト)
        bet_combo = "1-2-3"

    # 対象レース総数 (1号艇 entry + boat1 preview を LEFT JOIN)
    sql_total = f"""
        SELECT COUNT(DISTINCT r.race_id)
          FROM races r
          LEFT JOIN race_entries e1 ON e1.race_id=r.race_id AND e1.boat_number=1
          LEFT JOIN race_previews pv ON pv.race_id=r.race_id AND pv.boat_number=1
         WHERE {where}
    """
    sql_hits = f"""
        SELECT COUNT(*) , COALESCE(SUM(pp.payout), 0)
          FROM races r
          LEFT JOIN race_entries e1 ON e1.race_id=r.race_id AND e1.boat_number=1
          LEFT JOIN race_previews pv ON pv.race_id=r.race_id AND pv.boat_number=1
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
