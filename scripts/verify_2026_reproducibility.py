"""
2026年データのみでの再現性検証

これまでの発見が「過去の幻想」か「今も生きている戦略」かを判定する。

検証項目:
  1. 三連単1番人気500-1000円 + 1号艇単勝 ROI (期待: 全年 +29.56%)
  2. 三連単1番人気<500円 (期待: +21.83%)
  3. 三連単1番人気1000-2000円 (期待: +19.86%)
  4. 艇5+tilt=3.0+A2 三連単 (期待: ROI +118.29%)
  5. モーター35-50% フィルタ (期待: -7.29%)
  6. Sweet Spot 全部入り (期待: -4.37%)
  7. 会場別 (江戸川・平和島 トップ)
"""
import sqlite3
import random
import statistics
from typing import List

DB = "data/boatrace.db"
N_BOOT = 2000
random.seed(42)

YEAR_FILTER = "r.race_date >= '2026-01-01' AND r.race_date < '2027-01-01'"


def bootstrap_ci(payouts: List[float], bet: float = 100) -> dict:
    n = len(payouts)
    if n == 0:
        return {"n": 0, "roi": None, "lo": None, "hi": None, "p0": None}
    rois = []
    for _ in range(N_BOOT):
        sample = random.choices(payouts, k=n)
        rois.append(sum(sample) / n / bet - 1.0)
    rois.sort()
    return {
        "n": n,
        "roi": sum(payouts) / n / bet - 1.0,
        "lo": rois[int(N_BOOT * 0.025)],
        "hi": rois[int(N_BOOT * 0.975)],
        "p0": sum(1 for r in rois if r > 0) / N_BOOT,
    }


def show(label: str, r: dict, expected: float = None):
    if r["n"] == 0:
        print(f"  {label:<60} [no data]")
        return
    delta = ""
    if expected is not None:
        delta = f"  (期待 {expected:+.2%}, 差 {(r['roi'] - expected):+.2%})"
    flag = ""
    if r["hi"] > 0: flag = " *** CI+"
    elif r["p0"] > 0.05: flag = " * P>5%"
    print(f"  {label:<60} n={r['n']:>6,}  ROI={r['roi']:>+8.2%}  CI=[{r['lo']:>+7.2%}, {r['hi']:>+7.2%}]  P>0={r['p0']:>5.1%}{flag}{delta}")


def main():
    conn = sqlite3.connect(DB)

    print("=" * 120)
    print("2026年データのみ 再現性検証")
    print("=" * 120)

    # ベースライン
    print("\n[ベースライン]")
    cur = conn.execute(f"""
        SELECT CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout, 0) ELSE 0 END
        FROM races r
        JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = 1
        LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='win' AND pp.combination='1'
        WHERE {YEAR_FILTER}
    """)
    payouts = [float(row[0]) for row in cur.fetchall()]
    r = bootstrap_ci(payouts)
    show("2026 全レース 1号艇単勝", r, expected=-0.0929)

    cur = conn.execute(f"""
        SELECT CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout, 0) ELSE 0 END
        FROM races r
        JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = 1
        LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='win' AND pp.combination='1'
        WHERE {YEAR_FILTER} AND r.stadium_number NOT IN (2,7,10,21)
    """)
    payouts = [float(row[0]) for row in cur.fetchall()]
    r = bootstrap_ci(payouts)
    show("2026 Sweet Spot (4会場除外)", r, expected=-0.0872)

    # =========================================================
    # 検証1: 三連単1番人気帯 別 ROI ★最重要
    # =========================================================
    print("\n[検証1] 三連単1番人気帯 別 1号艇単勝 ROI - ★最重要")

    bands = [
        ("超本命 <500",      0, 500,  0.2183),
        ("本命 500-1000",    500, 1000, 0.2956),  # ★最強戦略
        ("やや本命 1k-2k",   1000, 2000, 0.1986),
        ("拮抗 2k-5k",       2000, 5000, -0.0721),
        ("荒れ寄り 5k-10k",  5000, 10000, -0.4079),
        ("波乱 10k+",        10000, 99999999, -0.7314),
    ]
    for label, lo, hi, exp in bands:
        cur = conn.execute(f"""
            WITH min_tri AS (
                SELECT race_id, MIN(payout) as min_p
                FROM race_payouts WHERE bet_type='trifecta' GROUP BY race_id
            )
            SELECT CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout, 0) ELSE 0 END
            FROM races r
            JOIN min_tri mt ON r.race_id = mt.race_id
            JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = 1
            LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='win' AND pp.combination='1'
            WHERE {YEAR_FILTER} AND mt.min_p >= ? AND mt.min_p < ?
        """, (lo, hi))
        payouts = [float(row[0]) for row in cur.fetchall()]
        r = bootstrap_ci(payouts)
        show(f"三連単1番人気 {label}", r, expected=exp)

    # =========================================================
    # 検証2: Ultra Signal (艇5+tilt=3.0+A2)
    # =========================================================
    print("\n[検証2] Ultra Signal (艇5+tilt=3.0+A2)")
    cur = conn.execute(f"""
        SELECT CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout, 0) ELSE 0 END
        FROM races r
        JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 5 AND e.class_number = 2
        JOIN race_previews p ON r.race_id = p.race_id AND p.boat_number = 5
        JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = 5
        LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='win' AND pp.combination='5'
        WHERE {YEAR_FILTER} AND p.tilt_adjustment = 3.0
    """)
    payouts = [float(row[0]) for row in cur.fetchall()]
    r = bootstrap_ci(payouts)
    show("艇5+tilt=3.0+A2 単勝", r, expected=0.1290)

    # 三連単版 (1着=艇5 のレースで三連単配当)
    cur = conn.execute(f"""
        SELECT pp_tri.payout as p
        FROM races r
        JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 5 AND e.class_number = 2
        JOIN race_previews p ON r.race_id = p.race_id AND p.boat_number = 5
        JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = 5
        LEFT JOIN race_payouts pp_tri ON pp_tri.race_id = r.race_id AND pp_tri.bet_type='trifecta'
        WHERE {YEAR_FILTER} AND p.tilt_adjustment = 3.0 AND res.finishing_position = 1
    """)
    win_payouts = [float(row[0]) for row in cur.fetchall() if row[0]]
    cur = conn.execute(f"""
        SELECT COUNT(*) FROM races r
        JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 5 AND e.class_number = 2
        JOIN race_previews p ON r.race_id = p.race_id AND p.boat_number = 5
        WHERE {YEAR_FILTER} AND p.tilt_adjustment = 3.0
    """)
    n_total = cur.fetchone()[0]
    if n_total > 0:
        win_rate = len(win_payouts) / n_total
        avg_payout = sum(win_payouts) / max(len(win_payouts), 1) if win_payouts else 0
        roi_20 = (win_rate * avg_payout) / (20 * 100) - 1 if win_payouts else -1.0
        roi_10_focused = (win_rate * (14/15) * avg_payout) / (10 * 100) - 1 if win_payouts else -1.0
        print(f"  Ultra trifecta n_total={n_total}, 1着={len(win_payouts)}({win_rate:.1%}), avg配当={avg_payout:,.0f}円")
        print(f"  全20点買い ROI 概算: {roi_20:+.2%} (期待 -43.55%)")
        print(f"  10点絞り買い ROI 概算: {roi_10_focused:+.2%} (期待 +12.90%)")

    # =========================================================
    # 検証3: モーター35-50% フィルタ
    # =========================================================
    print("\n[検証3] モーターフィルタ")
    for label, lo, hi, exp in [
        ("Motor 35-50%", 35, 50, -0.0729),
        ("Motor 35-45%", 35, 45, -0.0737),
        ("Motor 40-50%", 40, 50, -0.0719),
    ]:
        cur = conn.execute(f"""
            SELECT CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout, 0) ELSE 0 END
            FROM races r
            JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
            JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = 1
            LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='win' AND pp.combination='1'
            WHERE {YEAR_FILTER}
              AND r.stadium_number NOT IN (2,7,10,21)
              AND e.assigned_motor_top_2_percent >= ? AND e.assigned_motor_top_2_percent < ?
        """, (lo, hi))
        payouts = [float(row[0]) for row in cur.fetchall()]
        r = bootstrap_ci(payouts)
        show(f"SS + {label}", r, expected=exp)

    # =========================================================
    # 検証4: 全部入り Sweet Spot
    # =========================================================
    print("\n[検証4] 全部入り (SS+水面+Motor35-50+展示±0.05+微風)")
    cur = conn.execute(f"""
        SELECT CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout, 0) ELSE 0 END
        FROM races r
        JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
        JOIN race_previews p ON r.race_id = p.race_id AND p.boat_number = 1
        JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = 1
        JOIN stadiums s ON r.stadium_number = s.stadium_number
        LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='win' AND pp.combination='1'
        WHERE {YEAR_FILTER}
          AND r.stadium_number NOT IN (2,7,10,21)
          AND s.in_strength != 'low'
          AND e.assigned_motor_top_2_percent >= 35 AND e.assigned_motor_top_2_percent < 50
          AND (p.exhibition_time - (SELECT MIN(p2.exhibition_time) FROM race_previews p2
                                     WHERE p2.race_id = r.race_id AND p2.exhibition_time IS NOT NULL)) <= 0.05
          AND p.wind_speed BETWEEN 1 AND 3
    """)
    payouts = [float(row[0]) for row in cur.fetchall()]
    r = bootstrap_ci(payouts)
    show("全部入り戦略", r, expected=-0.0437)

    # =========================================================
    # 検証5: 会場別 (Top5)
    # =========================================================
    print("\n[検証5] 会場別 Motor35-50% フィルタ ROI")
    for sid, name, exp in [(3, "江戸川", 0.0049), (4, "平和島", 0.0028),
                            (5, "多摩川", -0.0260), (17, "宮島", -0.0419),
                            (6, "浜名湖", -0.0486)]:
        cur = conn.execute(f"""
            SELECT CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout, 0) ELSE 0 END
            FROM races r
            JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
            JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = 1
            LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='win' AND pp.combination='1'
            WHERE {YEAR_FILTER}
              AND r.stadium_number = ?
              AND e.assigned_motor_top_2_percent >= 35 AND e.assigned_motor_top_2_percent < 50
        """, (sid,))
        payouts = [float(row[0]) for row in cur.fetchall()]
        r = bootstrap_ci(payouts)
        show(f"{name} + Motor35-50", r, expected=exp)

    # =========================================================
    # 検証6: チルト戦略の単純検証
    # =========================================================
    print("\n[検証6] チルト戦略 (年別比較用)")
    for boat, tilt_where, label_base, exp in [
        (4, "p.tilt_adjustment >= 0.5 AND p.tilt_adjustment <= 1.5", "艇4 tilt 0.5-1.5 単勝", -0.1109),
        (5, "p.tilt_adjustment = 3.0", "艇5 tilt=3.0 単勝", -0.1482),
    ]:
        cur = conn.execute(f"""
            SELECT CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout, 0) ELSE 0 END
            FROM races r
            JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = {boat}
            JOIN race_previews p ON r.race_id = p.race_id AND p.boat_number = {boat}
            JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = {boat}
            LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='win' AND pp.combination='{boat}'
            WHERE {YEAR_FILTER} AND {tilt_where}
        """)
        payouts = [float(row[0]) for row in cur.fetchall()]
        r = bootstrap_ci(payouts)
        show(label_base, r, expected=exp)

    conn.close()


if __name__ == "__main__":
    main()
