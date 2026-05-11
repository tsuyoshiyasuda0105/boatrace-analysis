"""
展示タイムの閾値検証

仮説:
  - 展示タイム ±0.05秒以内 → 上位級
  - 展示タイム 0.10秒以上遅い → 伸び負け
  - 展示タイム順位 上位2 → 舟足仕上がり
  - スタート展示と直近成績の整合性 → 精度UP

検証:
  1. 1号艇の (展示タイム - レース内最速) の差別に 1着率・ROI
  2. 展示タイム順位別の 1着率・ROI
  3. (展示ST - 平均ST) の整合性チェック
"""
import sqlite3

DB = "data/boatrace.db"


def main():
    conn = sqlite3.connect(DB)

    # =========================================================
    # 検証1: 1号艇の展示タイム差別 ROI
    # =========================================================
    print("=" * 80)
    print("【検証1】1号艇の展示タイム差 (vs レース内最速) の階層別 ROI")
    print("=" * 80)
    cur = conn.execute(
        """
        WITH per_race AS (
            SELECT r.race_id, p1.exhibition_time as t1,
                   MIN(p.exhibition_time) as t_min
            FROM races r
            JOIN race_previews p1 ON r.race_id = p1.race_id AND p1.boat_number = 1
            JOIN race_previews p  ON r.race_id = p.race_id
            WHERE p1.exhibition_time IS NOT NULL AND p.exhibition_time IS NOT NULL
            GROUP BY r.race_id, p1.exhibition_time
        )
        SELECT
            CASE
                WHEN (pr.t1 - pr.t_min) <= 0.05 THEN 'A: 最速±0.05以内'
                WHEN (pr.t1 - pr.t_min) <= 0.10 THEN 'B: 最速+0.05〜0.10'
                WHEN (pr.t1 - pr.t_min) <= 0.15 THEN 'C: 最速+0.10〜0.15'
                ELSE 'D: 最速+0.15以上 (伸び負け)'
            END as tier,
            COUNT(*) as n,
            AVG(CASE WHEN res.finishing_position=1 THEN 1.0 ELSE 0.0 END) as wr,
            AVG(CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout,0) ELSE 0 END) as avgP
        FROM per_race pr
        JOIN race_results res ON pr.race_id = res.race_id AND res.boat_number = 1
        LEFT JOIN race_payouts pp ON pp.race_id = pr.race_id AND pp.bet_type='win' AND pp.combination='1'
        GROUP BY tier
        ORDER BY tier
        """
    )
    print(f"{'階層':<28} {'n':>8} {'1着率':>8} {'avg_pay':>10} {'ROI':>10}")
    print("-" * 70)
    for tier, n, wr, ap in cur.fetchall():
        roi = (ap or 0)/100 - 1
        print(f"{tier:<28} {n:>8,} {wr:>8.3f} {ap or 0:>10.1f} {roi:>+10.2%}")

    # =========================================================
    # 検証2: 1号艇の展示タイム順位別 ROI
    # =========================================================
    print()
    print("=" * 80)
    print("【検証2】1号艇の展示タイム順位 (1-6位) 別 ROI")
    print("=" * 80)
    cur = conn.execute(
        """
        WITH ranked AS (
            SELECT r.race_id,
                   p.boat_number,
                   p.exhibition_time,
                   RANK() OVER (PARTITION BY r.race_id ORDER BY p.exhibition_time ASC) as rk
            FROM races r
            JOIN race_previews p ON r.race_id = p.race_id
            WHERE p.exhibition_time IS NOT NULL
        )
        SELECT rk.rk as ext_rank,
               COUNT(*) as n,
               AVG(CASE WHEN res.finishing_position=1 THEN 1.0 ELSE 0.0 END) as wr,
               AVG(CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout,0) ELSE 0 END) as avgP
        FROM ranked rk
        JOIN race_results res ON rk.race_id = res.race_id AND rk.boat_number = res.boat_number
        LEFT JOIN race_payouts pp ON pp.race_id = rk.race_id AND pp.bet_type='win' AND pp.combination=CAST(rk.boat_number as TEXT)
        WHERE rk.boat_number = 1
        GROUP BY rk.rk
        ORDER BY rk.rk
        """
    )
    print(f"{'展示順位':<12} {'n':>8} {'1着率':>8} {'avg_pay':>10} {'ROI':>10}")
    print("-" * 60)
    for rk_, n, wr, ap in cur.fetchall():
        roi = (ap or 0)/100 - 1
        print(f"{rk_}位{'':<8} {n:>8,} {wr:>8.3f} {ap or 0:>10.1f} {roi:>+10.2%}")

    # =========================================================
    # 検証3: Sweet Spot に 展示タイム フィルタを重ね掛け
    # =========================================================
    print()
    print("=" * 80)
    print("【検証3】Sweet Spot + 展示タイム条件 重ね掛け")
    print("=" * 80)
    scenarios = [
        ("Sweet Spot 単体",
         "r.stadium_number NOT IN (2,7,10,21)"),
        ("+ 展示タイム 最速±0.05以内",
         """r.stadium_number NOT IN (2,7,10,21)
            AND (p1.exhibition_time - (SELECT MIN(pp2.exhibition_time)
                                       FROM race_previews pp2
                                       WHERE pp2.race_id = r.race_id
                                         AND pp2.exhibition_time IS NOT NULL)) <= 0.05"""),
        ("+ 展示タイム 最速±0.10以内",
         """r.stadium_number NOT IN (2,7,10,21)
            AND (p1.exhibition_time - (SELECT MIN(pp2.exhibition_time)
                                       FROM race_previews pp2
                                       WHERE pp2.race_id = r.race_id
                                         AND pp2.exhibition_time IS NOT NULL)) <= 0.10"""),
        ("+ モーター35%+ + 展示0.05以内",
         """r.stadium_number NOT IN (2,7,10,21)
            AND e.assigned_motor_top_2_percent >= 35
            AND (p1.exhibition_time - (SELECT MIN(pp2.exhibition_time)
                                       FROM race_previews pp2
                                       WHERE pp2.race_id = r.race_id
                                         AND pp2.exhibition_time IS NOT NULL)) <= 0.05"""),
        ("+ モーター35-50% + 展示0.05以内 (全部入り)",
         """r.stadium_number NOT IN (2,7,10,21)
            AND e.assigned_motor_top_2_percent >= 35
            AND e.assigned_motor_top_2_percent < 50
            AND (p1.exhibition_time - (SELECT MIN(pp2.exhibition_time)
                                       FROM race_previews pp2
                                       WHERE pp2.race_id = r.race_id
                                         AND pp2.exhibition_time IS NOT NULL)) <= 0.05"""),
    ]
    print(f"{'戦略':<45} {'n':>8} {'1着率':>8} {'avg_pay':>10} {'ROI':>10}")
    print("-" * 90)
    for label, where in scenarios:
        cur = conn.execute(
            f"""
            SELECT COUNT(*) as n,
                   AVG(CASE WHEN res.finishing_position=1 THEN 1.0 ELSE 0.0 END) as wr,
                   AVG(CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout,0) ELSE 0 END) as ap
            FROM races r
            JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
            JOIN race_previews p1 ON r.race_id = p1.race_id AND p1.boat_number = 1
            JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = 1
            LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='win' AND pp.combination='1'
            WHERE {where}
            """
        )
        n, wr, ap = cur.fetchone()
        roi = (ap or 0)/100 - 1
        print(f"{label:<45} {n:>8,} {wr or 0:>8.3f} {ap or 0:>10.1f} {roi:>+10.2%}")

    # =========================================================
    # 検証4: 展示ST と 平均ST の整合性
    # =========================================================
    print()
    print("=" * 80)
    print("【検証4】1号艇 展示ST vs 平均ST の整合性")
    print("=" * 80)
    cur = conn.execute(
        """
        SELECT
            CASE
                WHEN (p1.start_timing_exhibition - e.avg_start_timing) <= -0.03 THEN 'A: 展示が平均より大幅速い (+0.03超)'
                WHEN (p1.start_timing_exhibition - e.avg_start_timing) <= 0.00 THEN 'B: 展示≈平均 (差 0〜-0.03)'
                WHEN (p1.start_timing_exhibition - e.avg_start_timing) <= 0.03 THEN 'C: 展示が平均より少し遅い (+0〜0.03)'
                ELSE 'D: 展示が平均より大幅遅い (+0.03超)'
            END as tier,
            COUNT(*) as n,
            AVG(CASE WHEN res.finishing_position=1 THEN 1.0 ELSE 0.0 END) as wr,
            AVG(CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout,0) ELSE 0 END) as ap
        FROM races r
        JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
        JOIN race_previews p1 ON r.race_id = p1.race_id AND p1.boat_number = 1
        JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = 1
        LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='win' AND pp.combination='1'
        WHERE p1.start_timing_exhibition IS NOT NULL AND e.avg_start_timing IS NOT NULL
        GROUP BY tier
        ORDER BY tier
        """
    )
    print(f"{'階層':<45} {'n':>8} {'1着率':>8} {'avg_pay':>10} {'ROI':>10}")
    print("-" * 90)
    for tier, n, wr, ap in cur.fetchall():
        roi = (ap or 0)/100 - 1
        print(f"{tier:<45} {n:>8,} {wr or 0:>8.3f} {ap or 0:>10.1f} {roi:>+10.2%}")

    conn.close()


if __name__ == "__main__":
    main()
