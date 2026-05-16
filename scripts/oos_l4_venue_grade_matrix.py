"""L4 戦略を「会場 × グレード」で分解するマトリクス分析。

L4 戦略の定義:
  - 1号艇 A1 (race_entries.class_number = 1)
  - 本命三連単 1-2-3 のオッズ 500-1000 円 (race_payouts の min(trifecta) = pay123)
  - B除外会場除外 (戸田=2, 蒲郡=18, 三国=10, 芦屋=22, 平和島=4, 常滑=14, 下関=20, 大村=23)
  - 雨除外 (race_previews.weather_number != 4)
  - 買い目: 三連単 1-2-3、100 円固定

集計対象期間: 2022-04-01 〜 2026-03-31 (4 年分)

出力:
  1. 16 会場 × 5 グレード の ROI 行列 (コンパクト)
  2. n>=50 かつ ROI>=250% の cell (ピックアップ)
  3. n>=100 かつ ROI<100% の cell (要注意)
  4. 集中投資 Top5 / 除外検討リスト

Read-only.
"""
from __future__ import annotations

import random
import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

DB = Path(__file__).resolve().parents[1] / "data" / "boatrace.db"

EXCLUDE_STADIUMS = {2, 4, 10, 14, 18, 20, 22, 23}

STADIUM_NAMES = {
    1: "桐生", 2: "戸田", 3: "江戸川", 4: "平和島", 5: "多摩川",
    6: "浜名湖", 7: "蒲郡", 8: "常滑", 9: "津", 10: "三国",
    11: "びわこ", 12: "住之江", 13: "尼崎", 14: "鳴門", 15: "丸亀",
    16: "児島", 17: "宮島", 18: "徳山", 19: "下関", 20: "若松",
    21: "芦屋", 22: "福岡", 23: "唐津", 24: "大村",
}

# NOTE: 上記タスクで指示された除外会場名と stadium_number は標準マッピングに合わない箇所がある。
# 指示文の「戸田=2, 蒲郡=18, 三国=10, 芦屋=22, 平和島=4, 常滑=14, 下関=20, 大村=23」
# これを尊重して数値ベースで除外する (名前ずれは無視)。
EXCLUDED_STADIUM_NUMBERS = {2, 18, 10, 22, 4, 14, 20, 23}

GRADE_LABELS = {1: "SG", 2: "G1", 3: "G2", 4: "G3", 5: "一般戦"}
GRADE_ORDER = [1, 2, 3, 4, 5]

DATE_START = "2022-04-01"
DATE_END = "2026-03-31"

SQL = """
WITH min_tri AS (
  SELECT race_id, MIN(payout) AS min_pay
    FROM race_payouts
   WHERE bet_type='trifecta'
   GROUP BY race_id
),
pay123 AS (
  SELECT race_id, payout AS pay123
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
SELECT r.race_id, r.race_date, r.stadium_number, r.race_grade_number,
       p1.weather_number,
       res1.finishing_position AS fp1,
       res2.finishing_position AS fp2,
       res3.finishing_position AS fp3,
       pay123.pay123,
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
   AND min_tri.min_pay >= 500 AND min_tri.min_pay < 1000
   AND (p1.weather_number IS NULL OR p1.weather_number != 4)
   AND r.race_date BETWEEN ? AND ?
"""


def fetch_rows():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute(SQL, (DATE_START, DATE_END)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def bootstrap_ci(pnl, bet=100, iters=2000, seed=42):
    n = len(pnl)
    if n == 0:
        return None, None
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
    return lo, hi


def main():
    print(f"Loading rows {DATE_START} .. {DATE_END} ...")
    rows = fetch_rows()
    print(f"  raw rows (L4 base, A1 + pay500-1000 + 雨除外): {len(rows)}")

    # B除外会場フィルタは「集計時のみ」適用 — 除外会場を排除した cell だけ計算
    rows = [r for r in rows if r["stadium_number"] not in EXCLUDED_STADIUM_NUMBERS]
    print(f"  after B除外会場 filter: {len(rows)}")

    # Bucket
    BET = 100
    cells = {}  # (stadium, grade) -> list of pnl
    for r in rows:
        st = r["stadium_number"]
        gr = r["race_grade_number"]
        if gr is None:
            continue
        if gr not in GRADE_LABELS:
            continue
        hit = (r["fp1"] == 1 and r["fp2"] == 2 and r["fp3"] == 3)
        pay = r["pay123"] if hit else 0
        if pay is None:
            pay = 0
        pnl = pay - BET
        cells.setdefault((st, gr), []).append((pnl, hit, pay))

    # Compute stats per cell
    stats = {}
    for key, vs in cells.items():
        n = len(vs)
        hits = sum(1 for (_, h, _) in vs if h)
        cost = n * BET
        payout = sum(p for (_, _, p) in vs)
        profit = payout - cost
        roi = (payout / cost * 100) if cost else 0.0
        hit_rate = (hits / n * 100) if n else 0.0
        pnls = [pnl for (pnl, _, _) in vs]
        lo, hi = bootstrap_ci(pnls)
        stats[key] = dict(n=n, hits=hits, hit_rate=hit_rate,
                          roi=roi, profit=profit,
                          ci_lo=lo, ci_hi=hi)

    stadiums = sorted({st for st in STADIUM_NAMES if st not in EXCLUDED_STADIUM_NUMBERS})

    # ===== 1. 完全マトリクス (ROI %) =====
    print("\n" + "=" * 78)
    print("1) 完全マトリクス: ROI (%) — 行: 会場, 列: グレード")
    print("=" * 78)
    header = f"{'会場':<10}" + "".join(f"{GRADE_LABELS[g]:>10}" for g in GRADE_ORDER) + f"{'合計':>10}"
    print(header)
    for st in stadiums:
        line = f"{STADIUM_NAMES.get(st, '?'+str(st)):<10}"
        total_n = 0
        total_cost = 0
        total_payout = 0
        for g in GRADE_ORDER:
            s = stats.get((st, g))
            if s is None or s["n"] == 0:
                line += f"{'-':>10}"
            else:
                line += f"{s['roi']:>9.0f}%"
                total_n += s["n"]
                total_cost += s["n"] * BET
                total_payout += s["profit"] + s["n"] * BET
        if total_cost:
            tot_roi = total_payout / total_cost * 100
            line += f"{tot_roi:>9.0f}%"
        else:
            line += f"{'-':>10}"
        print(line)

    # n マトリクス (参考)
    print("\n--- 参考: n マトリクス (該当レース数) ---")
    print(header)
    for st in stadiums:
        line = f"{STADIUM_NAMES.get(st, '?'+str(st)):<10}"
        total_n = 0
        for g in GRADE_ORDER:
            s = stats.get((st, g))
            if s is None or s["n"] == 0:
                line += f"{'-':>10}"
            else:
                line += f"{s['n']:>10}"
                total_n += s["n"]
        line += f"{total_n:>10}"
        print(line)

    # ===== 2. ピックアップ (n>=50 & ROI>=250%) =====
    print("\n" + "=" * 78)
    print("2) ピックアップ表 (n >= 50 かつ ROI >= 250%)")
    print("=" * 78)
    print(f"| {'会場':<6} | {'グレード':<6} | {'n':>5} | {'hit':>6} | {'ROI':>6} | {'profit':>9} | {'CI 95%':<22} |")
    print(f"|{'-'*8}|{'-'*8}|{'-'*7}|{'-'*8}|{'-'*8}|{'-'*11}|{'-'*24}|")
    picks = []
    for (st, g), s in stats.items():
        if s["n"] >= 50 and s["roi"] >= 250:
            picks.append((st, g, s))
    picks.sort(key=lambda x: -x[2]["roi"])
    for st, g, s in picks:
        ci = f"[{s['ci_lo']*100:.0f}%, {s['ci_hi']*100:.0f}%]"
        print(f"| {STADIUM_NAMES.get(st, '?'+str(st)):<6} | {GRADE_LABELS[g]:<6} | "
              f"{s['n']:>5} | {s['hit_rate']:>5.1f}% | {s['roi']:>5.0f}% | "
              f"{s['profit']:>+9,} | {ci:<22} |")
    if not picks:
        print("  (該当 cell なし)")

    # ===== 3. 要注意 cell (n>=100 & ROI<100%) =====
    print("\n" + "=" * 78)
    print("3) 要注意 cell (n >= 100 かつ ROI < 100%) — 現状 L4 で買って損してる組み合わせ")
    print("=" * 78)
    print(f"| {'会場':<6} | {'グレード':<6} | {'n':>5} | {'hit':>6} | {'ROI':>6} | {'profit':>9} | {'CI 95%':<22} |")
    print(f"|{'-'*8}|{'-'*8}|{'-'*7}|{'-'*8}|{'-'*8}|{'-'*11}|{'-'*24}|")
    warns = []
    for (st, g), s in stats.items():
        if s["n"] >= 100 and s["roi"] < 100:
            warns.append((st, g, s))
    warns.sort(key=lambda x: x[2]["roi"])
    for st, g, s in warns:
        ci = f"[{s['ci_lo']*100:.0f}%, {s['ci_hi']*100:.0f}%]"
        print(f"| {STADIUM_NAMES.get(st, '?'+str(st)):<6} | {GRADE_LABELS[g]:<6} | "
              f"{s['n']:>5} | {s['hit_rate']:>5.1f}% | {s['roi']:>5.0f}% | "
              f"{s['profit']:>+9,} | {ci:<22} |")
    if not warns:
        print("  (該当 cell なし)")

    # ===== 4. Top 5 =====
    print("\n" + "=" * 78)
    print("4) 集中投資すべき会場×グレード Top 5 (n >= 30 & ROI 順)")
    print("=" * 78)
    candidates = [(st, g, s) for (st, g), s in stats.items() if s["n"] >= 30]
    candidates.sort(key=lambda x: -x[2]["roi"])
    for i, (st, g, s) in enumerate(candidates[:5], 1):
        ci = f"[{s['ci_lo']*100:.0f}%, {s['ci_hi']*100:.0f}%]"
        print(f"  {i}. {STADIUM_NAMES.get(st, '?'+str(st)):<6} × {GRADE_LABELS[g]:<6}  "
              f"n={s['n']:>4} hit={s['hit_rate']:>4.1f}%  ROI={s['roi']:>5.0f}%  "
              f"profit={s['profit']:>+8,}円  CI={ci}")

    print("\n--- 除外検討候補 (n>=80 & ROI<80%) ---")
    drops = [(st, g, s) for (st, g), s in stats.items() if s["n"] >= 80 and s["roi"] < 80]
    drops.sort(key=lambda x: x[2]["roi"])
    for st, g, s in drops:
        ci = f"[{s['ci_lo']*100:.0f}%, {s['ci_hi']*100:.0f}%]"
        print(f"  {STADIUM_NAMES.get(st, '?'+str(st)):<6} × {GRADE_LABELS[g]:<6}  "
              f"n={s['n']:>4} ROI={s['roi']:>5.0f}%  profit={s['profit']:>+8,}円  CI={ci}")
    if not drops:
        print("  (該当 cell なし)")


if __name__ == "__main__":
    main()
