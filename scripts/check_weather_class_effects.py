"""
風・水面条件と階級・経験の交互作用を検証

仮説:
  1. 追い風 → イン (1号艇) 有利
  2. 向かい風 → 差し・まくり決まる (1号艇不利)
  3. 強風・高波 → ベテラン・上位級が有利、若手・格下はミス
  4. 風弱い穏やか水面 → 階級差が出にくい (どの級でも実力どおり)

検証:
  - 風速階層 × 1号艇 1着率
  - 波高階層 × 1号艇 1着率
  - 風速 × 級別 (A1/A2/B1/B2) の交互作用
  - 風速 × 年齢階層 の交互作用
"""
import sqlite3

DB = "data/boatrace.db"


def main():
    conn = sqlite3.connect(DB)

    # =========================================================
    # 検証1: 風速階層 × 1号艇 1着率・ROI
    # =========================================================
    print("=" * 80)
    print("[Test 1] Wind speed effect on boat 1")
    print("=" * 80)
    cur = conn.execute(
        """
        SELECT
            CASE
                WHEN p.wind_speed = 0 THEN '0m (calm)'
                WHEN p.wind_speed BETWEEN 1 AND 2 THEN '1-2m (light)'
                WHEN p.wind_speed BETWEEN 3 AND 4 THEN '3-4m (mid)'
                WHEN p.wind_speed BETWEEN 5 AND 6 THEN '5-6m (strong)'
                WHEN p.wind_speed >= 7 THEN '7m+ (very strong)'
                ELSE 'NULL'
            END as wspd_tier,
            COUNT(*) as n,
            AVG(CASE WHEN res.finishing_position=1 THEN 1.0 ELSE 0.0 END) as wr,
            AVG(CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout,0) ELSE 0 END) as ap
        FROM races r
        JOIN race_previews p ON r.race_id = p.race_id AND p.boat_number = 1
        JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = 1
        LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='win' AND pp.combination='1'
        WHERE p.wind_speed IS NOT NULL
        GROUP BY wspd_tier
        ORDER BY MIN(p.wind_speed)
        """
    )
    print(f"{'Wind speed':<22} {'n':>8} {'boat1_winR':>12} {'avg_pay':>10} {'ROI':>10}")
    print("-" * 70)
    for tier, n, wr, ap in cur.fetchall():
        roi = (ap or 0)/100 - 1
        print(f"{tier:<22} {n:>8,} {wr:>12.3f} {ap or 0:>10.1f} {roi:>+10.2%}")

    # =========================================================
    # 検証2: 波高階層 × 1号艇 1着率・ROI
    # =========================================================
    print()
    print("=" * 80)
    print("[Test 2] Wave height effect on boat 1")
    print("=" * 80)
    cur = conn.execute(
        """
        SELECT
            CASE
                WHEN p.wave_height = 0 THEN '0cm (calm)'
                WHEN p.wave_height BETWEEN 1 AND 3 THEN '1-3cm (light)'
                WHEN p.wave_height BETWEEN 4 AND 6 THEN '4-6cm (mid)'
                WHEN p.wave_height BETWEEN 7 AND 10 THEN '7-10cm (rough)'
                WHEN p.wave_height >= 11 THEN '11cm+ (very rough)'
                ELSE 'NULL'
            END as wh_tier,
            COUNT(*) as n,
            AVG(CASE WHEN res.finishing_position=1 THEN 1.0 ELSE 0.0 END) as wr,
            AVG(CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout,0) ELSE 0 END) as ap
        FROM races r
        JOIN race_previews p ON r.race_id = p.race_id AND p.boat_number = 1
        JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = 1
        LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='win' AND pp.combination='1'
        WHERE p.wave_height IS NOT NULL
        GROUP BY wh_tier
        ORDER BY MIN(p.wave_height)
        """
    )
    print(f"{'Wave height':<22} {'n':>8} {'boat1_winR':>12} {'avg_pay':>10} {'ROI':>10}")
    print("-" * 70)
    for tier, n, wr, ap in cur.fetchall():
        roi = (ap or 0)/100 - 1
        print(f"{tier:<22} {n:>8,} {wr:>12.3f} {ap or 0:>10.1f} {roi:>+10.2%}")

    # =========================================================
    # 検証3: 風速 × 級別 (1号艇)
    # =========================================================
    print()
    print("=" * 80)
    print("[Test 3] Wind speed * Class interaction (boat 1)")
    print("=" * 80)
    class_names = {1: 'A1', 2: 'A2', 3: 'B1', 4: 'B2'}
    cur = conn.execute(
        """
        SELECT
            CASE
                WHEN p.wind_speed <= 2 THEN 'light(0-2m)'
                WHEN p.wind_speed <= 4 THEN 'mid(3-4m)'
                WHEN p.wind_speed <= 6 THEN 'strong(5-6m)'
                ELSE 'very_strong(7m+)'
            END as wspd_tier,
            e.class_number as cls,
            COUNT(*) as n,
            AVG(CASE WHEN res.finishing_position=1 THEN 1.0 ELSE 0.0 END) as wr,
            AVG(CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout,0) ELSE 0 END) as ap
        FROM races r
        JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
        JOIN race_previews p ON r.race_id = p.race_id AND p.boat_number = 1
        JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = 1
        LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='win' AND pp.combination='1'
        WHERE p.wind_speed IS NOT NULL AND e.class_number IS NOT NULL
        GROUP BY wspd_tier, cls
        HAVING n >= 200
        ORDER BY MIN(p.wind_speed), cls
        """
    )
    print(f"{'Wind':<18} {'Class':<6} {'n':>8} {'winR':>8} {'ROI':>10}")
    print("-" * 55)
    for tier, cls, n, wr, ap in cur.fetchall():
        roi = (ap or 0)/100 - 1
        print(f"{tier:<18} {class_names.get(cls, cls):<6} {n:>8,} {wr:>8.3f} {roi:>+10.2%}")

    # =========================================================
    # 検証4: 風速 × 年齢階層 (1号艇)
    # =========================================================
    print()
    print("=" * 80)
    print("[Test 4] Wind speed * Age interaction (boat 1)")
    print("=" * 80)
    cur = conn.execute(
        """
        SELECT
            CASE
                WHEN p.wind_speed <= 2 THEN 'light'
                WHEN p.wind_speed <= 4 THEN 'mid'
                WHEN p.wind_speed <= 6 THEN 'strong'
                ELSE 'very_strong'
            END as wspd_tier,
            CASE
                WHEN e.age < 30 THEN '20s'
                WHEN e.age < 40 THEN '30s'
                WHEN e.age < 50 THEN '40s'
                WHEN e.age < 60 THEN '50s'
                ELSE '60+'
            END as age_tier,
            COUNT(*) as n,
            AVG(CASE WHEN res.finishing_position=1 THEN 1.0 ELSE 0.0 END) as wr
        FROM races r
        JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
        JOIN race_previews p ON r.race_id = p.race_id AND p.boat_number = 1
        JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = 1
        WHERE p.wind_speed IS NOT NULL AND e.age IS NOT NULL
        GROUP BY wspd_tier, age_tier
        HAVING n >= 200
        ORDER BY MIN(p.wind_speed), MIN(e.age)
        """
    )
    print(f"{'Wind':<14} {'Age':<6} {'n':>8} {'winR':>8}")
    print("-" * 40)
    for tier, age, n, wr in cur.fetchall():
        print(f"{tier:<14} {age:<6} {n:>8,} {wr:>8.3f}")

    # =========================================================
    # 検証5: 荒れた水面で「6号艇」の活躍 (穴目検証)
    # =========================================================
    print()
    print("=" * 80)
    print("[Test 5] Boat 6 in rough conditions (longshot opportunity)")
    print("=" * 80)
    cur = conn.execute(
        """
        SELECT
            CASE
                WHEN p.wind_speed <= 2 AND p.wave_height <= 3 THEN 'calm'
                WHEN p.wind_speed >= 5 OR p.wave_height >= 7 THEN 'rough'
                ELSE 'mid'
            END as cond,
            p.boat_number as boat,
            COUNT(*) as n,
            AVG(CASE WHEN res.finishing_position=1 THEN 1.0 ELSE 0.0 END) as wr,
            AVG(CASE WHEN res.finishing_position<=3 THEN 1.0 ELSE 0.0 END) as top3r
        FROM races r
        JOIN race_previews p ON r.race_id = p.race_id
        JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = p.boat_number
        WHERE p.wind_speed IS NOT NULL AND p.wave_height IS NOT NULL
        GROUP BY cond, boat
        HAVING n >= 200
        ORDER BY cond, boat
        """
    )
    print(f"{'Condition':<10} {'Boat':>5} {'n':>10} {'winR':>8} {'top3R':>8}")
    print("-" * 50)
    for cond, boat, n, wr, top3 in cur.fetchall():
        print(f"{cond:<10} {boat:>5} {n:>10,} {wr:>8.3f} {top3:>8.3f}")

    conn.close()


if __name__ == "__main__":
    main()
