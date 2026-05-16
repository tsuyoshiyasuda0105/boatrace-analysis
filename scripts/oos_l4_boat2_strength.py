"""OOS validation for L4 general-race overlay filters focused on Boat 2 strength.

F1: L4+ x (boat2 national_top_2_percent >= 40)
F2: L4+ x (boat2 national_top_2_percent >= 35) x (weather NULL or 2; rain=3 already excluded, sun=1 excluded here too)

Read-only. Bootstrap 95% CI with 1000 iterations.
"""
import sqlite3
import random
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "data" / "boatrace.db"

BASE_SQL = """
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
SELECT r.race_id, r.race_date, r.stadium_number,
       e1.nat1, e2.nat2_top2,
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
  JOIN p1      ON p1.race_id=r.race_id
  JOIN res1    ON res1.race_id=r.race_id
  LEFT JOIN res2  ON res2.race_id=r.race_id
  LEFT JOIN res3  ON res3.race_id=r.race_id
  LEFT JOIN pay123 ON pay123.race_id=r.race_id
 WHERE r.race_grade_number=5
   AND e1.class_number=1
   AND e1.nat1 IS NOT NULL AND e1.nat1 >= 7
   AND r.stadium_number NOT IN (2,4,7,8,10,19,21,24)
   AND min_tri.min_pay >= 500 AND min_tri.min_pay < 1000
   AND (p1.weather_number IS NULL OR p1.weather_number != 3)
   AND r.race_date BETWEEN ? AND ?
"""


def fetch_base(start: str, end: str):
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute(BASE_SQL, (start, end)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def passes_filter(row, name):
    nat2 = row["nat2_top2"]
    wn = row["weather_number"]
    # skip if boat2 top2 is missing (judgement skip)
    if nat2 is None:
        return None
    if name == "F1":
        return nat2 >= 40
    if name == "F2":
        if nat2 < 35:
            return False
        # weather: cloudy(2) or NULL only (exclude sun=1)
        return (wn is None) or (wn == 2)
    raise ValueError(name)


def evaluate(rows, name, bet=100, iters=1000, seed=42):
    pnl = []
    hits = 0
    skipped = 0
    for r in rows:
        ok = passes_filter(r, name)
        if ok is None:
            skipped += 1
            continue
        if not ok:
            continue
        pay = r["pay123"] if (r["fp1"] == 1 and r["fp2"] == 2 and r["fp3"] == 3) else 0
        if pay is None:
            pay = 0
        if pay > 0:
            hits += 1
        pnl.append(pay - bet)
    n = len(pnl)
    if n == 0:
        return {"n": 0, "hit": None, "roi": None, "ci_lo": None, "ci_hi": None, "skipped": skipped}
    hit_rate = hits / n
    roi = sum(pnl) / (n * bet) + 1.0
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
    return {"n": n, "hit": hit_rate, "roi": roi, "ci_lo": lo, "ci_hi": hi, "skipped": skipped}


def fmt(d):
    if d["n"] == 0:
        return f"n=0 (skip={d.get('skipped',0)})"
    return (f"n={d['n']} hit={d['hit']*100:.1f}% "
            f"ROI={d['roi']*100:.0f}% CI[{d['ci_lo']*100:.0f},{d['ci_hi']*100:.0f}]")


def tier(results):
    all_n = [r["n"] for r in results.values()]
    if min(all_n) < 30:
        return "Tier 3 (n<30)"
    rois = [r["roi"] for r in results.values()]
    los = [r["ci_lo"] for r in results.values()]
    if all(roi >= 1.5 for roi in rois) and all(lo >= 1.2 for lo in los):
        return "Tier 1"
    if any(lo < 1.0 for lo in los):
        return "Tier 3"
    ok = sum(1 for roi, lo in zip(rois, los) if roi >= 1.5 and lo >= 1.0)
    if ok >= 2:
        return "Tier 2"
    return "Tier 3"


WINDOWS = [
    ("A 2024",  "2024-01-01", "2024-12-31"),
    ("B 2023",  "2023-01-01", "2023-12-31"),
    ("C 25-26", "2025-05-01", "2026-05-16"),
]
FILTERS = ["F1", "F2"]

print("Loading base data per window...")
base = {}
for lbl, s, e in WINDOWS:
    base[lbl] = fetch_base(s, e)
    print(f"  {lbl}: base n = {len(base[lbl])}")

print("\n## OOS validation matrix\n")
print("| Filter | A 2024 | B 2023 | C 25-26 | Tier |")
print("|---|---|---|---|---|")
all_results = {}
for f in FILTERS:
    res = {}
    cells = []
    for lbl, _, _ in WINDOWS:
        d = evaluate(base[lbl], f)
        res[lbl] = d
        cells.append(fmt(d))
    t = tier(res)
    all_results[f] = (res, t)
    print(f"| {f} | {cells[0]} | {cells[1]} | {cells[2]} | {t} |")

print()
for f, (res, t) in all_results.items():
    print(f"# {f} -> {t}")
    for lbl, _, _ in WINDOWS:
        print(f"  {lbl}: {res[lbl]}")
