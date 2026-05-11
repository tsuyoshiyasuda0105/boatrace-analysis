"""
モデル予測 prob_first と「実際の三連単1番人気帯」の相関検証

目的: 事前判定 (preds[0].prob_first) が事後の「+EV ゾーン」を予測できるか確認
"""
import sqlite3

DB = "data/boatrace.db"


def main():
    conn = sqlite3.connect(DB)

    # predictions テーブルがあるか確認
    cur = conn.execute("SELECT COUNT(*), COUNT(DISTINCT race_id) FROM predictions WHERE boat_number=1")
    print("predictions table:", cur.fetchone())

    # boat1 の予測確率と三連単1番人気の払戻金額の関係
    cur = conn.execute("""
        WITH min_tri AS (
            SELECT race_id, MIN(payout) as min_p
            FROM race_payouts WHERE bet_type='trifecta' GROUP BY race_id
        ),
        pred1 AS (
            SELECT race_id, MAX(prob_first) as p1
            FROM predictions WHERE boat_number = 1
            GROUP BY race_id
        )
        SELECT
            CASE
                WHEN p.p1 >= 0.85 THEN 'A: p1>=85%'
                WHEN p.p1 >= 0.75 THEN 'B: 75-85%'
                WHEN p.p1 >= 0.65 THEN 'C: 65-75%'
                WHEN p.p1 >= 0.55 THEN 'D: 55-65%'
                WHEN p.p1 >= 0.45 THEN 'E: 45-55%'
                ELSE 'F: <45%'
            END as p1_tier,
            COUNT(*) as n,
            AVG(mt.min_p) as avg_fav_payout,
            -- +EV ゾーン (<2000) に入る割合
            AVG(CASE WHEN mt.min_p < 2000 THEN 1.0 ELSE 0.0 END) as p_ev_zone,
            -- 完全 +EV ゾーン (500-1000)
            AVG(CASE WHEN mt.min_p >= 500 AND mt.min_p < 1000 THEN 1.0 ELSE 0.0 END) as p_500_1000,
            AVG(CASE WHEN res.finishing_position = 1 THEN 1.0 ELSE 0.0 END) as boat1_winrate,
            AVG(CASE WHEN res.finishing_position = 1 THEN COALESCE(pp.payout, 0) ELSE 0 END) as avg_win_pay
        FROM pred1 p
        JOIN min_tri mt ON p.race_id = mt.race_id
        JOIN race_results res ON p.race_id = res.race_id AND res.boat_number = 1
        LEFT JOIN race_payouts pp ON pp.race_id = p.race_id AND pp.bet_type='win' AND pp.combination='1'
        GROUP BY p1_tier
        ORDER BY p1_tier
    """)
    print()
    print("=" * 100)
    print("モデル予測 (1号艇1着率) 別の市場結果")
    print("=" * 100)
    print(f"{'tier':<12} {'n':>8} {'avg本命払戻':>12} {'P(<2k)':>9} {'P(500-1k)':>12} {'win率':>8} {'avg配当':>10} {'ROI':>10}")
    print("-" * 100)
    for tier, n, avg_p, p_ev, p_500, wr, ap in cur.fetchall():
        roi = (ap or 0) / 100 - 1
        marker = " <<<" if (p_ev or 0) > 0.5 else ""
        print(f"  {tier:<10} {n:>8,} {avg_p or 0:>12,.0f} {p_ev or 0:>9.1%} {p_500 or 0:>12.1%} "
              f"{wr or 0:>8.3f} {ap or 0:>10.1f} {roi:>+10.2%}{marker}")

    conn.close()


if __name__ == "__main__":
    main()
