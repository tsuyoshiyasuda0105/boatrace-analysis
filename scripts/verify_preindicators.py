"""
事前判定指標の検証

目的: モデル不要で、entry data だけから「+EV ゾーンに入りそうなレース」を予測できるか

検証する事前指標:
  - 1号艇の class_number
  - 1号艇の national_top_1_percent
  - 1号艇の local_top_1_percent
"""
import sqlite3

DB = "data/boatrace.db"


def main():
    conn = sqlite3.connect(DB)

    # 1号艇の class × 三連単1番人気帯
    print("=" * 100)
    print("[A] 1号艇 class 別 三連単1番人気帯 分布")
    print("=" * 100)
    cur = conn.execute("""
        WITH min_tri AS (
            SELECT race_id, MIN(payout) as min_p
            FROM race_payouts WHERE bet_type='trifecta' GROUP BY race_id
        )
        SELECT e.class_number as cls,
               COUNT(*) as n,
               AVG(mt.min_p) as avg_fav_pay,
               AVG(CASE WHEN mt.min_p < 500 THEN 1.0 ELSE 0.0 END) as p_lt500,
               AVG(CASE WHEN mt.min_p >= 500 AND mt.min_p < 1000 THEN 1.0 ELSE 0.0 END) as p_500_1k,
               AVG(CASE WHEN mt.min_p >= 1000 AND mt.min_p < 2000 THEN 1.0 ELSE 0.0 END) as p_1k_2k,
               AVG(CASE WHEN mt.min_p < 2000 THEN 1.0 ELSE 0.0 END) as p_lt2k,
               AVG(CASE WHEN res.finishing_position=1 THEN 1.0 ELSE 0.0 END) as winrate,
               AVG(CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout, 0) ELSE 0 END) as avg_win_pay
        FROM race_entries e
        JOIN min_tri mt ON e.race_id = mt.race_id
        JOIN race_results res ON e.race_id = res.race_id AND res.boat_number = 1
        LEFT JOIN race_payouts pp ON pp.race_id = e.race_id AND pp.bet_type='win' AND pp.combination='1'
        WHERE e.boat_number = 1 AND e.class_number IS NOT NULL
        GROUP BY cls
        ORDER BY cls
    """)
    cls_name = {1: 'A1', 2: 'A2', 3: 'B1', 4: 'B2'}
    print(f"{'class':<8} {'n':>8} {'avg本命払戻':>11} {'P(<500)':>9} {'P(500-1k)':>11} {'P(1k-2k)':>10} {'P(<2k)':>9} {'win率':>8} {'ROI':>10}")
    print("-" * 110)
    for cls, n, avg_p, p1, p2, p3, p_lt, wr, ap in cur.fetchall():
        roi = (ap or 0)/100 - 1
        print(f"  {cls_name.get(cls, '?'):<6} {n:>8,} {avg_p or 0:>11,.0f} {p1 or 0:>9.1%} "
              f"{p2 or 0:>11.1%} {p3 or 0:>10.1%} {p_lt or 0:>9.1%} {wr or 0:>8.3f} {roi:>+10.2%}")

    # national_top_1_percent 別
    print()
    print("=" * 100)
    print("[B] 1号艇 national_top_1_percent 別 三連単1番人気帯")
    print("=" * 100)
    cur = conn.execute("""
        WITH min_tri AS (
            SELECT race_id, MIN(payout) as min_p
            FROM race_payouts WHERE bet_type='trifecta' GROUP BY race_id
        )
        SELECT
            CASE
                WHEN e.national_top_1_percent < 5 THEN 'A: <5%'
                WHEN e.national_top_1_percent < 8 THEN 'B: 5-8%'
                WHEN e.national_top_1_percent < 12 THEN 'C: 8-12%'
                WHEN e.national_top_1_percent < 18 THEN 'D: 12-18%'
                ELSE 'E: 18%+'
            END as tier,
            COUNT(*) as n,
            AVG(mt.min_p) as avg_fav_pay,
            AVG(CASE WHEN mt.min_p < 2000 THEN 1.0 ELSE 0.0 END) as p_lt2k,
            AVG(CASE WHEN mt.min_p >= 500 AND mt.min_p < 1000 THEN 1.0 ELSE 0.0 END) as p_sweet,
            AVG(CASE WHEN res.finishing_position=1 THEN 1.0 ELSE 0.0 END) as winrate,
            AVG(CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout, 0) ELSE 0 END) as avg_win_pay
        FROM race_entries e
        JOIN min_tri mt ON e.race_id = mt.race_id
        JOIN race_results res ON e.race_id = res.race_id AND res.boat_number = 1
        LEFT JOIN race_payouts pp ON pp.race_id = e.race_id AND pp.bet_type='win' AND pp.combination='1'
        WHERE e.boat_number = 1 AND e.national_top_1_percent IS NOT NULL
        GROUP BY tier
        ORDER BY tier
    """)
    print(f"{'tier':<14} {'n':>8} {'avg本命':>10} {'P(<2k)':>9} {'P(500-1k)':>11} {'win率':>8} {'ROI':>10}")
    for tier, n, ap, p2k, ps, wr, awp in cur.fetchall():
        roi = (awp or 0)/100 - 1
        print(f"  {tier:<12} {n:>8,} {ap or 0:>10,.0f} {p2k or 0:>9.1%} {ps or 0:>11.1%} "
              f"{wr or 0:>8.3f} {roi:>+10.2%}")

    # 複合: class × national
    print()
    print("=" * 100)
    print("[C] 複合判定 (class × national_top_1_percent)")
    print("=" * 100)
    cur = conn.execute("""
        WITH min_tri AS (
            SELECT race_id, MIN(payout) as min_p
            FROM race_payouts WHERE bet_type='trifecta' GROUP BY race_id
        )
        SELECT
            e.class_number as cls,
            CASE
                WHEN e.national_top_1_percent < 8 THEN 'low'
                WHEN e.national_top_1_percent < 14 THEN 'mid'
                ELSE 'high'
            END as nat_tier,
            COUNT(*) as n,
            AVG(CASE WHEN mt.min_p < 2000 THEN 1.0 ELSE 0.0 END) as p_lt2k,
            AVG(CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout, 0) ELSE 0 END) as avg_win_pay
        FROM race_entries e
        JOIN min_tri mt ON e.race_id = mt.race_id
        JOIN race_results res ON e.race_id = res.race_id AND res.boat_number = 1
        LEFT JOIN race_payouts pp ON pp.race_id = e.race_id AND pp.bet_type='win' AND pp.combination='1'
        WHERE e.boat_number = 1 AND e.class_number IS NOT NULL
          AND e.national_top_1_percent IS NOT NULL
        GROUP BY cls, nat_tier
        HAVING n >= 500
        ORDER BY cls, nat_tier
    """)
    cls_name = {1: 'A1', 2: 'A2', 3: 'B1', 4: 'B2'}
    print(f"{'class':<8} {'nat_tier':<10} {'n':>8} {'P(<2k)':>9} {'avg配当':>10} {'ROI':>10}")
    print("-" * 70)
    for cls, nt, n, p2k, awp in cur.fetchall():
        roi = (awp or 0)/100 - 1
        marker = " <<< +EV候補" if roi > 0 else ""
        print(f"  {cls_name.get(cls,'?'):<6} {nt:<10} {n:>8,} {p2k or 0:>9.1%} {awp or 0:>10.1f} {roi:>+10.2%}{marker}")

    conn.close()


if __name__ == "__main__":
    main()
