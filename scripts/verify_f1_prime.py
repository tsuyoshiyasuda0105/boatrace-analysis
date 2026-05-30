"""F1 Prime 11-12R 条件の ROI 実測検証 (オッズ帯絞り無し版).

【検証条件】
  L4 universe (1号艇A1 + B除外 + 男性のみ + 雨除外) — オッズ帯 500-1000 は除く
  AND 一般戦 (race_grade_number=5)
  AND 1号艇 全国1着率 >= 7
  AND 2号艇 モーター2連率 >= 40
  AND race_number IN (11, 12)
  → 3連単 1-2-3 を 100円 ベット

【注意】
  ローカル odds_trifecta は 2026-05-11〜13 の 3 日分しか無いため、
  「L4 帯 (T-5min オッズ 5-10 倍 = payout 500-1000円)」の厳密フィルタは不可.
  本検証はオッズ帯絞り **無し** で実施.
  → 既存 F1 単体 ROI 204% (L4 帯限定) と直接比較できない点に注意.
  → ただし F1 + 11/12R の universe 全体での ROI は計算可能.

【出力】
  - 全期間 ROI / 年別 / 月別
  - F1 単体 (Prime 制約なし) との対比
  - 時系列スプリット (train < 2026-01-01 / test >= 2026-01-01)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.verification.backtest import _conn

# L4 universe 定数
EXCLUDE_B_VENUES = {2, 4, 7, 8, 10, 19, 21, 24}
SPLIT = "2026-01-01"
PH = "%s" if os.environ.get("DATABASE_URL") else "?"


def run(label, where_extra, args_extra=()):
    """指定条件下での 3連単 1-2-3 ROI 集計を返す.

    母数 = 条件マッチ race 全部 (オッズ帯絞り無し).
    分子 = そのうち 3連単 1-2-3 結着 race の payout 合計.
    """
    excl_ph = ",".join([PH] * len(EXCLUDE_B_VENUES))
    where = f"""
        e1.boat_number = 1
        AND e1.class_number = 1
        AND r.stadium_number NOT IN ({excl_ph})
        AND r.race_grade_number = 5
        AND (pv.weather_number IS NULL OR pv.weather_number != 3)
        AND NOT EXISTS (
            SELECT 1 FROM race_entries e2
            JOIN racers ra ON e2.racer_number = ra.racer_number
            WHERE e2.race_id = r.race_id AND ra.gender = 2
        )
    """
    if where_extra:
        where += " AND " + where_extra

    sql = f"""
        SELECT COUNT(DISTINCT r.race_id) AS n_races,
               COUNT(DISTINCT CASE WHEN pp.payout IS NOT NULL THEN r.race_id END) AS n_hits,
               COALESCE(SUM(pp.payout), 0) AS total_pay
        FROM races r
        JOIN race_entries e1 ON e1.race_id = r.race_id AND e1.boat_number = 1
        LEFT JOIN race_previews pv ON pv.race_id = r.race_id AND pv.boat_number = 1
        LEFT JOIN race_payouts pp ON pp.race_id = r.race_id
            AND pp.bet_type = 'trifecta'
            AND pp.combination = '1-2-3'
        WHERE {where}
    """

    cur = conn.cursor()
    cur.execute(sql, list(EXCLUDE_B_VENUES) + list(args_extra))
    n, hits, pay = cur.fetchone()
    n = n or 0
    hits = hits or 0
    pay = int(pay or 0)
    bets = n * 100  # 1race=100円
    hit_rate = round(100 * hits / max(1, n), 2) if n else 0
    roi = round(100 * pay / max(1, bets), 2) if bets else 0
    avg_payout = round(pay / max(1, hits), 0) if hits else 0
    profit = pay - bets
    return {
        "label": label, "n": n, "hits": hits, "hit_rate": hit_rate,
        "pay": pay, "avg_payout": avg_payout,
        "roi": roi, "profit": profit,
    }


def emit(r, prefix="  "):
    print(
        f"{prefix}n={r['n']:>5} hits={r['hits']:>4} ({r['hit_rate']:>5.1f}%) "
        f"avg_pay={r['avg_payout']:>5,.0f}円 "
        f"ROI={r['roi']:>6.1f}% profit={r['profit']:+,}"
    )


def main():
    global conn
    conn = _conn()
    print("=" * 80)
    print("F1 Prime 11-12R ROI 検証 (オッズ帯絞り無し版)")
    print("=" * 80)
    print(f"スプリット日: {SPLIT}")
    print(f"母数: L4 universe (1号艇A1+B除外+男性のみ+雨除外+一般戦)")
    print(f"     + F1 (国1≥7 AND 2号motor≥40)")
    print(f"買い目: 3連単 1-2-3 (100円)")
    print()

    # F1 base (Prime 制約なし)
    f1_base = """
        e1.national_top_1_percent >= 7
        AND EXISTS (
            SELECT 1 FROM race_entries e2x
            WHERE e2x.race_id = r.race_id AND e2x.boat_number = 2
              AND e2x.assigned_motor_top_2_percent >= 40
        )
    """

    # === Section A: F1 単体 (全レース番号) ===
    print("--- Section A: F1 単体 (全レース番号) ---")
    r = run("F1 全期間", f1_base)
    emit(r)
    print()

    # === Section B: F1 Prime (11-12R) ===
    print("--- Section B: F1 Prime (= F1 + 11/12R) ---")
    f1_prime = f1_base + " AND r.race_number IN (11, 12)"
    r_all = run("F1 Prime 全期間", f1_prime)
    emit(r_all, "  全期間: ")
    r_tr = run("F1 Prime train", f1_prime + f" AND r.race_date < {PH}", (SPLIT,))
    r_te = run("F1 Prime test",  f1_prime + f" AND r.race_date >= {PH}", (SPLIT,))
    emit(r_tr, f"  train(〜{SPLIT}): ")
    emit(r_te, f"  test ({SPLIT}〜): ")
    icon = "🏆" if (r_tr["roi"] >= 130 and r_te["roi"] >= 130 and r_tr["n"] >= 30 and r_te["n"] >= 30) else (
        "⚠" if (r_tr["roi"] >= 100 and r_te["roi"] >= 100) else "❌")
    print(f"  時系列 robust 判定: [{icon}]")
    print()

    # === Section C: F1 単体 vs F1 Prime ===
    print("--- Section C: F1 単体 vs F1 Prime (同基準) ---")
    delta_roi = r_all["roi"] - r["roi"]
    delta_hit = r_all["hit_rate"] - r["hit_rate"]
    print(f"  F1 単体: n={r['n']:>4}, hit_rate={r['hit_rate']:>5.1f}%, ROI={r['roi']:>6.1f}%")
    print(f"  F1 Prime: n={r_all['n']:>4}, hit_rate={r_all['hit_rate']:>5.1f}%, ROI={r_all['roi']:>6.1f}%")
    print(f"  差分: ROI {delta_roi:+.1f}pt, hit_rate {delta_hit:+.1f}pt")
    if delta_roi > 10:
        print("  → F1 Prime の方が +10pt 以上高い → 専用バッジ採用候補")
    elif delta_roi < -10:
        print("  → F1 Prime の方が大幅劣化 → 採用不可")
    else:
        print("  → F1 単体とほぼ同等 → 観察止まり推奨")
    print()

    # === Section D: 年別 ROI ===
    print("--- Section D: F1 Prime 年別 ROI ---")
    print(f"  {'年':<6} {'n':>4} {'hits':>4} {'hit率':>6} {'avg_pay':>7} {'ROI':>7} {'損益':>10}")
    for year in ["2022", "2023", "2024", "2025", "2026"]:
        ry = run(
            f"F1 Prime {year}",
            f1_prime + f" AND r.race_date >= {PH} AND r.race_date < {PH}",
            (f"{year}-01-01", f"{int(year)+1}-01-01"),
        )
        if ry["n"]:
            print(f"  {year:<6} {ry['n']:>4} {ry['hits']:>4} {ry['hit_rate']:>5.1f}% "
                  f"{ry['avg_payout']:>6,.0f} {ry['roi']:>6.1f}% {ry['profit']:+,}")
        else:
            print(f"  {year:<6}  -")
    print()

    # === Section E: 11R vs 12R 個別 ===
    print("--- Section E: 11R / 12R 個別 ROI ---")
    r11 = run("F1 + 11R", f1_base + " AND r.race_number = 11")
    r12 = run("F1 + 12R", f1_base + " AND r.race_number = 12")
    print(f"  F1 + 11R: ", end="")
    emit(r11, "")
    print(f"  F1 + 12R: ", end="")
    emit(r12, "")
    print()

    # === Section F: F1 Prime の hit 払戻分布 ===
    print("--- Section F: F1 Prime hit 払戻分布 (帯別) ---")
    where_match = f"""
        e1.boat_number=1 AND e1.class_number=1
        AND r.stadium_number NOT IN ({",".join([PH]*len(EXCLUDE_B_VENUES))})
        AND r.race_grade_number=5
        AND (pv.weather_number IS NULL OR pv.weather_number!=3)
        AND NOT EXISTS (SELECT 1 FROM race_entries e2 JOIN racers ra ON e2.racer_number=ra.racer_number
                        WHERE e2.race_id=r.race_id AND ra.gender=2)
        AND e1.national_top_1_percent >= 7
        AND EXISTS (SELECT 1 FROM race_entries e2x
                    WHERE e2x.race_id=r.race_id AND e2x.boat_number=2 AND e2x.assigned_motor_top_2_percent>=40)
        AND r.race_number IN (11,12)
    """
    cur = conn.cursor()
    cur.execute(f"""
        SELECT
          SUM(CASE WHEN pp.payout BETWEEN 100 AND 499 THEN 1 ELSE 0 END) AS r_lt500,
          SUM(CASE WHEN pp.payout BETWEEN 500 AND 999 THEN 1 ELSE 0 END) AS r_l4_band,
          SUM(CASE WHEN pp.payout BETWEEN 1000 AND 1999 THEN 1 ELSE 0 END) AS r_1000_2000,
          SUM(CASE WHEN pp.payout BETWEEN 2000 AND 4999 THEN 1 ELSE 0 END) AS r_2000_5000,
          SUM(CASE WHEN pp.payout >= 5000 THEN 1 ELSE 0 END) AS r_5000_plus,
          COUNT(pp.payout) AS r_hits
        FROM races r
        JOIN race_entries e1 ON e1.race_id=r.race_id AND e1.boat_number=1
        LEFT JOIN race_previews pv ON pv.race_id=r.race_id AND pv.boat_number=1
        JOIN race_payouts pp ON pp.race_id=r.race_id AND pp.bet_type='trifecta' AND pp.combination='1-2-3'
        WHERE {where_match}
    """, list(EXCLUDE_B_VENUES))
    bands = cur.fetchone()
    print(f"  hit 内訳 (全 {bands[5]} hits):")
    print(f"    <¥500       : {bands[0]:>3}")
    print(f"    ¥500-999 (L4): {bands[1]:>3}  ← 既存 L4 帯")
    print(f"    ¥1000-1999  : {bands[2]:>3}")
    print(f"    ¥2000-4999  : {bands[3]:>3}")
    print(f"    >=¥5000     : {bands[4]:>3}")

    # L4 帯のみの ROI を推定
    if bands[1] and r_all["n"]:
        l4_band_hit_rate = 100 * bands[1] / r_all["n"]
        avg_pay_l4_band = 750  # L4 帯の中点と仮定
        roi_l4_only = l4_band_hit_rate * avg_pay_l4_band / 100
        print()
        print(f"  推定 L4 帯のみ ROI (粗算): {roi_l4_only:.1f}%")
        print(f"    = (L4 帯 hit 率 {l4_band_hit_rate:.2f}%) × (平均配当 750円仮定)")

    conn.close()
    print()
    print("=" * 80)
    print("検証完了")
    print("=" * 80)


if __name__ == "__main__":
    main()
