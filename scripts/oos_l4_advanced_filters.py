"""L4 戦略の高度フィルタ分析 (3 課題まとめ).

L4 戦略の定義 (ユーザ指定):
  - 1号艇 A1 (race_entries.class_number = 1)
  - 本命三連単 1-2-3 のオッズ 500-1000 円
  - B除外会場除外: 戸田=2, 平和島=4, 三国=10, 鳴門=14, 徳山=18, 若松=20, 福岡=22, 唐津=23
  - 雨除外: race_previews.weather_number != 4
  - 買い目: 三連単 1-2-3 / 100円固定
  - 期間: 2022-04-01 〜 2026-03-31

課題:
  1) 5R-8R 除外シミュレーション
  2) L4-evening (9R-12R) / L4-prime (11R-12R) / L4-12R-only
  3) グレード × race_number の二重分解

Read-only. Bootstrap 95% CI, 2000 iter.
"""
from __future__ import annotations

import random
import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

DB = Path(r"C:/boat_project/boatrace-analysis/data/boatrace.db")

# ユーザ指定の除外会場番号 (名称は無視し番号ベースで除外)
EXCLUDE_STADIUMS = (2, 4, 10, 14, 18, 20, 22, 23)
START = "2022-04-01"
END = "2026-03-31"

GRADE_LABELS = {1: "SG", 2: "G1", 3: "G2", 4: "G3", 5: "一般戦"}
GRADE_ORDER = [1, 2, 3, 4, 5]

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
       r.race_grade_number,
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


def section(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def main():
    print(f"Loading L4 base rows [{START} .. {END}] ...")
    rows = fetch_rows()
    print(f"  total L4 rows = {len(rows):,}")

    # ============================================================
    # Baseline (L4 全体)
    # ============================================================
    d_all = evaluate(rows)

    # ============================================================
    # 課題 1: 5R-8R 除外シミュレーション
    # ============================================================
    section("課題 1: 5R-8R / CI 弱い R 除外シミュレーション")

    # まず race_number 別の CI lo を測って「CI 弱い R」候補を可視化
    print("\n[参考] race_number 別 ROI と CI lo")
    print("| race_number | n | hit | ROI | profit | CI 95% |")
    print("|---|---|---|---|---|---|")
    per_rn = {}
    for rn in range(1, 13):
        sub = [r for r in rows if r["race_number"] == rn]
        d = evaluate(sub)
        per_rn[rn] = d
        print(fmt_row(f"{rn}R", d))

    # CI 下限が 150% 未満の R を抽出 (= "CI 弱い R")
    weak_ci = sorted([rn for rn, d in per_rn.items() if d["ci_lo"] * 100 < 150])
    print(f"\nCI 下限 < 150% の race_number: {weak_ci}")

    print("\n[本題] 戦略比較")
    print("| 戦略 | n | hit | ROI | profit | CI 95% |")
    print("|---|---|---|---|---|---|")
    print(fmt_row("L4 全体 (現状)", d_all))

    excl_58 = [r for r in rows if r["race_number"] not in (5, 6, 7, 8)]
    d_excl58 = evaluate(excl_58)
    print(fmt_row("L4 (5-8R 除外)", d_excl58))

    # 動的に CI 弱い R 全部除外
    if weak_ci:
        excl_weak = [r for r in rows if r["race_number"] not in set(weak_ci)]
        d_excl_weak = evaluate(excl_weak)
        label_weak = f"L4 ({','.join(str(x)+'R' for x in weak_ci)} 除外)"
        print(fmt_row(label_weak, d_excl_weak))

    # 利益貢献度: 5-8R 部分の絶対貢献
    sub_58 = [r for r in rows if r["race_number"] in (5, 6, 7, 8)]
    d_58 = evaluate(sub_58)
    if d_all["profit"]:
        share_58 = d_58["profit"] / d_all["profit"] * 100
    else:
        share_58 = 0.0
    print(f"\n5-8R 部分の利益貢献: profit={d_58['profit']:+,} / "
          f"全体 {d_all['profit']:+,} = {share_58:.1f}%")

    # ============================================================
    # 課題 2: L4-evening / L4-prime の詳細
    # ============================================================
    section("課題 2: 時間帯フィルタ (evening / prime / 12R-only)")

    print("\n| 戦略 | 定義 | n | hit | ROI | profit | CI 95% |")
    print("|---|---|---|---|---|---|---|")
    presets = [
        ("L4-base",      "全 race_number", range(1, 13)),
        ("L4-evening",   "9R-12R",         range(9, 13)),
        ("L4-prime",     "11R-12R",        range(11, 13)),
        ("L4-12R-only",  "12R のみ",        range(12, 13)),
    ]
    summary2 = {}
    for label, defn, rng in presets:
        sub = [r for r in rows if r["race_number"] in rng]
        d = evaluate(sub)
        summary2[label] = d
        if d["n"] == 0:
            print(f"| {label} | {defn} | 0 | - | - | - | - |")
        else:
            print(f"| {label} | {defn} | {d['n']} | {d['hits']} | "
                  f"{d['roi']*100:.1f}% | {d['profit']:+,} | "
                  f"[{d['ci_lo']*100:.0f}%, {d['ci_hi']*100:.0f}%] |")

    # ============================================================
    # 課題 3: グレード × race_number の二重分解
    # ============================================================
    section("課題 3: グレード × race_number の二重分解")

    # まずグレード別の全体 ROI を出す
    print("\n[3a] グレード別 全 race_number 集計")
    print("| グレード | n | hit | ROI | profit | CI 95% |")
    print("|---|---|---|---|---|---|")
    by_grade = {}
    for g in GRADE_ORDER:
        sub = [r for r in rows if r["race_grade_number"] == g]
        d = evaluate(sub)
        by_grade[g] = d
        print(fmt_row(GRADE_LABELS[g], d))

    # グレード × race_number 全 cell (12 race_number × 5 grade)
    print("\n[3b] グレード × race_number ROI マトリクス (%)")
    header = f"{'race_no':<10}" + "".join(f"{GRADE_LABELS[g]:>10}" for g in GRADE_ORDER)
    print(header)
    cells = {}
    for rn in range(1, 13):
        line = f"{str(rn)+'R':<10}"
        for g in GRADE_ORDER:
            sub = [r for r in rows
                   if r["race_number"] == rn and r["race_grade_number"] == g]
            d = evaluate(sub)
            cells[(rn, g)] = d
            if d["n"] == 0:
                line += f"{'-':>10}"
            else:
                line += f"{d['roi']*100:>9.0f}%"
        print(line)

    # n 参考
    print("\n[3b-n] 同マトリクス (n)")
    print(header)
    for rn in range(1, 13):
        line = f"{str(rn)+'R':<10}"
        for g in GRADE_ORDER:
            d = cells[(rn, g)]
            line += f"{d['n']:>10}" if d["n"] else f"{'-':>10}"
        print(line)

    # フォーカス: 各グレードについて、全体 vs 12R限定, vs 11R-12R限定, vs 9R-12R限定
    print("\n[3c] グレード × 時間帯フォーカス")
    print("| グレード | フィルタ | n | hit | ROI | profit | CI 95% |")
    print("|---|---|---|---|---|---|---|")
    focus_filters = [
        ("全体",     range(1, 13)),
        ("9R-12R",   range(9, 13)),
        ("11R-12R",  range(11, 13)),
        ("12R のみ", range(12, 13)),
    ]
    for g in GRADE_ORDER:
        for flabel, rng in focus_filters:
            sub = [r for r in rows
                   if r["race_grade_number"] == g and r["race_number"] in rng]
            d = evaluate(sub)
            if d["n"] == 0:
                print(f"| {GRADE_LABELS[g]} | {flabel} | 0 | - | - | - | - |")
            else:
                print(f"| {GRADE_LABELS[g]} | {flabel} | {d['n']} | "
                      f"{d['hits']} | {d['roi']*100:.1f}% | "
                      f"{d['profit']:+,} | "
                      f"[{d['ci_lo']*100:.0f}%, {d['ci_hi']*100:.0f}%] |")

    # ============================================================
    # 採用判定 (まとめ)
    # ============================================================
    section("採用判定 (n>=1000 & CI 下限>=150% / 1500&165% / 2000&175%)")

    def verdict(d):
        if d["n"] == 0:
            return "-"
        lo = d["ci_lo"] * 100
        n = d["n"]
        if n >= 2000 and lo >= 175:
            return "最強推奨"
        if n >= 1500 and lo >= 165:
            return "強推奨"
        if n >= 1000 and lo >= 150:
            return "採用可"
        return "見送り"

    candidates = []
    candidates.append(("L4 全体 (現状)",       d_all))
    candidates.append(("L4 (5-8R 除外)",       d_excl58))
    if weak_ci:
        candidates.append((f"L4 (CI弱R除外 {weak_ci})", d_excl_weak))
    for label, _, _ in presets[1:]:
        candidates.append((label, summary2[label]))
    # グレード × 12R に絞った候補も判定
    for g in GRADE_ORDER:
        sub = [r for r in rows
               if r["race_grade_number"] == g and r["race_number"] == 12]
        d = evaluate(sub)
        candidates.append((f"L4 {GRADE_LABELS[g]} × 12R", d))
        sub2 = [r for r in rows
                if r["race_grade_number"] == g and r["race_number"] in range(11, 13)]
        d2 = evaluate(sub2)
        candidates.append((f"L4 {GRADE_LABELS[g]} × 11-12R", d2))

    print("\n| 戦略 | n | ROI | CI 下限 | 判定 |")
    print("|---|---|---|---|---|")
    for label, d in candidates:
        if d["n"] == 0:
            print(f"| {label} | 0 | - | - | - |")
        else:
            print(f"| {label} | {d['n']} | {d['roi']*100:.1f}% | "
                  f"{d['ci_lo']*100:.0f}% | {verdict(d)} |")


if __name__ == "__main__":
    main()
