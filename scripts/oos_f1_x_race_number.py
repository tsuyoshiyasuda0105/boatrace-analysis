"""F1 戦略 x race_number 分解 OOS 検証

F1 ベース条件 (一般戦のみ):
  - r.race_grade_number = 5 (一般戦)
  - 1号艇 class_number = 1 (A1)
  - 1号艇 national_top_1_percent >= 7.0
  - 2号艇 national_top_2_percent >= 40.0
  - 三連単本命 (最小オッズ) 500-1000円
  - B除外会場: 戸田(2), 平和島(4), 三国(10), 鳴門(14), 徳山(18), 若松(20), 福岡(22), 唐津(23)
  - 雨除外: race_previews.weather_number != 4
  - 期間: 2022-04-01 〜 2026-03-31
  - 買い目: 三連単 1-2-3 / 100円固定

Pattern 1: race_number 1..12 個別
Pattern 2: 集約 (morning/midday/evening/prime/12R-only/base)
Pattern 3: F1-evening vs L4-evening (全グレード) 比較

Read-only. Bootstrap 95% CI, 2000 iter.
"""
from __future__ import annotations
import sqlite3
import random
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

DB = Path(r"C:/boat_project/boatrace-analysis/data/boatrace.db")

# user spec: 戸田=2, 平和島=4, 三国=10, 鳴門=14, 徳山=18, 若松=20, 福岡=22, 唐津=23
EXCLUDE_STADIUMS = (2, 4, 10, 14, 18, 20, 22, 23)
START = "2022-04-01"
END = "2026-03-31"

# ===========================================================================
# F1 universe: 一般戦 x 1A1 x nat1>=7 x boat2 nat2>=40 x odds500-1000 x ...
# ===========================================================================
F1_SQL = f"""
WITH min_tri AS (
  SELECT race_id, MIN(payout) AS min_pay
    FROM race_payouts
   WHERE bet_type='trifecta'
   GROUP BY race_id
),
pay123 AS (
  SELECT race_id, payout
    FROM race_payouts
   WHERE bet_type='trifecta' AND combination='1-2-3'
),
res1 AS (SELECT race_id, finishing_position FROM race_results WHERE boat_number=1),
res2 AS (SELECT race_id, finishing_position FROM race_results WHERE boat_number=2),
res3 AS (SELECT race_id, finishing_position FROM race_results WHERE boat_number=3),
e1 AS (
  SELECT race_id, class_number, national_top_1_percent AS nat1
    FROM race_entries WHERE boat_number=1
),
e2 AS (
  SELECT race_id, national_top_2_percent AS nat2_top2
    FROM race_entries WHERE boat_number=2
),
p1 AS (
  SELECT race_id, weather_number FROM race_previews WHERE boat_number=1
)
SELECT r.race_id, r.race_date, r.stadium_number, r.race_number,
       e1.class_number, e1.nat1,
       e2.nat2_top2,
       p1.weather_number,
       res1.finishing_position AS fp1,
       res2.finishing_position AS fp2,
       res3.finishing_position AS fp3,
       pay123.payout AS pay123,
       min_tri.min_pay
  FROM races r
  JOIN min_tri ON min_tri.race_id=r.race_id
  JOIN e1      ON e1.race_id=r.race_id
  LEFT JOIN e2 ON e2.race_id=r.race_id
  LEFT JOIN p1 ON p1.race_id=r.race_id
  JOIN res1    ON res1.race_id=r.race_id
  LEFT JOIN res2  ON res2.race_id=r.race_id
  LEFT JOIN res3  ON res3.race_id=r.race_id
  LEFT JOIN pay123 ON pay123.race_id=r.race_id
 WHERE r.race_grade_number = 5
   AND e1.class_number = 1
   AND e1.nat1 IS NOT NULL AND e1.nat1 >= 7.0
   AND e2.nat2_top2 IS NOT NULL AND e2.nat2_top2 >= 40.0
   AND r.stadium_number NOT IN ({",".join(str(s) for s in EXCLUDE_STADIUMS)})
   AND min_tri.min_pay >= 500 AND min_tri.min_pay < 1000
   AND (p1.weather_number IS NULL OR p1.weather_number != 4)
   AND r.race_date BETWEEN ? AND ?
"""

# ===========================================================================
# L4 universe: 全グレード x 1A1 x odds500-1000 x stadium/weather フィルタ
#   (boat2 制約なし、grade 制約なし)
# ===========================================================================
L4_SQL = f"""
WITH min_tri AS (
  SELECT race_id, MIN(payout) AS min_pay
    FROM race_payouts
   WHERE bet_type='trifecta'
   GROUP BY race_id
),
pay123 AS (
  SELECT race_id, payout
    FROM race_payouts
   WHERE bet_type='trifecta' AND combination='1-2-3'
),
res1 AS (SELECT race_id, finishing_position FROM race_results WHERE boat_number=1),
res2 AS (SELECT race_id, finishing_position FROM race_results WHERE boat_number=2),
res3 AS (SELECT race_id, finishing_position FROM race_results WHERE boat_number=3),
e1 AS (
  SELECT race_id, class_number
    FROM race_entries WHERE boat_number=1
),
p1 AS (
  SELECT race_id, weather_number FROM race_previews WHERE boat_number=1
)
SELECT r.race_id, r.race_date, r.stadium_number, r.race_number,
       e1.class_number,
       p1.weather_number,
       res1.finishing_position AS fp1,
       res2.finishing_position AS fp2,
       res3.finishing_position AS fp3,
       pay123.payout AS pay123,
       min_tri.min_pay
  FROM races r
  JOIN min_tri ON min_tri.race_id=r.race_id
  JOIN e1      ON e1.race_id=r.race_id
  LEFT JOIN p1 ON p1.race_id=r.race_id
  JOIN res1    ON res1.race_id=r.race_id
  LEFT JOIN res2  ON res2.race_id=r.race_id
  LEFT JOIN res3  ON res3.race_id=r.race_id
  LEFT JOIN pay123 ON pay123.race_id=r.race_id
 WHERE e1.class_number = 1
   AND r.stadium_number NOT IN ({",".join(str(s) for s in EXCLUDE_STADIUMS)})
   AND min_tri.min_pay >= 500 AND min_tri.min_pay < 1000
   AND (p1.weather_number IS NULL OR p1.weather_number != 4)
   AND r.race_date BETWEEN ? AND ?
"""


def fetch_rows(sql: str):
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute(sql, (START, END)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def evaluate(rows, bet=100, iters=2000, seed=42):
    pnl = []
    hits = 0
    for r in rows:
        pay = r["pay123"] if (r["fp1"] == 1 and r["fp2"] == 2 and r["fp3"] == 3) else 0
        if pay is None:
            pay = 0
        if pay > 0:
            hits += 1
        pnl.append(pay - bet)
    n = len(pnl)
    if n == 0:
        return dict(n=0, hits=0, hit=0.0, roi=0.0, profit=0,
                    ci_lo=0.0, ci_hi=0.0)
    profit = sum(pnl)
    roi = profit / (n * bet) + 1.0
    hit_rate = hits / n
    rng = random.Random(seed)
    rois = []
    for _ in range(iters):
        s = 0
        for _ in range(n):
            s += pnl[rng.randrange(n)]
        rois.append(s / (n * bet) + 1.0)
    rois.sort()
    lo = rois[int(0.025 * iters)]
    hi = rois[int(0.975 * iters) - 1]
    return dict(n=n, hits=hits, hit=hit_rate, roi=roi, profit=profit,
                ci_lo=lo, ci_hi=hi)


def fmt_row(label, d):
    if d["n"] == 0:
        return f"| {label} | 0 | - | - | - | - |"
    return (f"| {label} | {d['n']} | {d['hits']} | {d['roi']*100:.1f}% | "
            f"{d['profit']:+,} | [{d['ci_lo']*100:.0f}%, {d['ci_hi']*100:.0f}%] |")


def main():
    print(f"Loading F1 base rows [{START} .. {END}] ...")
    f1_rows = fetch_rows(F1_SQL)
    print(f"  F1 base n = {len(f1_rows):,}")

    print(f"Loading L4 (all grades) base rows ...")
    l4_rows = fetch_rows(L4_SQL)
    print(f"  L4 base n = {len(l4_rows):,}\n")

    # ----- Pattern 1: race_number 1..12 individually -----
    print("## Pattern 1: F1 x race_number 1R-12R 個別\n")
    print("| race | n | hit | ROI | profit | CI 95% |")
    print("|---|---|---|---|---|---|")
    p1_results = {}
    for rn in range(1, 13):
        sub = [r for r in f1_rows if r["race_number"] == rn]
        d = evaluate(sub)
        p1_results[rn] = d
        print(fmt_row(f"{rn}R", d))
    d_f1_all = evaluate(f1_rows)
    print(fmt_row("F1-base", d_f1_all))

    # ----- Pattern 2: aggregated groups -----
    print("\n## Pattern 2: F1 集約版\n")
    print("| group | n | hit | ROI | profit | CI 95% |")
    print("|---|---|---|---|---|---|")
    groups2 = [
        ("F1-morning  (1R-4R)",   range(1, 5)),
        ("F1-midday   (5R-8R)",   range(5, 9)),
        ("F1-evening  (9R-12R)",  range(9, 13)),
        ("F1-prime    (11R-12R)", range(11, 13)),
        ("F1-12R-only (12R)",     range(12, 13)),
        ("F1-base     (all)",     range(1, 13)),
    ]
    p2_results = {}
    for label, rng in groups2:
        sub = [r for r in f1_rows if r["race_number"] in rng]
        d = evaluate(sub)
        p2_results[label] = d
        print(fmt_row(label, d))

    # ----- Pattern 3: F1-evening vs L4-evening -----
    print("\n## Pattern 3: F1-evening (9R-12R) vs L4-evening (9R-12R) 比較\n")
    print("| strategy | n | hit | ROI | profit | CI 95% |")
    print("|---|---|---|---|---|---|")
    f1_evening = [r for r in f1_rows if 9 <= r["race_number"] <= 12]
    l4_evening = [r for r in l4_rows if 9 <= r["race_number"] <= 12]
    l4_all = l4_rows
    d_f1_ev = evaluate(f1_evening)
    d_l4_ev = evaluate(l4_evening)
    d_l4_all = evaluate(l4_all)
    print(fmt_row("L4-base     (全 race_number)", d_l4_all))
    print(fmt_row("L4-evening  (9R-12R 全グレード)", d_l4_ev))
    print(fmt_row("F1-base     (全 race_number)", d_f1_all))
    print(fmt_row("F1-evening  (9R-12R 一般戦+nat2>=40)", d_f1_ev))

    # uplift summary
    def _r(d): return d["roi"] * 100
    print()
    print(f"  L4-base ROI    = {_r(d_l4_all):.1f}%")
    print(f"  L4-evening ROI = {_r(d_l4_ev):.1f}%  (uplift vs L4-base: {_r(d_l4_ev)-_r(d_l4_all):+.1f}pt)")
    print(f"  F1-base ROI    = {_r(d_f1_all):.1f}%")
    print(f"  F1-evening ROI = {_r(d_f1_ev):.1f}%  (uplift vs F1-base: {_r(d_f1_ev)-_r(d_f1_all):+.1f}pt)")
    print(f"  F1-evening uplift over L4-evening = {_r(d_f1_ev)-_r(d_l4_ev):+.1f}pt")

    # ----- Judgement -----
    print("\n## 判定\n")
    d_f1_12 = p2_results["F1-12R-only (12R)"]
    d_f1_ev_p = p2_results["F1-evening  (9R-12R)"]
    d_f1_pr  = p2_results["F1-prime    (11R-12R)"]

    def show_decision(label, d, roi_th, lo_th):
        ok_roi = d["roi"] >= roi_th
        ok_lo  = d["ci_lo"] >= lo_th
        flag = "OK" if (ok_roi and ok_lo) else "NG"
        return (f"  {label}: n={d['n']} ROI={d['roi']*100:.1f}% "
                f"CI_lo={d['ci_lo']*100:.0f}% (threshold ROI>={roi_th*100:.0f}% "
                f"CI_lo>={lo_th*100:.0f}%) => {flag}")

    print(show_decision("F1-12R-only (採用候補/最強)", d_f1_12, 2.30, 1.80))
    print(show_decision("F1-evening  (現実解候補)",  d_f1_ev_p, 2.20, 1.70))
    print(show_decision("F1-prime    (11-12R)",      d_f1_pr,  2.30, 1.80))

    # auto verdict
    if d_f1_12["n"] >= 30 and d_f1_12["roi"] >= 2.30 and d_f1_12["ci_lo"] >= 1.80:
        verdict = "ADOPT F1-prime (F1-12R-only)"
    elif d_f1_ev_p["n"] >= 30 and d_f1_ev_p["roi"] >= 2.20 and d_f1_ev_p["ci_lo"] >= 1.70:
        verdict = "ADOPT F1-evening (9R-12R)"
    else:
        verdict = "NO ADOPT - F1 と race_number 効果は独立性が低い (相関で説明可)"
    print(f"\n  Verdict: {verdict}")


if __name__ == "__main__":
    main()
