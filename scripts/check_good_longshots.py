"""
「根拠のある穴」検証

5つのシグナル (利用可能なものを検証):
  L1: 展示直線抜け  → 各艇の exhibition_time 最速
  L2: 外の壁が厚い  → 4-6艇の class 平均 (低いほど壁強い = A1多い)
  L3: 内側 ST 不安定 → 1-2艇 F+L 累積
  L4: 気温/水温が味方 → 水温-気温の差 (モーターパワー)
  L5: 節間で脚上昇 → 未取得 (series_day 未投入)

検証:
  - 1着艇が 2/3/4/5/6号艇 になるレースの「穴シグナル」分布
  - シグナル合成スコアと「非1号艇1着率」の階段関数
  - 高スコア穴狙いの三連単 ROI
"""
import sqlite3

DB = "data/boatrace.db"


def main():
    conn = sqlite3.connect(DB)

    # =========================================================
    # 個別シグナル
    # =========================================================

    # L1: 各艇の exhibition_time 最速 (= 1位) なら 1着率
    print("=" * 80)
    print("[L1] 展示タイム1位の艇 -> 各コース別 1着率")
    print("=" * 80)
    cur = conn.execute(
        """
        WITH ex_rank AS (
            SELECT r.race_id, p.boat_number, p.exhibition_time,
                   RANK() OVER (PARTITION BY r.race_id ORDER BY p.exhibition_time ASC) as rk
            FROM races r
            JOIN race_previews p ON r.race_id = p.race_id
            WHERE p.exhibition_time IS NOT NULL
        )
        SELECT ex.boat_number as bn,
               COUNT(*) as n_top_ex,
               AVG(CASE WHEN res.finishing_position=1 THEN 1.0 ELSE 0.0 END) as wr
        FROM ex_rank ex
        JOIN race_results res ON ex.race_id = res.race_id AND ex.boat_number = res.boat_number
        WHERE ex.rk = 1
        GROUP BY bn
        ORDER BY bn
        """
    )
    print(f"{'boat':<6} {'n(ex_top)':>10} {'won_when_ex_top':>17}")
    for bn, n, wr in cur.fetchall():
        print(f"  {bn:<4} {n:>10,} {wr:>17.3f}")

    # L2: 4-6艇に A1/A2 が含まれる時、各艇の 1着率
    print()
    print("=" * 80)
    print("[L2] 外艇 (4-6) に A1 がいる時の各艇 1着率")
    print("=" * 80)
    cur = conn.execute(
        """
        WITH outer_a1 AS (
            SELECT r.race_id,
                   MAX(CASE WHEN e.boat_number IN (4,5,6) AND e.class_number = 1 THEN 1 ELSE 0 END) as has_a1_outer
            FROM races r
            JOIN race_entries e ON r.race_id = e.race_id
            GROUP BY r.race_id
        )
        SELECT res.boat_number as bn,
               oa.has_a1_outer,
               COUNT(*) as n,
               AVG(CASE WHEN res.finishing_position=1 THEN 1.0 ELSE 0.0 END) as wr
        FROM outer_a1 oa
        JOIN race_results res ON oa.race_id = res.race_id
        GROUP BY bn, oa.has_a1_outer
        ORDER BY bn, oa.has_a1_outer
        """
    )
    print(f"{'boat':<6} {'外A1有':<8} {'n':>10} {'1着率':>8}")
    for bn, has, n, wr in cur.fetchall():
        flag = 'YES' if has == 1 else '-'
        print(f"  {bn:<4} {flag:<8} {n:>10,} {wr:>8.3f}")

    # L3: 内側 (1-2艇) F+L 累積 が 1着分布に与える影響
    print()
    print("=" * 80)
    print("[L3] 内側 (1+2艇) F+L 累積 別 1着艇分布")
    print("=" * 80)
    cur = conn.execute(
        """
        WITH inner_fl AS (
            SELECT r.race_id,
                   (COALESCE(e1.flying_count,0) + COALESCE(e1.late_count,0)
                  + COALESCE(e2.flying_count,0) + COALESCE(e2.late_count,0)) as fl_sum
            FROM races r
            JOIN race_entries e1 ON r.race_id = e1.race_id AND e1.boat_number = 1
            JOIN race_entries e2 ON r.race_id = e2.race_id AND e2.boat_number = 2
        )
        SELECT
            CASE WHEN fl_sum = 0 THEN '0 (clean)'
                 WHEN fl_sum = 1 THEN '1'
                 WHEN fl_sum = 2 THEN '2'
                 ELSE '3+ (unstable)' END as tier,
            res.boat_number as bn,
            COUNT(*) as n,
            AVG(CASE WHEN res.finishing_position=1 THEN 1.0 ELSE 0.0 END) as wr
        FROM inner_fl ifl
        JOIN race_results res ON ifl.race_id = res.race_id
        GROUP BY tier, bn
        HAVING n >= 100
        ORDER BY MIN(fl_sum), bn
        """
    )
    print(f"{'inner_FL':<18} {'boat':<6} {'n':>10} {'win_rate':>10}")
    for tier, bn, n, wr in cur.fetchall():
        print(f"  {tier:<16} {bn:<6} {n:>10,} {wr:>10.3f}")

    # L4: 水温-気温 (motor power)
    print()
    print("=" * 80)
    print("[L4] 水温-気温の差 (モーター回転に有利? 不利?) 別 1着分布")
    print("=" * 80)
    cur = conn.execute(
        """
        SELECT
            CASE
                WHEN (p.water_temperature - p.temperature) <= -5 THEN 'A: 水温<<気温 (水冷効きにくい)'
                WHEN (p.water_temperature - p.temperature) <= -2 THEN 'B: 水温<気温 (やや不利)'
                WHEN (p.water_temperature - p.temperature) <=  2 THEN 'C: 同等'
                WHEN (p.water_temperature - p.temperature) <=  5 THEN 'D: 水温>気温 (やや有利)'
                ELSE 'E: 水温>>気温 (水冷有利)'
            END as t_diff,
            res.boat_number as bn,
            COUNT(*) as n,
            AVG(CASE WHEN res.finishing_position=1 THEN 1.0 ELSE 0.0 END) as wr
        FROM races r
        JOIN race_previews p ON r.race_id = p.race_id AND p.boat_number = 1
        JOIN race_results res ON r.race_id = res.race_id
        WHERE p.water_temperature IS NOT NULL AND p.temperature IS NOT NULL
        GROUP BY t_diff, bn
        HAVING n >= 100
        ORDER BY t_diff, bn
        """
    )
    print(f"{'water-air diff':<35} {'boat':<6} {'n':>10} {'win_rate':>10}")
    for tier, bn, n, wr in cur.fetchall():
        print(f"  {tier:<33} {bn:<6} {n:>10,} {wr:>10.3f}")

    # =========================================================
    # 「根拠のある穴スコア」の合成検証 (非1号艇1着)
    # =========================================================
    print()
    print("=" * 80)
    print("[Combined] 穴シグナル合成スコア vs 1着艇分布")
    print("=" * 80)
    print("Signals (1点ずつ):")
    print("  L1: 1号艇 NOT 展示1位")
    print("  L2: 4-6艇に A1 がいる")
    print("  L3: 1-2艇 F+L 累積 >= 2")
    print("  L4: 1号艇展示 vs 最速差 > 0.10秒")
    print("  L5: 強風 (wind_speed >= 4) or 高波 (wave >= 5)")
    print()
    cur = conn.execute(
        """
        WITH ex_rank AS (
            SELECT r.race_id, p.boat_number,
                   RANK() OVER (PARTITION BY r.race_id ORDER BY p.exhibition_time ASC) as rk
            FROM races r
            JOIN race_previews p ON r.race_id = p.race_id
            WHERE p.exhibition_time IS NOT NULL
        ),
        ex_min AS (
            SELECT r.race_id, MIN(p.exhibition_time) as min_ex
            FROM races r
            JOIN race_previews p ON r.race_id = p.race_id
            WHERE p.exhibition_time IS NOT NULL
            GROUP BY r.race_id
        ),
        signals AS (
            SELECT r.race_id,
                   CASE WHEN exr.rk > 1 THEN 1 ELSE 0 END as L1,
                   MAX(CASE WHEN eo.boat_number IN (4,5,6) AND eo.class_number = 1 THEN 1 ELSE 0 END) as L2,
                   CASE WHEN (COALESCE(e1.flying_count,0)+COALESCE(e1.late_count,0)
                            + COALESCE(e2.flying_count,0)+COALESCE(e2.late_count,0)) >= 2 THEN 1 ELSE 0 END as L3,
                   CASE WHEN (p1.exhibition_time - em.min_ex) > 0.10 THEN 1 ELSE 0 END as L4,
                   CASE WHEN p1.wind_speed >= 4 OR p1.wave_height >= 5 THEN 1 ELSE 0 END as L5,
                   res1.boat_number as winning_boat_dummy
            FROM races r
            JOIN race_entries e1 ON r.race_id = e1.race_id AND e1.boat_number = 1
            JOIN race_entries e2 ON r.race_id = e2.race_id AND e2.boat_number = 2
            JOIN race_entries eo ON r.race_id = eo.race_id
            JOIN race_previews p1 ON r.race_id = p1.race_id AND p1.boat_number = 1
            LEFT JOIN ex_rank exr ON r.race_id = exr.race_id AND exr.boat_number = 1
            LEFT JOIN ex_min em ON r.race_id = em.race_id
            LEFT JOIN race_results res1 ON r.race_id = res1.race_id AND res1.finishing_position = 1
            WHERE p1.exhibition_time IS NOT NULL
            GROUP BY r.race_id
        ),
        winners AS (
            SELECT race_id, boat_number as winning_boat
            FROM race_results WHERE finishing_position = 1
        )
        SELECT (s.L1+s.L2+s.L3+s.L4+s.L5) as score,
               COUNT(*) as n,
               AVG(CASE WHEN w.winning_boat = 1 THEN 1.0 ELSE 0.0 END) as p1_win,
               AVG(CASE WHEN w.winning_boat = 2 THEN 1.0 ELSE 0.0 END) as p2_win,
               AVG(CASE WHEN w.winning_boat = 3 THEN 1.0 ELSE 0.0 END) as p3_win,
               AVG(CASE WHEN w.winning_boat IN (4,5,6) THEN 1.0 ELSE 0.0 END) as p456_win
        FROM signals s
        JOIN winners w ON s.race_id = w.race_id
        GROUP BY score
        ORDER BY score
        """
    )
    print(f"{'#L_score':<10} {'n':>10} {'P(boat1)':>10} {'P(boat2)':>10} {'P(boat3)':>10} {'P(4-6)':>10}")
    print("-" * 70)
    for score, n, p1, p2, p3, p456 in cur.fetchall():
        print(f"  {score} signals {n:>10,} {p1:>10.3f} {p2:>10.3f} {p3:>10.3f} {p456:>10.3f}")

    # =========================================================
    # 高スコア時の三連単穴 ROI
    # =========================================================
    print()
    print("=" * 80)
    print("[ROI] 穴スコア >=3 のレースで 2-X-X / 3-X-X 三連単 ROI 概算")
    print("=" * 80)
    cur = conn.execute(
        """
        WITH ex_rank AS (
            SELECT r.race_id, p.boat_number,
                   RANK() OVER (PARTITION BY r.race_id ORDER BY p.exhibition_time ASC) as rk
            FROM races r
            JOIN race_previews p ON r.race_id = p.race_id
            WHERE p.exhibition_time IS NOT NULL
        ),
        ex_min AS (
            SELECT r.race_id, MIN(p.exhibition_time) as min_ex
            FROM races r
            JOIN race_previews p ON r.race_id = p.race_id
            WHERE p.exhibition_time IS NOT NULL
            GROUP BY r.race_id
        ),
        signals AS (
            SELECT r.race_id,
                   (CASE WHEN exr.rk > 1 THEN 1 ELSE 0 END
                  + MAX(CASE WHEN eo.boat_number IN (4,5,6) AND eo.class_number = 1 THEN 1 ELSE 0 END)
                  + CASE WHEN (COALESCE(e1.flying_count,0)+COALESCE(e1.late_count,0)
                             + COALESCE(e2.flying_count,0)+COALESCE(e2.late_count,0)) >= 2 THEN 1 ELSE 0 END
                  + CASE WHEN (p1.exhibition_time - em.min_ex) > 0.10 THEN 1 ELSE 0 END
                  + CASE WHEN p1.wind_speed >= 4 OR p1.wave_height >= 5 THEN 1 ELSE 0 END) as score
            FROM races r
            JOIN race_entries e1 ON r.race_id = e1.race_id AND e1.boat_number = 1
            JOIN race_entries e2 ON r.race_id = e2.race_id AND e2.boat_number = 2
            JOIN race_entries eo ON r.race_id = eo.race_id
            JOIN race_previews p1 ON r.race_id = p1.race_id AND p1.boat_number = 1
            LEFT JOIN ex_rank exr ON r.race_id = exr.race_id AND exr.boat_number = 1
            LEFT JOIN ex_min em ON r.race_id = em.race_id
            WHERE p1.exhibition_time IS NOT NULL
            GROUP BY r.race_id
        ),
        winners AS (
            SELECT race_id, boat_number as winning_boat
            FROM race_results WHERE finishing_position = 1
        )
        SELECT
            CASE WHEN s.score >= 3 THEN 'high (>=3)' ELSE 'low (<3)' END as flag,
            COUNT(*) as n,
            AVG(CASE WHEN w.winning_boat IN (2,3,4,5,6) THEN 1.0 ELSE 0.0 END) as p_longshot_win,
            AVG(CASE WHEN w.winning_boat IN (2,3,4,5,6) THEN COALESCE(pt.payout,0) ELSE 0 END) as avg_3sound_when_longshot,
            AVG(COALESCE(pt.payout, 0)) as avg_trifecta_payout
        FROM signals s
        JOIN winners w ON s.race_id = w.race_id
        LEFT JOIN race_payouts pt ON s.race_id = pt.race_id AND pt.bet_type='trifecta'
        GROUP BY flag
        """
    )
    print(f"{'flag':<14} {'n':>10} {'P(穴勝ち)':>12} {'平均三連単払戻':>15}")
    print("-" * 65)
    for flag, n, p, ap_when, ap_avg in cur.fetchall():
        print(f"  {flag:<12} {n:>10,} {p:>12.3f} {ap_avg or 0:>15,.0f}")

    conn.close()


if __name__ == "__main__":
    main()
