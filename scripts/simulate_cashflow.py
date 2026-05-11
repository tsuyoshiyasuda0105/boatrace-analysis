"""
Priority F: Cash Flow / Drawdown Monte Carlo シミュレーション

目的: 「+EV 戦略を実運用したら実際にいくら稼げるか + どれだけ損失リスクがあるか」を定量化

シナリオ:
  1. 1号艇単勝のみ (+27.41%)
  2. 3連単 1-2-3 のみ (+44.23%)
  3. 「全 +EV 券種同時買い」(分散)
  4. Kelly 基準でベットサイズ最適化

評価指標:
  - 月収支分布 (100回 Monte Carlo)
  - 最大ドローダウン
  - 必要軍資金 (95%ile of max drawdown)
  - 連敗最長
  - Sharpe ratio
"""
import sqlite3
import random
import statistics
import math
from typing import List, Tuple

DB = "data/boatrace.db"
random.seed(42)


def get_per_race_yields(db: str, strategy: str) -> Tuple[List[float], dict]:
    """
    各レースの (払戻 / 賭け金) を取得。
    戦略によって買い目とコストが異なる。
    """
    conn = sqlite3.connect(db)
    cur = conn.execute("""
        WITH min_tri AS (
            SELECT race_id, MIN(payout) as min_p
            FROM race_payouts WHERE bet_type='trifecta' GROUP BY race_id
        )
        SELECT r.race_id, r.race_date,
               COALESCE(pw.payout, 0) as pay_win,
               COALESCE(pp.payout, 0) as pay_place,
               COALESCE(pe12.payout, 0) as pay_exacta12,
               COALESCE(pq12.payout, 0) as pay_quinella12,
               COALESCE(pt123.payout, 0) as pay_tri123,
               COALESCE(ptr123.payout, 0) as pay_trio123,
               res1.finishing_position as pos1,
               res2.finishing_position as pos2,
               res3.finishing_position as pos3,
               res4.finishing_position as pos4,
               res5.finishing_position as pos5,
               res6.finishing_position as pos6
        FROM races r
        JOIN min_tri mt ON r.race_id = mt.race_id
        LEFT JOIN race_results res1 ON r.race_id=res1.race_id AND res1.boat_number=1
        LEFT JOIN race_results res2 ON r.race_id=res2.race_id AND res2.boat_number=2
        LEFT JOIN race_results res3 ON r.race_id=res3.race_id AND res3.boat_number=3
        LEFT JOIN race_results res4 ON r.race_id=res4.race_id AND res4.boat_number=4
        LEFT JOIN race_results res5 ON r.race_id=res5.race_id AND res5.boat_number=5
        LEFT JOIN race_results res6 ON r.race_id=res6.race_id AND res6.boat_number=6
        LEFT JOIN race_payouts pw ON pw.race_id=r.race_id AND pw.bet_type='win' AND pw.combination='1'
        LEFT JOIN race_payouts pp ON pp.race_id=r.race_id AND pp.bet_type='place' AND pp.combination='1'
        LEFT JOIN race_payouts pe12 ON pe12.race_id=r.race_id AND pe12.bet_type='exacta' AND pe12.combination='1-2'
        LEFT JOIN race_payouts pq12 ON pq12.race_id=r.race_id AND pq12.bet_type='quinella' AND pq12.combination='1=2'
        LEFT JOIN race_payouts pt123 ON pt123.race_id=r.race_id AND pt123.bet_type='trifecta' AND pt123.combination='1-2-3'
        LEFT JOIN race_payouts ptr123 ON ptr123.race_id=r.race_id AND ptr123.bet_type='trio' AND ptr123.combination='1=2=3'
        WHERE r.race_date >= '2026-01-01'
          AND mt.min_p >= 500 AND mt.min_p < 1000
    """)

    yields = []  # 各レースの (払戻 / 賭け金)
    cost_per_race = 0
    win_rates = {}

    for row in cur.fetchall():
        rid, rd, pw, pp_, pe, pq, pt, ptr, p1, p2, p3, p4, p5, p6 = row
        win = (p1 == 1)
        place = (p1 is not None and p1 <= 2)
        exacta12 = (p1 == 1 and p2 == 2)
        quinella12 = (p1 in (1, 2) and p2 in (1, 2)) if (p1 and p2) else False
        # 三連単 1-2-3: 1着=1, 2着=2, 3着=3
        tri123 = (p1 == 1 and p2 == 2 and p3 == 3)
        # 3連複 1=2=3: 1,2,3 全員が 3着以内
        positions = {1: p1, 2: p2, 3: p3, 4: p4, 5: p5, 6: p6}
        boats_top3 = [b for b, pos in positions.items() if pos and pos <= 3]
        trio123 = set([1, 2, 3]).issubset(set(boats_top3))

        if strategy == "win_only":
            cost = 100
            payout = pw if win else 0
        elif strategy == "tri123_only":
            cost = 100
            payout = pt if tri123 else 0
        elif strategy == "all_positive_ev":
            # 6 通り買い: 単勝, 複勝, 2連単 1-2, 2連複 1=2, 3連単 1-2-3, 3連複 1=2=3
            cost = 6 * 100
            payout = 0
            if win: payout += pw
            if place: payout += pp_
            if exacta12: payout += pe
            if quinella12: payout += pq
            if tri123: payout += pt
            if trio123: payout += ptr
        elif strategy == "win_tri123":
            # 単勝 + 3連単 1-2-3 だけ (高EV2点)
            cost = 2 * 100
            payout = 0
            if win: payout += pw
            if tri123: payout += pt
        else:
            cost = 100
            payout = 0

        yields.append(payout / cost)
        cost_per_race = cost

    conn.close()
    return yields, {"cost_per_race": cost_per_race, "n_races": len(yields)}


def simulate_monthly(yields: List[float], n_races_per_month: int = 100,
                     n_months: int = 12, n_sims: int = 1000) -> dict:
    """
    月収支を Monte Carlo シミュレーション

    Each simulation:
      - 12ヶ月にわたって、毎月 n_races_per_month レースをランダムサンプル
      - 月ごとの累積 yield を計算
      - 最大ドローダウンを記録
    """
    monthly_returns = []  # 全シミュ各月の return
    max_drawdowns = []
    longest_losing_streaks = []
    final_returns = []  # 1年後の累積 return

    for sim in range(n_sims):
        cumulative = 0.0
        equity_curve = [0.0]
        peak = 0.0
        max_dd = 0.0
        losing_streak = 0
        max_streak = 0

        for m in range(n_months):
            month_sample = random.choices(yields, k=n_races_per_month)
            month_return = sum(month_sample) - n_races_per_month  # yield - cost ratio
            cumulative += month_return
            equity_curve.append(cumulative)
            if month_return < 0:
                losing_streak += 1
                max_streak = max(max_streak, losing_streak)
            else:
                losing_streak = 0
            peak = max(peak, cumulative)
            dd = peak - cumulative
            max_dd = max(max_dd, dd)

        max_drawdowns.append(max_dd)
        longest_losing_streaks.append(max_streak)
        final_returns.append(cumulative)
        # 月単位の return をすべて集計
        for i in range(1, len(equity_curve)):
            monthly_returns.append(equity_curve[i] - equity_curve[i - 1])

    return {
        "monthly_returns": monthly_returns,
        "max_drawdowns": max_drawdowns,
        "longest_losing_streaks": longest_losing_streaks,
        "final_returns": final_returns,
        "n_sims": n_sims,
        "n_months": n_months,
        "n_races_per_month": n_races_per_month,
    }


def report(label: str, yields: List[float], info: dict, n_races_per_month: int = 100):
    print(f"\n{'=' * 100}")
    print(f"[{label}]")
    print(f"  該当レース数 (年間): {info['n_races']}, 1レース投資: {info['cost_per_race']}円")
    print(f"  ROI 点推定: {(sum(yields)/len(yields) - 1):+.2%}")

    # シミュレーション
    sim = simulate_monthly(yields, n_races_per_month=n_races_per_month, n_months=12, n_sims=1000)

    monthly_rs = sim["monthly_returns"]
    monthly_yen = [r * info["cost_per_race"] for r in monthly_rs]
    final_rs = sim["final_returns"]
    final_yen = [r * info["cost_per_race"] for r in final_rs]
    max_dds = [d * info["cost_per_race"] for d in sim["max_drawdowns"]]
    streaks = sim["longest_losing_streaks"]

    # 月収支統計
    monthly_yen.sort()
    print(f"\n  月収支分布 (n_races/月={n_races_per_month}, 1000シミュ x 12ヶ月):")
    print(f"    平均: ¥{int(statistics.mean(monthly_yen)):>+8,}")
    print(f"    中央: ¥{int(statistics.median(monthly_yen)):>+8,}")
    print(f"    5%ile: ¥{int(monthly_yen[len(monthly_yen)//20]):>+8,}")
    print(f"    25%ile: ¥{int(monthly_yen[len(monthly_yen)//4]):>+8,}")
    print(f"    75%ile: ¥{int(monthly_yen[len(monthly_yen)*3//4]):>+8,}")
    print(f"    95%ile: ¥{int(monthly_yen[len(monthly_yen)*19//20]):>+8,}")
    print(f"    最大: ¥{int(monthly_yen[-1]):>+8,}")
    print(f"    最小: ¥{int(monthly_yen[0]):>+8,}")

    # 年間収支
    final_yen.sort()
    print(f"\n  年間収支 (12ヶ月) 分布:")
    print(f"    平均: ¥{int(statistics.mean(final_yen)):>+8,}")
    print(f"    中央: ¥{int(statistics.median(final_yen)):>+8,}")
    print(f"    5%ile: ¥{int(final_yen[len(final_yen)//20]):>+8,}")
    print(f"    95%ile: ¥{int(final_yen[len(final_yen)*19//20]):>+8,}")

    # 最大ドローダウン
    max_dds.sort()
    print(f"\n  最大ドローダウン分布:")
    print(f"    平均: ¥{int(statistics.mean(max_dds)):>+8,}")
    print(f"    50%ile: ¥{int(max_dds[len(max_dds)//2]):>+8,}")
    print(f"    95%ile: ¥{int(max_dds[len(max_dds)*19//20]):>+8,}")
    print(f"    最大: ¥{int(max_dds[-1]):>+8,}")

    # 必要軍資金 (95%ile DD x 1.5 倍を推奨)
    safe_capital = int(max_dds[len(max_dds)*19//20] * 1.5)
    monthly_cost = info["cost_per_race"] * n_races_per_month
    print(f"\n  推奨軍資金: ¥{safe_capital:,} (月コスト ¥{monthly_cost:,}, 安全係数1.5x)")

    # 連敗
    streaks.sort()
    print(f"\n  連敗月数 (赤字の月の連続):")
    print(f"    平均: {statistics.mean(streaks):.1f}ヶ月")
    print(f"    95%ile: {streaks[len(streaks)*19//20]}ヶ月")
    print(f"    最大: {streaks[-1]}ヶ月")

    # 破産確率 (年間 < 0 の確率)
    p_loss_year = sum(1 for r in final_yen if r < 0) / len(final_yen)
    p_big_loss = sum(1 for r in final_yen if r < -safe_capital/2) / len(final_yen)
    print(f"\n  年間赤字確率: {p_loss_year:.1%}")
    print(f"  年間 50%以上 損失確率: {p_big_loss:.1%}")

    # Sharpe ratio (月単位)
    if statistics.stdev(monthly_yen) > 0:
        sharpe = statistics.mean(monthly_yen) / statistics.stdev(monthly_yen) * math.sqrt(12)
        print(f"\n  Sharpe Ratio (年率): {sharpe:.2f}")


def main():
    print("=" * 100)
    print("Priority F: Cash Flow / Drawdown シミュレーション")
    print("対象: 2026年 + 三連単1番人気500-1000円帯のレース")
    print("=" * 100)

    # 該当レースは 2026年5ヶ月で 3,465 → 年間ペースで約 8,316
    # 月あたり 約 700 レース
    # ユーザーが「全部買う」のは無理なので、月100レースで設定

    strategies = [
        ("単勝1号艇のみ", "win_only", 100),
        ("3連単 1-2-3 のみ", "tri123_only", 50),
        ("単勝 + 3連単1-2-3 (2点買い)", "win_tri123", 80),
        ("全 +EV 6点買い (単+複+2連単+2連複+3連単+3連複)", "all_positive_ev", 50),
    ]

    for label, strategy, n_per_month in strategies:
        yields, info = get_per_race_yields(DB, strategy)
        report(label, yields, info, n_races_per_month=n_per_month)


if __name__ == "__main__":
    main()
