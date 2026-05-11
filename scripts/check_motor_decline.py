"""
モーター不調 (部品交換が必要な状態) の代理検証

代理指標:
  motor_top2_diff_vs_official = 直近の連対率 - 公式発表の連対率
    - 大幅マイナス: 公式値より実態悪い = モーター調子下降 = 部品交換可能性
    - プラス: モーター好調

ただし motor_top2_diff_vs_official は学習時計算で、DB に持ってない。
代わりに以下を見る:
  - 公式 assigned_motor_top_2_percent (絶対値)
  - これと「現在の節での実際の成績」の比較
"""
import sqlite3

DB = "data/boatrace.db"


def main():
    conn = sqlite3.connect(DB)

    # =========================================================
    # 検証1: 1号艇のモーター公式値 と「節初日からの実成績」の乖離
    # =========================================================
    print("=" * 80)
    print("[Test 1] 公式モーター値の絶対値別 1号艇 ROI 再確認 (既出だが)")
    print("=" * 80)

    # 公式値別 ROI (既に確認済だが新基準で)
    cur = conn.execute(
        """
        SELECT
            CASE
                WHEN e.assigned_motor_top_2_percent < 20 THEN 'A: <20% (極低)'
                WHEN e.assigned_motor_top_2_percent < 30 THEN 'B: 20-30%'
                WHEN e.assigned_motor_top_2_percent < 35 THEN 'C: 30-35%'
                WHEN e.assigned_motor_top_2_percent < 40 THEN 'D: 35-40%'
                WHEN e.assigned_motor_top_2_percent < 45 THEN 'E: 40-45%'
                WHEN e.assigned_motor_top_2_percent < 50 THEN 'F: 45-50%'
                ELSE 'G: 50%+ (絶好調)'
            END as tier,
            COUNT(*) as n,
            AVG(CASE WHEN res.finishing_position=1 THEN 1.0 ELSE 0.0 END) as wr,
            AVG(CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout,0) ELSE 0 END) as ap
        FROM races r
        JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
        JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = 1
        LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='win' AND pp.combination='1'
        WHERE e.assigned_motor_top_2_percent IS NOT NULL
        GROUP BY tier
        ORDER BY tier
        """
    )
    print(f"{'tier':<22} {'n':>10} {'1go_winR':>10} {'ROI':>10}")
    for tier, n, wr, ap in cur.fetchall():
        roi = (ap or 0)/100 - 1
        print(f"  {tier:<20} {n:>10,} {wr:>10.3f} {roi:>+10.2%}")

    # =========================================================
    # 検証2: 「節内変化」: 同一節 (連続日) の中で 1号艇 成績変動
    # =========================================================
    print()
    print("=" * 80)
    print("[Test 2] 節内 同モーター連続使用時の成績推移 (部品交換代用)")
    print("=" * 80)
    cur = conn.execute(
        """
        WITH motor_appearances AS (
            SELECT r.race_id, r.race_date, r.stadium_number,
                   e.assigned_motor_number,
                   e.boat_number,
                   res.finishing_position,
                   ROW_NUMBER() OVER (
                       PARTITION BY r.stadium_number, e.assigned_motor_number,
                                    substr(r.race_date, 1, 7)
                       ORDER BY r.race_date, r.race_number
                   ) as nth_in_month
            FROM races r
            JOIN race_entries e ON r.race_id = e.race_id
            JOIN race_results res ON r.race_id = res.race_id AND e.boat_number = res.boat_number
            WHERE e.boat_number = 1
        )
        SELECT
            CASE
                WHEN nth_in_month = 1 THEN '1走目'
                WHEN nth_in_month BETWEEN 2 AND 3 THEN '2-3走目'
                WHEN nth_in_month BETWEEN 4 AND 6 THEN '4-6走目'
                WHEN nth_in_month BETWEEN 7 AND 10 THEN '7-10走目'
                ELSE '11走目以降'
            END as nth_tier,
            COUNT(*) as n,
            AVG(CASE WHEN finishing_position = 1 THEN 1.0 ELSE 0.0 END) as wr
        FROM motor_appearances
        GROUP BY nth_tier
        ORDER BY MIN(nth_in_month)
        """
    )
    print(f"{'節内出走回数':<14} {'n':>10} {'1着率':>10}")
    print("-" * 40)
    for tier, n, wr in cur.fetchall():
        print(f"  {tier:<12} {n:>10,} {wr:>10.3f}")

    # =========================================================
    # 検証3: 同一モーターの前走成績と今回成績の関係
    # =========================================================
    print()
    print("=" * 80)
    print("[Test 3] 同モーターの直近10走勝率 vs 今回1着率 (1号艇)")
    print("=" * 80)
    cur = conn.execute(
        """
        WITH motor_history AS (
            SELECT r.race_id, r.race_date, r.stadium_number,
                   e.assigned_motor_number,
                   res.finishing_position,
                   AVG(CASE WHEN res.finishing_position = 1 THEN 1.0 ELSE 0.0 END) OVER (
                       PARTITION BY r.stadium_number, e.assigned_motor_number
                       ORDER BY r.race_date, r.race_number
                       ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING
                   ) as recent_motor_winrate
            FROM races r
            JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
            JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = 1
        )
        SELECT
            CASE
                WHEN recent_motor_winrate IS NULL THEN 'NULL (新モーター)'
                WHEN recent_motor_winrate < 0.30 THEN 'A: <30% (絶不調)'
                WHEN recent_motor_winrate < 0.45 THEN 'B: 30-45%'
                WHEN recent_motor_winrate < 0.55 THEN 'C: 45-55%'
                WHEN recent_motor_winrate < 0.65 THEN 'D: 55-65%'
                ELSE 'E: 65%+ (絶好調)'
            END as motor_form,
            COUNT(*) as n,
            AVG(CASE WHEN finishing_position = 1 THEN 1.0 ELSE 0.0 END) as wr
        FROM motor_history
        GROUP BY motor_form
        ORDER BY motor_form
        """
    )
    print(f"{'直近10走モーター勝率':<22} {'n':>10} {'今回1着率':>10}")
    print("-" * 50)
    for tier, n, wr in cur.fetchall():
        print(f"  {tier:<20} {n:>10,} {wr:>10.3f}")

    # =========================================================
    # 検証4: 「公式値 - 直近実績」乖離別 ROI
    # =========================================================
    print()
    print("=" * 80)
    print("[Test 4] 公式値 vs 直近10走実績の乖離 別 1号艇 1着率")
    print("=" * 80)
    cur = conn.execute(
        """
        WITH motor_metrics AS (
            SELECT r.race_id, r.race_date,
                   e.assigned_motor_top_2_percent / 100.0 as official_top2,
                   AVG(CASE WHEN res_prev.finishing_position <= 2 THEN 1.0 ELSE 0.0 END) OVER (
                       PARTITION BY r.stadium_number, e.assigned_motor_number
                       ORDER BY r.race_date, r.race_number
                       ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING
                   ) as recent_top2,
                   res.finishing_position
            FROM races r
            JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
            JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = 1
            LEFT JOIN race_results res_prev ON r.race_id = res_prev.race_id
                                            AND res_prev.boat_number = 1
        )
        SELECT
            CASE
                WHEN recent_top2 IS NULL OR official_top2 IS NULL THEN 'NULL'
                WHEN (recent_top2 - official_top2) < -0.20 THEN 'A: 直近<<公式 (急降下)'
                WHEN (recent_top2 - official_top2) < -0.10 THEN 'B: 直近<公式 (下降中)'
                WHEN (recent_top2 - official_top2) <  0.10 THEN 'C: 一致'
                WHEN (recent_top2 - official_top2) <  0.20 THEN 'D: 直近>公式 (上昇中)'
                ELSE 'E: 直近>>公式 (急上昇)'
            END as gap,
            COUNT(*) as n,
            AVG(CASE WHEN finishing_position = 1 THEN 1.0 ELSE 0.0 END) as wr
        FROM motor_metrics
        WHERE recent_top2 IS NOT NULL AND official_top2 IS NOT NULL
        GROUP BY gap
        ORDER BY gap
        """
    )
    print(f"{'公式vs直近 乖離':<28} {'n':>10} {'今回1着率':>10}")
    print("-" * 55)
    for tier, n, wr in cur.fetchall():
        print(f"  {tier:<26} {n:>10,} {wr:>10.3f}")

    conn.close()


if __name__ == "__main__":
    main()
