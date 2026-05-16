"""L4 戦略をレース番号別 (1R-12R) に分解した OOS 分析。

ベース条件 (L4):
  - 1号艇 class_number=1 (A1)
  - 三連単本命 (最小オッズ) 500-1000円
  - B除外会場: 戸田(2), 平和島(4), 蒲郡(18), 三国(10), 芦屋(22), 常滑(14), 下関(20), 大村(23)
  - 雨除外: race_previews.weather_number != 4
  - 期間: 2022-04-01 〜 2026-03-31 (4年分)
  - 買い目: 三連単 1-2-3 / 100円

Pattern A: race_number 1..12 を個別集計
Pattern B: 朝(1-4) / 昼(5-8) / 夕夜(9-12)
Pattern C: 12R vs 1-11R

Read-only. Bootstrap 95% CI, 2000 iter.
"""
from __future__ import annotations
import sqlite3
import random
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

DB = Path(r"C:/boat_project/boatrace-analysis/data/boatrace.db")

# user spec: 戸田=2, 平和島=4, 三国=10, 常滑=14, 蒲郡=18, 下関=20, 芦屋=22, 大村=23
EXCLUDE_STADIUMS = (2, 4, 10, 14, 18, 20, 22, 23)
START = "2022-04-01"
END = "2026-03-31"

SQL = f"""
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


def fetch_rows():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute(SQL, (START, END)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def evaluate(rows, bet=100, iters=2000, seed=42):
    """Return ROI/CI for an iterable of row dicts. Bet 100yen on 1-2-3 trifecta."""
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
    print(f"Loading L4 base rows [{START} .. {END}] ...")
    rows = fetch_rows()
    print(f"  total L4 rows = {len(rows):,}\n")

    # ---------- Pattern A: per race_number ----------
    print("## Pattern A: race_number 1R-12R 個別\n")
    print("| race_number | n | hit | ROI | profit | CI 95% |")
    print("|---|---|---|---|---|---|")
    a_results = {}
    for rn in range(1, 13):
        sub = [r for r in rows if r["race_number"] == rn]
        d = evaluate(sub)
        a_results[rn] = d
        print(fmt_row(f"{rn}R", d))

    # overall
    d_all = evaluate(rows)
    print(fmt_row("ALL", d_all))

    # ---------- Pattern B: 3 groups ----------
    print("\n## Pattern B: 朝/昼/夕夜 集約\n")
    print("| group | n | hit | ROI | profit | CI 95% |")
    print("|---|---|---|---|---|---|")
    groups = [
        ("朝 1R-4R",   range(1, 5)),
        ("昼 5R-8R",   range(5, 9)),
        ("夕夜 9R-12R", range(9, 13)),
    ]
    b_results = {}
    for label, rng in groups:
        sub = [r for r in rows if r["race_number"] in rng]
        d = evaluate(sub)
        b_results[label] = d
        print(fmt_row(label, d))

    # ---------- Pattern C: 12R vs 1-11R ----------
    print("\n## Pattern C: 12R (メイン) vs 1R-11R\n")
    print("| group | n | hit | ROI | profit | CI 95% |")
    print("|---|---|---|---|---|---|")
    sub_12 = [r for r in rows if r["race_number"] == 12]
    sub_other = [r for r in rows if r["race_number"] != 12]
    d12 = evaluate(sub_12)
    dother = evaluate(sub_other)
    print(fmt_row("12R only", d12))
    print(fmt_row("1R-11R",   dother))

    # ---------- Summary stats ----------
    print("\n## Summary helpers\n")
    sorted_a = sorted(a_results.items(),
                      key=lambda kv: kv[1]["roi"], reverse=True)
    print("ROI top3 race_number:")
    for rn, d in sorted_a[:3]:
        print(f"  {rn}R  ROI={d['roi']*100:.1f}%  n={d['n']}  "
              f"CI=[{d['ci_lo']*100:.0f}%, {d['ci_hi']*100:.0f}%]")
    print("ROI worst3 race_number:")
    for rn, d in sorted_a[-3:]:
        print(f"  {rn}R  ROI={d['roi']*100:.1f}%  n={d['n']}  "
              f"CI=[{d['ci_lo']*100:.0f}%, {d['ci_hi']*100:.0f}%]")

    below100 = [rn for rn, d in a_results.items() if d["roi"] < 1.0]
    print(f"\nROI<100% race_numbers: {below100}")


if __name__ == "__main__":
    main()
