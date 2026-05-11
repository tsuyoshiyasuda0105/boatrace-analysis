"""
チルト角度の詳細検証

専門家知見:
  - 標準 -0.5度 が主流 (多くの選手が変更しない)
  - プラスチルト = 注目シグナル
  - +1.0度以上 = ダッシュからまくり一撃狙い
  - +3.0度 = 大まくり勝負賭け
  - マイナス側深め = 出足重視・ターン安定

検証:
  1. チルト分布の確認 (どの値が多いか)
  2. チルト×コース別 1着率 (プラスチルトで外艇が伸びるか)
  3. チルト+1.0以上の艇の出現と1着率
  4. 4-6コース x プラスチルト の穴目検証
  5. チルト+3.0の極端な例 (n小だが調査)
"""
import sqlite3

DB = "data/boatrace.db"


def main():
    conn = sqlite3.connect(DB)

    # =========================================================
    # Test 1: チルトの実際の分布
    # =========================================================
    print("=" * 80)
    print("[Test 1] チルト調整値の分布 (全艇)")
    print("=" * 80)
    cur = conn.execute(
        """
        SELECT tilt_adjustment, COUNT(*) as n
        FROM race_previews
        WHERE tilt_adjustment IS NOT NULL
        GROUP BY tilt_adjustment
        ORDER BY tilt_adjustment
        """
    )
    rows = cur.fetchall()
    total = sum(n for _, n in rows)
    print(f"{'tilt値':<10} {'n':>10} {'割合':>8}")
    print("-" * 35)
    for tilt, n in rows:
        pct = n / total
        bar = "#" * int(pct * 100)
        print(f"  {tilt:>6}    {n:>10,} {pct:>7.1%}  {bar}")

    # =========================================================
    # Test 2: チルト x コース別 1着率
    # =========================================================
    print()
    print("=" * 80)
    print("[Test 2] チルト x コース別 1着率")
    print("=" * 80)
    cur = conn.execute(
        """
        SELECT
            CASE
                WHEN p.tilt_adjustment <= -0.5 THEN 'A: <=-0.5 (標準/出足)'
                WHEN p.tilt_adjustment <  0.0 THEN 'B: -0.5〜0 (やや出足)'
                WHEN p.tilt_adjustment =  0.0 THEN 'C: 0.0 (フラット)'
                WHEN p.tilt_adjustment <  1.0 THEN 'D: 0〜1.0 (やや伸び)'
                WHEN p.tilt_adjustment <  2.0 THEN 'E: 1.0〜2.0 (まくり)'
                ELSE 'F: 2.0以上 (大まくり)'
            END as tilt_tier,
            p.boat_number as bn,
            COUNT(*) as n,
            AVG(CASE WHEN res.finishing_position = 1 THEN 1.0 ELSE 0.0 END) as wr
        FROM race_previews p
        JOIN race_results res ON p.race_id = res.race_id AND p.boat_number = res.boat_number
        WHERE p.tilt_adjustment IS NOT NULL
        GROUP BY tilt_tier, bn
        HAVING n >= 30
        ORDER BY tilt_tier, bn
        """
    )
    print(f"{'tilt':<28} {'boat':<6} {'n':>8} {'1着率':>10}")
    print("-" * 60)
    for tier, bn, n, wr in cur.fetchall():
        marker = " <<<" if bn >= 4 and wr > 0.15 else ""
        print(f"  {tier:<26} {bn:<6} {n:>8,} {wr:>10.3f}{marker}")

    # =========================================================
    # Test 3: +1.0以上のチルトを設定した艇の出現頻度と1着率
    # =========================================================
    print()
    print("=" * 80)
    print("[Test 3] チルト>=1.0 を設定した艇の特徴 (まくり狙い艇)")
    print("=" * 80)
    cur = conn.execute(
        """
        SELECT p.boat_number as bn,
               COUNT(*) as n,
               AVG(CASE WHEN res.finishing_position = 1 THEN 1.0 ELSE 0.0 END) as wr,
               AVG(CASE WHEN res.finishing_position <= 3 THEN 1.0 ELSE 0.0 END) as top3
        FROM race_previews p
        JOIN race_results res ON p.race_id = res.race_id AND p.boat_number = res.boat_number
        WHERE p.tilt_adjustment >= 1.0
        GROUP BY bn
        ORDER BY bn
        """
    )
    print(f"{'boat':<6} {'n':>10} {'1着率':>10} {'3連対率':>10}")
    print("-" * 50)
    for bn, n, wr, top3 in cur.fetchall():
        # 平均 1着率は艇別に大きく違うのでベースラインも示す
        print(f"  {bn:<4} {n:>10,} {wr:>10.3f} {top3:>10.3f}")

    print()
    print("(参考) チルト無関係の艇別 1着率ベースライン:")
    cur = conn.execute(
        """
        SELECT res.boat_number, COUNT(*) as n,
               AVG(CASE WHEN res.finishing_position = 1 THEN 1.0 ELSE 0.0 END) as wr
        FROM race_results res
        GROUP BY res.boat_number ORDER BY res.boat_number
        """
    )
    print(f"  {'boat':<6} {'n':>10} {'1着率':>10}")
    for bn, n, wr in cur.fetchall():
        print(f"  {bn:<4} {n:>10,} {wr:>10.3f}")

    # =========================================================
    # Test 4: 4-6コース x プラスチルトでの 1着率 & ROI
    # =========================================================
    print()
    print("=" * 80)
    print("[Test 4] 4-6号艇 x チルト別 1着率 & ROI (穴目検証)")
    print("=" * 80)
    cur = conn.execute(
        """
        SELECT p.boat_number as bn,
               CASE
                   WHEN p.tilt_adjustment <= -0.5 THEN 'A: 標準/出足'
                   WHEN p.tilt_adjustment <   0.5 THEN 'B: 中間'
                   WHEN p.tilt_adjustment <   1.5 THEN 'C: 伸び (0.5-1.5)'
                   ELSE 'D: 大まくり (>=1.5)'
               END as tilt_tier,
               COUNT(*) as n,
               AVG(CASE WHEN res.finishing_position = 1 THEN 1.0 ELSE 0.0 END) as wr,
               AVG(CASE WHEN res.finishing_position = 1 THEN COALESCE(pp.payout,0) ELSE 0 END) as ap
        FROM race_previews p
        JOIN race_results res ON p.race_id = res.race_id AND p.boat_number = res.boat_number
        LEFT JOIN race_payouts pp ON pp.race_id = p.race_id
                                  AND pp.bet_type='win'
                                  AND pp.combination = CAST(p.boat_number AS TEXT)
        WHERE p.tilt_adjustment IS NOT NULL
          AND p.boat_number IN (4, 5, 6)
        GROUP BY bn, tilt_tier
        HAVING n >= 30
        ORDER BY bn, tilt_tier
        """
    )
    print(f"{'boat':<6} {'tilt':<25} {'n':>8} {'1着率':>10} {'avg_pay':>10} {'ROI':>10}")
    print("-" * 80)
    for bn, tier, n, wr, ap in cur.fetchall():
        roi = (ap or 0)/100 - 1
        marker = " <<<" if wr > 0.10 and bn >= 4 else ""
        print(f"  {bn:<4} {tier:<23} {n:>8,} {wr:>10.3f} {ap or 0:>10.1f} {roi:>+10.2%}{marker}")

    # =========================================================
    # Test 5: チルト+3.0 の極端な例
    # =========================================================
    print()
    print("=" * 80)
    print("[Test 5] チルト >= 2.5 (極端な大まくり狙い) の結果")
    print("=" * 80)
    cur = conn.execute(
        """
        SELECT p.boat_number as bn, p.tilt_adjustment as tilt,
               COUNT(*) as n,
               AVG(CASE WHEN res.finishing_position = 1 THEN 1.0 ELSE 0.0 END) as wr,
               AVG(CASE WHEN res.finishing_position <= 3 THEN 1.0 ELSE 0.0 END) as top3,
               AVG(CASE WHEN res.finishing_position = 1 THEN COALESCE(pp.payout,0) ELSE 0 END) as ap
        FROM race_previews p
        JOIN race_results res ON p.race_id = res.race_id AND p.boat_number = res.boat_number
        LEFT JOIN race_payouts pp ON pp.race_id = p.race_id
                                  AND pp.bet_type='win'
                                  AND pp.combination = CAST(p.boat_number AS TEXT)
        WHERE p.tilt_adjustment >= 2.5
        GROUP BY bn, tilt
        HAVING n >= 5
        ORDER BY bn, tilt
        """
    )
    print(f"{'boat':<6} {'tilt':<8} {'n':>6} {'1着率':>10} {'3連対率':>10} {'ROI':>10}")
    print("-" * 65)
    for bn, tilt, n, wr, top3, ap in cur.fetchall():
        roi = (ap or 0)/100 - 1
        print(f"  {bn:<4} {tilt:<6} {n:>6} {wr:>10.3f} {top3:>10.3f} {roi:>+10.2%}")

    # =========================================================
    # Test 6: チルトとレース水面の関係 (静水 vs 荒水で勝負狙い変化?)
    # =========================================================
    print()
    print("=" * 80)
    print("[Test 6] 強風時のチルト+1.0以上選手のパフォーマンス")
    print("=" * 80)
    cur = conn.execute(
        """
        SELECT
            CASE
                WHEN p.wind_speed <= 2 THEN '微風'
                WHEN p.wind_speed <= 4 THEN '中風'
                ELSE '強風 (5m+)'
            END as wind_tier,
            p.boat_number as bn,
            COUNT(*) as n,
            AVG(CASE WHEN res.finishing_position = 1 THEN 1.0 ELSE 0.0 END) as wr,
            AVG(CASE WHEN res.finishing_position = 1 THEN COALESCE(pp.payout,0) ELSE 0 END) as ap
        FROM race_previews p
        JOIN race_results res ON p.race_id = res.race_id AND p.boat_number = res.boat_number
        LEFT JOIN race_payouts pp ON pp.race_id = p.race_id
                                  AND pp.bet_type='win'
                                  AND pp.combination = CAST(p.boat_number AS TEXT)
        WHERE p.tilt_adjustment >= 1.0
          AND p.wind_speed IS NOT NULL
          AND p.boat_number IN (4, 5, 6)
        GROUP BY wind_tier, bn
        HAVING n >= 20
        ORDER BY wind_tier, bn
        """
    )
    print(f"{'wind':<14} {'boat':<6} {'n':>6} {'1着率':>10} {'ROI':>10}")
    print("-" * 50)
    for w, bn, n, wr, ap in cur.fetchall():
        roi = (ap or 0)/100 - 1
        print(f"  {w:<12} {bn:<4} {n:>6} {wr:>10.3f} {roi:>+10.2%}")

    conn.close()


if __name__ == "__main__":
    main()
