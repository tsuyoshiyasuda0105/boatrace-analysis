"""L4 戦略の全期間バックテスト + 資金カーブグラフ生成

実データ範囲: 2022-05-08 〜 2026-05-14 (約4年分、6年には届かないが過去最長)

買い目: 三連単 1-2-3 を 1点100円
戦略バリエーション:
  - L4 基本 (1号艇A1 + 本命500-1000 + B除外)
  - L4+ (上記 + 国1%>=7.0)
  - L4++ (上記 + 局1%>=7.0)
  - L4派生 A2 (1号艇A2 + 本命500-1000 + B除外)

出力:
  data/backtest/l4_capital_curve.png : 資金カーブ図
  data/backtest/l4_summary.txt        : 統計サマリ
  data/backtest/l4_monthly.csv        : 月次集計
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

# 日本語フォント設定 (Windows: Yu Gothic, Mac: Hiragino, Linux: Noto Sans CJK)
for f in ["Yu Gothic UI", "Yu Gothic", "Meiryo", "MS Gothic",
          "Hiragino Sans", "Noto Sans CJK JP"]:
    try:
        from matplotlib.font_manager import findfont, FontProperties
        if findfont(FontProperties(family=f), fallback_to_default=False):
            plt.rcParams["font.family"] = f
            break
    except Exception:
        continue
plt.rcParams["axes.unicode_minus"] = False  # マイナス記号文字化け回避

from src.db.connection import connect

OUT_DIR = Path("data/backtest")
OUT_DIR.mkdir(parents=True, exist_ok=True)

EXCLUDE = {2, 4, 7, 8, 10, 19, 21, 24}


def fetch_all_l4_races():
    """全期間の L4 該当レース (1号艇クラス + 選手成績 + 結果) を取得"""
    print("DB から全期間データ取得中...")
    conn = connect()
    cur = conn.execute("""
        SELECT r.race_date,
               r.stadium_number,
               r.race_grade_number,
               e.class_number,
               e.national_top_1_percent,
               e.local_top_1_percent,
               pp.min_pay AS fav,
               res1.boat_number AS w1,
               res2.boat_number AS w2,
               res3.boat_number AS w3,
               pt.payout AS tri_pay
        FROM races r
        LEFT JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
        JOIN (SELECT race_id, MIN(payout) AS min_pay FROM race_payouts
              WHERE bet_type='trifecta' GROUP BY race_id) pp ON pp.race_id = r.race_id
        LEFT JOIN race_results res1 ON res1.race_id = r.race_id AND res1.finishing_position=1
        LEFT JOIN race_results res2 ON res2.race_id = r.race_id AND res2.finishing_position=2
        LEFT JOIN race_results res3 ON res3.race_id = r.race_id AND res3.finishing_position=3
        LEFT JOIN race_payouts pt ON pt.race_id = r.race_id
                                  AND pt.bet_type='trifecta'
                                  AND pt.combination='1-2-3'
        ORDER BY r.race_date
    """)
    rows = cur.fetchall()
    conn.close()
    print(f"  全レース取得: {len(rows):,}")

    # L4 条件でフィルタ
    results = []
    for row in rows:
        (rdate, stadium, grade, cls, natl, local, fav,
         w1, w2, w3, tri_pay) = row
        if fav is None or not (500 <= fav < 1000):
            continue
        if stadium in EXCLUDE:
            continue
        if cls not in (1, 2):
            continue
        if w1 is None:  # まだ結果が出ていないレース除外
            continue
        try:
            n1 = float(natl) if natl is not None else 0.0
            l1 = float(local) if local is not None else 0.0
        except (TypeError, ValueError):
            n1 = l1 = 0.0
        is_hit_tri = (w1 == 1 and w2 == 2 and w3 == 3)
        payout = (tri_pay or 0) if is_hit_tri else 0
        results.append({
            "date": rdate,
            "class": cls,
            "natl": n1,
            "local": l1,
            "is_hit": is_hit_tri,
            "payout": payout,
        })
    print(f"  L4 条件マッチ: {len(results):,}")
    return results


def categorize_strategy(r):
    """各レースに該当する戦略タグのリストを返す"""
    tags = []
    if r["class"] == 1:
        tags.append("L4_base_A1")
        if r["natl"] >= 7.0:
            tags.append("L4_plus")
        if r["natl"] >= 7.0 and r["local"] >= 7.0:
            tags.append("L4_plus_plus")
    elif r["class"] == 2:
        tags.append("L4_a2")
    return tags


def simulate(races, strategy_tag, bet_per_race=100):
    """指定戦略でシミュレーション。日次データを返す。"""
    by_date = defaultdict(lambda: {"bets": 0, "hits": 0, "payout": 0})
    for r in races:
        if strategy_tag not in categorize_strategy(r):
            continue
        d = by_date[r["date"]]
        d["bets"] += 1
        if r["is_hit"]:
            d["hits"] += 1
            d["payout"] += r["payout"]
    # 連続日付に展開
    sorted_dates = sorted(by_date.keys())
    if not sorted_dates:
        return []
    first = datetime.fromisoformat(sorted_dates[0]).date()
    last = datetime.fromisoformat(sorted_dates[-1]).date()
    cur = first
    daily = []
    cumulative_profit = 0
    while cur <= last:
        ds = cur.isoformat()
        d = by_date.get(ds, {"bets": 0, "hits": 0, "payout": 0})
        cost = d["bets"] * bet_per_race
        profit = d["payout"] - cost
        cumulative_profit += profit
        daily.append({
            "date": cur,
            "bets": d["bets"],
            "hits": d["hits"],
            "payout": d["payout"],
            "cost": cost,
            "profit": profit,
            "cumulative_profit": cumulative_profit,
        })
        cur += timedelta(days=1)
    return daily


def compute_stats(daily, strategy_name):
    if not daily:
        return None
    n_bets_total = sum(d["bets"] for d in daily)
    n_hits_total = sum(d["hits"] for d in daily)
    cost_total = n_bets_total * 100
    payout_total = sum(d["payout"] for d in daily)
    profit_total = payout_total - cost_total
    roi = (payout_total / cost_total * 100) if cost_total else 0
    # 最大ドローダウン
    peak = 0
    max_dd = 0
    for d in daily:
        if d["cumulative_profit"] > peak:
            peak = d["cumulative_profit"]
        dd = peak - d["cumulative_profit"]
        if dd > max_dd:
            max_dd = dd
    # 連敗: 連続赤字 (profit<0) 日数の最長
    cur_streak = 0
    max_streak = 0
    for d in daily:
        if d["bets"] == 0:
            continue
        if d["profit"] < 0:
            cur_streak += 1
            max_streak = max(max_streak, cur_streak)
        else:
            cur_streak = 0
    n_days_active = sum(1 for d in daily if d["bets"] > 0)
    return {
        "strategy": strategy_name,
        "first_date": daily[0]["date"],
        "last_date": daily[-1]["date"],
        "n_days": (daily[-1]["date"] - daily[0]["date"]).days + 1,
        "n_days_active": n_days_active,
        "n_bets": n_bets_total,
        "n_hits": n_hits_total,
        "hit_rate": (n_hits_total / n_bets_total * 100) if n_bets_total else 0,
        "cost": cost_total,
        "payout": payout_total,
        "profit": profit_total,
        "roi": roi,
        "max_dd": max_dd,
        "max_loss_streak_days": max_streak,
        "final_capital": profit_total,  # 元手0からの累積利益
    }


def main():
    races = fetch_all_l4_races()
    if not races:
        print("L4 該当レースなし")
        return

    strategies = [
        ("L4_base_A1", "L4 基本 (1号艇A1)", "#4ade80"),
        ("L4_plus", "L4+ (国1%≥7)", "#a3a3a3"),
        ("L4_plus_plus", "L4++ (国×局≥7)", "#fbbf24"),
        ("L4_a2", "L4派生 A2", "#9ca3af"),
    ]

    all_daily = {}
    all_stats = []
    for tag, label, color in strategies:
        daily = simulate(races, tag)
        all_daily[tag] = daily
        stats = compute_stats(daily, label)
        if stats:
            all_stats.append(stats)

    # ========== グラフ生成 ==========
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 9),
                                    gridspec_kw={"height_ratios": [3, 1]})
    fig.patch.set_facecolor("#1a1a1a")

    # 上: 累積資金カーブ
    ax1.set_facecolor("#1a1a1a")
    for tag, label, color in strategies:
        daily = all_daily[tag]
        if not daily:
            continue
        dates = [d["date"] for d in daily]
        cumprofit = [d["cumulative_profit"] for d in daily]
        ax1.plot(dates, cumprofit, label=label, color=color, linewidth=2)
    ax1.axhline(0, color="#666", linestyle="--", linewidth=1)
    ax1.set_ylabel("累積損益 (円)", color="white", fontsize=12)
    ax1.set_title(f"L4 戦略 資金カーブ ({all_stats[0]['first_date']} 〜 {all_stats[0]['last_date']}, 1点100円ベース)",
                  color="white", fontsize=14, pad=15)
    ax1.tick_params(colors="white")
    ax1.grid(True, alpha=0.15, color="white")
    ax1.legend(loc="upper left", fontsize=11, framealpha=0.8)
    ax1.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    for spine in ax1.spines.values():
        spine.set_color("#555")

    # 下: 日次損益 (L4 基本のみ)
    ax2.set_facecolor("#1a1a1a")
    base_daily = all_daily.get("L4_base_A1", [])
    if base_daily:
        dates = [d["date"] for d in base_daily]
        profits = [d["profit"] for d in base_daily]
        colors = ["#22c55e" if p >= 0 else "#ef4444" for p in profits]
        ax2.bar(dates, profits, color=colors, width=1.0, alpha=0.7)
    ax2.axhline(0, color="#888", linewidth=0.5)
    ax2.set_ylabel("日次損益 (円)", color="white", fontsize=11)
    ax2.set_title("L4 基本戦略 日次損益", color="white", fontsize=11)
    ax2.tick_params(colors="white")
    ax2.grid(True, alpha=0.1, color="white")
    ax2.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    for spine in ax2.spines.values():
        spine.set_color("#555")

    plt.tight_layout()
    out_png = OUT_DIR / "l4_capital_curve.png"
    plt.savefig(out_png, dpi=120, facecolor="#1a1a1a")
    print(f"\n✅ グラフ保存: {out_png}")

    # ========== サマリ書き出し ==========
    out_txt = OUT_DIR / "l4_summary.txt"
    with open(out_txt, "w", encoding="utf-8") as f:
        f.write("=" * 70 + "\n")
        f.write(f"L4 戦略 全期間バックテスト サマリ\n")
        f.write("=" * 70 + "\n\n")
        for s in all_stats:
            f.write(f"■ {s['strategy']}\n")
            f.write(f"  期間          : {s['first_date']} 〜 {s['last_date']} "
                    f"({s['n_days']} 日, うちレース日 {s['n_days_active']})\n")
            f.write(f"  該当レース    : {s['n_bets']:,} 件\n")
            f.write(f"  3連単HIT      : {s['n_hits']:,} 件 ({s['hit_rate']:.1f}%)\n")
            f.write(f"  投資総額      : {s['cost']:,} 円\n")
            f.write(f"  払戻総額      : {s['payout']:,} 円\n")
            f.write(f"  通算損益      : {s['profit']:+,} 円\n")
            f.write(f"  ROI           : {s['roi']:.1f}% (回収率)\n")
            f.write(f"  期待値        : {s['roi']-100:+.1f}% (利益率)\n")
            f.write(f"  最大ドローダウン: -{s['max_dd']:,} 円\n")
            f.write(f"  最長連敗日数   : {s['max_loss_streak_days']} 日\n")
            f.write(f"  最終資金 (元手0): {s['final_capital']:+,} 円\n")
            f.write("\n")

    # コンソール出力
    print("\n" + "=" * 70)
    print("L4 戦略 全期間バックテスト サマリ")
    print("=" * 70)
    for s in all_stats:
        print(f"\n■ {s['strategy']}")
        print(f"  期間          : {s['first_date']} 〜 {s['last_date']} ({s['n_days']} 日)")
        print(f"  該当レース    : {s['n_bets']:,} 件")
        print(f"  3連単HIT      : {s['n_hits']:,} 件 ({s['hit_rate']:.1f}%)")
        print(f"  通算損益      : {s['profit']:+,} 円  ←元手0からの利益")
        print(f"  ROI           : {s['roi']:.1f}% (回収率)")
        print(f"  最大ドローダウン: -{s['max_dd']:,} 円")
        print(f"  最長連敗      : {s['max_loss_streak_days']} 日")

    print(f"\nグラフ: {out_png}")
    print(f"テキストサマリ: {out_txt}")


if __name__ == "__main__":
    main()
