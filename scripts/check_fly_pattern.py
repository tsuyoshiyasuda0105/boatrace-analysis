"""
「飛びパターン」(1号艇沈み) シグナル検証

検証する7つのシグナル:
  1. 1号艇のスタート指数が不安定 (F/L 回数で代用)
  2. 展示で行き足が弱い (exhibition_time が遅い)
  3. 気温が高く回転が鈍い (temperature 高)
  4. 差し水面 (in_strength=low の会場)
  5. 2コースに差しが鋭い選手 (2艇の national_top_1_percent 高)
  6. 3コースに壁にならない選手 (3艇が B級 or 低成績)
  7. 追い風で1マーク流れ (風速 + 風向)

最終的に「警告シグナル数」で 1号艇 1着率の階段関数を作る。
"""
import sqlite3

DB = "data/boatrace.db"


def main():
    conn = sqlite3.connect(DB)

    # =========================================================
    # 個別シグナル検証
    # =========================================================
    print("=" * 80)
    print("[S1] 1号艇 STペナルティ累積 (F+L) 別 1着率")
    print("=" * 80)
    cur = conn.execute(
        """
        SELECT
            CASE
                WHEN (COALESCE(e.flying_count,0) + COALESCE(e.late_count,0)) = 0 THEN '0 (clean)'
                WHEN (COALESCE(e.flying_count,0) + COALESCE(e.late_count,0)) = 1 THEN '1'
                WHEN (COALESCE(e.flying_count,0) + COALESCE(e.late_count,0)) = 2 THEN '2'
                ELSE '3+ (unstable)'
            END as fl_tier,
            COUNT(*) as n,
            AVG(CASE WHEN res.finishing_position=1 THEN 1.0 ELSE 0.0 END) as wr
        FROM races r
        JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
        JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = 1
        GROUP BY fl_tier
        ORDER BY MIN(COALESCE(e.flying_count,0) + COALESCE(e.late_count,0))
        """
    )
    print(f"{'F+L count':<18} {'n':>10} {'boat1_winR':>12}")
    for tier, n, wr in cur.fetchall():
        print(f"{tier:<18} {n:>10,} {wr:>12.3f}")

    # 気温
    print()
    print("=" * 80)
    print("[S3] Temperature effect on boat 1")
    print("=" * 80)
    cur = conn.execute(
        """
        SELECT
            CASE
                WHEN p.temperature < 10 THEN 'cold (<10C)'
                WHEN p.temperature < 20 THEN 'cool (10-20C)'
                WHEN p.temperature < 28 THEN 'warm (20-28C)'
                WHEN p.temperature < 33 THEN 'hot (28-33C)'
                ELSE 'extreme (33C+)'
            END as t_tier,
            COUNT(*) as n,
            AVG(CASE WHEN res.finishing_position=1 THEN 1.0 ELSE 0.0 END) as wr,
            AVG(CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout,0) ELSE 0 END) as ap
        FROM races r
        JOIN race_previews p ON r.race_id = p.race_id AND p.boat_number = 1
        JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = 1
        LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='win' AND pp.combination='1'
        WHERE p.temperature IS NOT NULL
        GROUP BY t_tier
        ORDER BY MIN(p.temperature)
        """
    )
    print(f"{'Temp':<18} {'n':>10} {'boat1_winR':>12} {'ROI':>10}")
    for tier, n, wr, ap in cur.fetchall():
        roi = (ap or 0)/100 - 1
        print(f"{tier:<18} {n:>10,} {wr:>12.3f} {roi:>+10.2%}")

    # 差し水面 (戸田・江戸川など in_strength=low)
    print()
    print("=" * 80)
    print("[S4] Sashi-water (in_strength=low) stadiums effect")
    print("=" * 80)
    cur = conn.execute(
        """
        SELECT s.in_strength,
               COUNT(*) as n,
               AVG(CASE WHEN res.finishing_position=1 THEN 1.0 ELSE 0.0 END) as wr,
               AVG(CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout,0) ELSE 0 END) as ap
        FROM races r
        JOIN stadiums s ON r.stadium_number = s.stadium_number
        JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = 1
        LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='win' AND pp.combination='1'
        GROUP BY s.in_strength
        ORDER BY CASE s.in_strength WHEN 'low' THEN 1 WHEN 'mid' THEN 2 WHEN 'high' THEN 3 ELSE 4 END
        """
    )
    print(f"{'In_strength':<18} {'n':>10} {'boat1_winR':>12} {'ROI':>10}")
    for ins, n, wr, ap in cur.fetchall():
        roi = (ap or 0)/100 - 1
        print(f"{ins:<18} {n:>10,} {wr:>12.3f} {roi:>+10.2%}")

    # 2コース選手の実力
    print()
    print("=" * 80)
    print("[S5] Boat 2's strength (national_top_1_pct) vs boat 1 win rate")
    print("=" * 80)
    cur = conn.execute(
        """
        SELECT
            CASE
                WHEN e2.national_top_1_percent < 15 THEN '<15 (weak)'
                WHEN e2.national_top_1_percent < 25 THEN '15-25'
                WHEN e2.national_top_1_percent < 35 THEN '25-35'
                WHEN e2.national_top_1_percent < 45 THEN '35-45'
                ELSE '45+ (sashi specialist?)'
            END as boat2_tier,
            COUNT(*) as n,
            AVG(CASE WHEN res1.finishing_position=1 THEN 1.0 ELSE 0.0 END) as wr,
            AVG(CASE WHEN res1.finishing_position=1 THEN COALESCE(pp.payout,0) ELSE 0 END) as ap
        FROM races r
        JOIN race_entries e2 ON r.race_id = e2.race_id AND e2.boat_number = 2
        JOIN race_results res1 ON r.race_id = res1.race_id AND res1.boat_number = 1
        LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='win' AND pp.combination='1'
        WHERE e2.national_top_1_percent IS NOT NULL
        GROUP BY boat2_tier
        ORDER BY MIN(e2.national_top_1_percent)
        """
    )
    print(f"{'Boat 2 strength':<25} {'n':>10} {'boat1_winR':>12} {'ROI':>10}")
    for tier, n, wr, ap in cur.fetchall():
        roi = (ap or 0)/100 - 1
        print(f"{tier:<25} {n:>10,} {wr:>12.3f} {roi:>+10.2%}")

    # 3コース選手の弱さ (壁にならない)
    print()
    print("=" * 80)
    print("[S6] Boat 3's class effect on boat 1 win rate")
    print("=" * 80)
    cur = conn.execute(
        """
        SELECT e3.class_number as cls3,
               COUNT(*) as n,
               AVG(CASE WHEN res1.finishing_position=1 THEN 1.0 ELSE 0.0 END) as wr,
               AVG(CASE WHEN res1.finishing_position=1 THEN COALESCE(pp.payout,0) ELSE 0 END) as ap
        FROM races r
        JOIN race_entries e3 ON r.race_id = e3.race_id AND e3.boat_number = 3
        JOIN race_results res1 ON r.race_id = res1.race_id AND res1.boat_number = 1
        LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='win' AND pp.combination='1'
        WHERE e3.class_number IS NOT NULL
        GROUP BY cls3
        """
    )
    name = {1:'A1', 2:'A2', 3:'B1', 4:'B2'}
    print(f"{'Boat 3 class':<18} {'n':>10} {'boat1_winR':>12} {'ROI':>10}")
    for cls, n, wr, ap in cur.fetchall():
        roi = (ap or 0)/100 - 1
        print(f"{name.get(cls, cls):<18} {n:>10,} {wr:>12.3f} {roi:>+10.2%}")

    # =========================================================
    # 警告シグナル数による階段スコア
    # =========================================================
    print()
    print("=" * 80)
    print("[Combined] Warning signal count vs boat 1 outcome")
    print("=" * 80)
    print("Signals:")
    print("  W1: boat1 F+L >= 2 (ST unstable)")
    print("  W2: boat1 exhibition_time rank >= 3 (slow takeoff)")
    print("  W3: temperature >= 28 (hot, motor sluggish)")
    print("  W4: stadium in_strength=low (sashi water)")
    print("  W5: boat2 class A1 or A2 (strong sashi candidate)")
    print("  W6: boat3 class B1 or B2 (no wall)")
    print("  W7: wind_speed >= 4 (windy)")
    print()
    cur = conn.execute(
        """
        WITH ex_rank AS (
            SELECT r.race_id, p.boat_number,
                   RANK() OVER (PARTITION BY r.race_id ORDER BY p.exhibition_time ASC) as ex_rk
            FROM races r
            JOIN race_previews p ON r.race_id = p.race_id
            WHERE p.exhibition_time IS NOT NULL
        ),
        signals AS (
            SELECT r.race_id,
                   CASE WHEN (COALESCE(e1.flying_count,0)+COALESCE(e1.late_count,0)) >= 2 THEN 1 ELSE 0 END as W1,
                   CASE WHEN ex.ex_rk >= 3 THEN 1 ELSE 0 END as W2,
                   CASE WHEN p1.temperature >= 28 THEN 1 ELSE 0 END as W3,
                   CASE WHEN s.in_strength = 'low' THEN 1 ELSE 0 END as W4,
                   CASE WHEN e2.class_number <= 2 THEN 1 ELSE 0 END as W5,
                   CASE WHEN e3.class_number >= 3 THEN 1 ELSE 0 END as W6,
                   CASE WHEN p1.wind_speed >= 4 THEN 1 ELSE 0 END as W7,
                   res.finishing_position as pos1,
                   pp.payout as payout
            FROM races r
            JOIN race_entries e1 ON r.race_id = e1.race_id AND e1.boat_number = 1
            JOIN race_entries e2 ON r.race_id = e2.race_id AND e2.boat_number = 2
            JOIN race_entries e3 ON r.race_id = e3.race_id AND e3.boat_number = 3
            JOIN race_previews p1 ON r.race_id = p1.race_id AND p1.boat_number = 1
            LEFT JOIN ex_rank ex ON r.race_id = ex.race_id AND ex.boat_number = 1
            JOIN stadiums s ON r.stadium_number = s.stadium_number
            JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = 1
            LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='win' AND pp.combination='1'
            WHERE p1.exhibition_time IS NOT NULL
        )
        SELECT (W1+W2+W3+W4+W5+W6+W7) as score,
               COUNT(*) as n,
               AVG(CASE WHEN pos1 = 1 THEN 1.0 ELSE 0.0 END) as wr,
               AVG(CASE WHEN pos1 = 1 THEN COALESCE(payout,0) ELSE 0 END) as ap
        FROM signals
        GROUP BY score
        ORDER BY score
        """
    )
    print(f"{'#warnings':<12} {'n':>10} {'boat1_winR':>12} {'ROI':>10}")
    print("-" * 50)
    rows = cur.fetchall()
    for score, n, wr, ap in rows:
        roi = (ap or 0)/100 - 1
        bar = "#" * min(int(wr*30), 30)
        print(f"{score} signals   {n:>10,} {wr:>12.3f} {roi:>+10.2%}  {bar}")

    # 三連単/万舟券狙い: 警告4個以上のレースで穴目を見る
    print()
    print("=" * 80)
    print("[Bonus] 警告4個以上のレースでの 6コース絡みトリプル払戻分布")
    print("=" * 80)
    cur = conn.execute(
        """
        WITH ex_rank AS (
            SELECT r.race_id, p.boat_number,
                   RANK() OVER (PARTITION BY r.race_id ORDER BY p.exhibition_time ASC) as ex_rk
            FROM races r
            JOIN race_previews p ON r.race_id = p.race_id
            WHERE p.exhibition_time IS NOT NULL
        ),
        flagged AS (
            SELECT r.race_id,
                   (CASE WHEN (COALESCE(e1.flying_count,0)+COALESCE(e1.late_count,0)) >= 2 THEN 1 ELSE 0 END
                  + CASE WHEN ex.ex_rk >= 3 THEN 1 ELSE 0 END
                  + CASE WHEN p1.temperature >= 28 THEN 1 ELSE 0 END
                  + CASE WHEN s.in_strength = 'low' THEN 1 ELSE 0 END
                  + CASE WHEN e2.class_number <= 2 THEN 1 ELSE 0 END
                  + CASE WHEN e3.class_number >= 3 THEN 1 ELSE 0 END
                  + CASE WHEN p1.wind_speed >= 4 THEN 1 ELSE 0 END) as score
            FROM races r
            JOIN race_entries e1 ON r.race_id = e1.race_id AND e1.boat_number = 1
            JOIN race_entries e2 ON r.race_id = e2.race_id AND e2.boat_number = 2
            JOIN race_entries e3 ON r.race_id = e3.race_id AND e3.boat_number = 3
            JOIN race_previews p1 ON r.race_id = p1.race_id AND p1.boat_number = 1
            LEFT JOIN ex_rank ex ON r.race_id = ex.race_id AND ex.boat_number = 1
            JOIN stadiums s ON r.stadium_number = s.stadium_number
            WHERE p1.exhibition_time IS NOT NULL
        )
        SELECT
            CASE WHEN f.score >= 4 THEN '>=4 (危険)' ELSE '<4 (普通)' END as flag,
            COUNT(*) as n,
            AVG(CASE WHEN res.finishing_position=1 THEN 1.0 ELSE 0.0 END) as boat1_wr,
            AVG(COALESCE(pt.payout, 0)) as avg_trifecta_payout
        FROM flagged f
        JOIN race_results res ON f.race_id = res.race_id AND res.boat_number = 1
        LEFT JOIN race_payouts pt ON f.race_id = pt.race_id AND pt.bet_type='trifecta'
        GROUP BY flag
        """
    )
    print(f"{'flag':<14} {'n':>10} {'boat1_wr':>10} {'avg三連単払戻':>16}")
    for flag, n, wr, ap in cur.fetchall():
        print(f"{flag:<14} {n:>10,} {wr:>10.3f} {ap or 0:>16,.0f}")

    conn.close()


if __name__ == "__main__":
    main()
