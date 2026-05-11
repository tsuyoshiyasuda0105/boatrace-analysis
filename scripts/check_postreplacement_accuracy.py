"""
改装直後の予測精度を検証。

仮説:
  - 改装直後 (0-60日) は「motor_long_50」が古いモーターのデータを使う = 完全に間違い
  - 改装後 60-180日 になると、新モーターでの走行が累積し、長期統計が正しくなる
  - もし上記が正しければ、改装後 0-60日 の予測精度が劇的に悪いはず

検証方法:
  - 各レースで「会場の最終改装日からの日数」を計算
  - 日数別に「1号艇1着率の予測誤差」と「ROI」を集計
"""
import sqlite3
from datetime import date, datetime

REPLACEMENT_MONTH = {
    1: 3, 2: 5, 3: 11, 4: 3, 5: 4, 6: 4, 7: 6, 8: 7, 9: 8, 10: 3,
    11: 10, 12: 5, 13: 7, 14: 6, 15: 9, 16: 4, 17: 3, 18: 10, 19: 6,
    20: 4, 21: 11, 22: 2, 23: 12, 24: 7,
}


def days_since_last_replacement(race_date_str: str, stadium: int) -> int:
    """概算: 直近改装月の1日からの経過日数"""
    rd = datetime.strptime(race_date_str, "%Y-%m-%d").date()
    rep_month = REPLACEMENT_MONTH[stadium]
    # 改装日: 同年の改装月1日 or 前年の改装月1日
    cand_this_year = date(rd.year, rep_month, 1)
    cand_prev_year = date(rd.year - 1, rep_month, 1)
    if rd >= cand_this_year:
        last_rep = cand_this_year
    else:
        last_rep = cand_prev_year
    return (rd - last_rep).days


def main():
    conn = sqlite3.connect("data/boatrace.db")
    cur = conn.execute(
        """
        SELECT r.race_date, r.stadium_number,
               res.finishing_position,
               COALESCE(pp.payout, 0) as payout
        FROM races r
        JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
        JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = 1
        LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='win' AND pp.combination='1'
        WHERE r.race_date >= '2023-01-01' AND r.race_date <= '2025-12-31'
        """
    )

    bins = {
        "0-30日 (新品直後)":   {"n": 0, "wins": 0, "payouts": 0},
        "30-60日":             {"n": 0, "wins": 0, "payouts": 0},
        "60-120日":            {"n": 0, "wins": 0, "payouts": 0},
        "120-200日":           {"n": 0, "wins": 0, "payouts": 0},
        "200-300日":           {"n": 0, "wins": 0, "payouts": 0},
        "300日+ (改装直前)":   {"n": 0, "wins": 0, "payouts": 0},
    }

    def bucket(days):
        if days < 30: return "0-30日 (新品直後)"
        if days < 60: return "30-60日"
        if days < 120: return "60-120日"
        if days < 200: return "120-200日"
        if days < 300: return "200-300日"
        return "300日+ (改装直前)"

    for rd, sid, pos, payout in cur.fetchall():
        d = days_since_last_replacement(rd, sid)
        b = bucket(d)
        bins[b]["n"] += 1
        if pos == 1:
            bins[b]["wins"] += 1
            bins[b]["payouts"] += (payout or 0)

    conn.close()
    print("=" * 70)
    print("会場改装からの経過日数別 1号艇単勝 ROI")
    print("=" * 70)
    print(f"{'期間':<22} {'n':>8} {'1着率':>8} {'avg_payout':>12} {'ROI':>10}")
    print("-" * 70)
    for label in ["0-30日 (新品直後)", "30-60日", "60-120日", "120-200日", "200-300日", "300日+ (改装直前)"]:
        b = bins[label]
        if b["n"] == 0:
            continue
        wr = b["wins"] / b["n"]
        avg_payout = b["payouts"] / b["n"]
        roi = avg_payout / 100.0 - 1.0
        print(f"{label:<22} {b['n']:>8,} {wr:>8.3f} {avg_payout:>12.1f} {roi:>+10.2%}")

    print("\n--- 解釈 ---")
    print("もし motor_long_50 が改装後に汚染されているなら、")
    print("'0-30日' や '30-60日' の ROI が他期間より顕著に悪いはず。")
    print("差がないなら、汚染の実質的影響は軽微（市場が織り込んでいる）。")


if __name__ == "__main__":
    main()
