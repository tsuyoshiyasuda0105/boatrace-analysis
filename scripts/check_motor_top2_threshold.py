"""
モーター 2連対率の閾値（30% / 40%）の予測力検証。

仮説:
  - 40%超: 調子の良いモーター → 1着率高くROI改善
  - 30%未満: 調子の悪いモーター → 1着率低くROI悪化
  - これが事実なら、シンプルなフィルタとして有用

検証:
  - assigned_motor_top_2_percent の値別に 1号艇単勝 ROI を集計
  - 加えて 1号艇1着 / 連対 (1-2着) の的中率も見る
"""
import sqlite3

DB = "data/boatrace.db"


def main():
    conn = sqlite3.connect(DB)

    # 1号艇のモーター連対率別の集計
    print("=" * 80)
    print("【検証1】1号艇モーター 2連対率の階層別 ROI")
    print("=" * 80)

    bins = [
        ("0-25%", 0, 25),
        ("25-30%", 25, 30),
        ("30-35%", 30, 35),
        ("35-40%", 35, 40),
        ("40-45%", 40, 45),
        ("45-50%", 45, 50),
        ("50%+",   50, 100),
    ]

    print(f"{'階層':<10} {'n':>8} {'1go_winR':>10} {'top2_R':>10} "
          f"{'avg_pay_win':>13} {'ROI(単勝)':>10} {'avg_pay_2chk':>14} {'ROI(複勝)':>10}")
    print("-" * 95)

    for label, lo, hi in bins:
        cur = conn.execute(
            """
            SELECT COUNT(*) as n,
                   AVG(CASE WHEN res.finishing_position=1 THEN 1.0 ELSE 0.0 END) as wr,
                   AVG(CASE WHEN res.finishing_position<=2 THEN 1.0 ELSE 0.0 END) as top2r,
                   AVG(CASE WHEN res.finishing_position=1 THEN COALESCE(pw.payout, 0) ELSE 0 END) as ap_win,
                   AVG(CASE WHEN res.finishing_position<=2 THEN COALESCE(ppl.payout, 0) ELSE 0 END) as ap_place
            FROM races r
            JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
            JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = 1
            LEFT JOIN race_payouts pw  ON pw.race_id = r.race_id AND pw.bet_type='win'   AND pw.combination='1'
            LEFT JOIN race_payouts ppl ON ppl.race_id = r.race_id AND ppl.bet_type='place' AND ppl.combination='1'
            WHERE e.assigned_motor_top_2_percent >= ? AND e.assigned_motor_top_2_percent < ?
            """,
            (lo, hi),
        )
        n, wr, top2r, ap_win, ap_place = cur.fetchone()
        if n == 0:
            continue
        roi_win = (ap_win or 0) / 100.0 - 1.0
        roi_place = (ap_place or 0) / 100.0 - 1.0
        print(f"{label:<10} {n:>8,} {wr:>10.3f} {top2r:>10.3f} "
              f"{ap_win or 0:>13.1f} {roi_win:>+10.2%} "
              f"{ap_place or 0:>14.1f} {roi_place:>+10.2%}")

    # 30%/40% の閾値での実用フィルタ評価
    print()
    print("=" * 80)
    print("【検証2】「40%超なら買う / 30%未満なら避ける」戦略の効果")
    print("=" * 80)

    scenarios = [
        ("全レース",                  "1=1"),
        ("Motor 30%+ のみ (悪除外)",  "e.assigned_motor_top_2_percent >= 30"),
        ("Motor 35%+ のみ",            "e.assigned_motor_top_2_percent >= 35"),
        ("Motor 40%+ のみ (好調)",     "e.assigned_motor_top_2_percent >= 40"),
        ("Motor 45%+ のみ (絶好調)",    "e.assigned_motor_top_2_percent >= 45"),
        ("Motor < 30% (調子悪)",       "e.assigned_motor_top_2_percent < 30"),
    ]

    print(f"{'戦略':<32} {'n':>10} {'1着率':>8} {'avg_pay':>10} {'ROI':>10}")
    print("-" * 80)
    for label, where in scenarios:
        cur = conn.execute(
            f"""
            SELECT COUNT(*) as n,
                   AVG(CASE WHEN res.finishing_position=1 THEN 1.0 ELSE 0.0 END) as wr,
                   AVG(CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout, 0) ELSE 0 END) as ap
            FROM races r
            JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
            JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = 1
            LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='win' AND pp.combination='1'
            WHERE {where}
            """
        )
        n, wr, ap = cur.fetchone()
        roi = (ap or 0) / 100.0 - 1.0
        print(f"{label:<32} {n:>10,} {wr:>8.3f} {ap or 0:>10.1f} {roi:>+10.2%}")

    # Sweet Spot 戦略 + モーター 40%+ の組合せ
    print()
    print("=" * 80)
    print("【検証3】Sweet Spot 4会場除外 × モーター 40%+ 重ね掛け")
    print("=" * 80)
    scenarios = [
        ("Sweet Spot (4会場除外)",
         "r.stadium_number NOT IN (2,7,10,21)"),
        ("Sweet Spot + Motor 30%+",
         "r.stadium_number NOT IN (2,7,10,21) AND e.assigned_motor_top_2_percent >= 30"),
        ("Sweet Spot + Motor 35%+",
         "r.stadium_number NOT IN (2,7,10,21) AND e.assigned_motor_top_2_percent >= 35"),
        ("Sweet Spot + Motor 40%+",
         "r.stadium_number NOT IN (2,7,10,21) AND e.assigned_motor_top_2_percent >= 40"),
        ("Sweet Spot + Motor 45%+",
         "r.stadium_number NOT IN (2,7,10,21) AND e.assigned_motor_top_2_percent >= 45"),
    ]
    print(f"{'戦略':<35} {'n':>10} {'1着率':>8} {'avg_pay':>10} {'ROI':>10}")
    print("-" * 80)
    for label, where in scenarios:
        cur = conn.execute(
            f"""
            SELECT COUNT(*) as n,
                   AVG(CASE WHEN res.finishing_position=1 THEN 1.0 ELSE 0.0 END) as wr,
                   AVG(CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout, 0) ELSE 0 END) as ap
            FROM races r
            JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
            JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = 1
            LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='win' AND pp.combination='1'
            WHERE {where}
            """
        )
        n, wr, ap = cur.fetchone()
        roi = (ap or 0) / 100.0 - 1.0
        print(f"{label:<35} {n:>10,} {wr:>8.3f} {ap or 0:>10.1f} {roi:>+10.2%}")

    conn.close()


if __name__ == "__main__":
    main()
