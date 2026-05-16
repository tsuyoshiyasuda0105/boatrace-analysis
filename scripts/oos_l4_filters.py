"""OOS validation for L4 general-race overlay filters (F1..F5).

Read-only. Bootstrap 95% CI with 500 iterations.
"""
import sqlite3
import random
import statistics
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "data" / "boatrace.db"

# Stadiums excluded as "B"
EXCLUDED_STADIUMS = (2, 4, 7, 8, 10, 19, 21, 24)

# Base set query - returns one row per qualifying race with all needed fields
# Filters:
#   race_grade_number=5 (一般戦)
#   1号艇 class_number=1 (A1)
#   stadium NOT IN excluded
#   trifecta min payout in [500,1000)
#   weather_number != 3 (rain excluded) - using boat1 preview row
#   has result
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
res1 AS (
  SELECT race_id, finishing_position
    FROM race_results
   WHERE boat_number=1
),
res2 AS (
  SELECT race_id, finishing_position
    FROM race_results
   WHERE boat_number=2
),
res3 AS (
  SELECT race_id, finishing_position
    FROM race_results
   WHERE boat_number=3
),
e1 AS (
  SELECT race_id, class_number, national_top_1_percent AS nat1,
         local_top_1_percent AS loc1, assigned_motor_top_2_percent AS mot2
    FROM race_entries WHERE boat_number=1
),
p1 AS (
  SELECT race_id, weather_number, wind_speed, exhibition_time
    FROM race_previews WHERE boat_number=1
),
p_all AS (
  SELECT race_id,
         MIN(CASE WHEN boat_number=1 THEN exhibition_time END) AS et1,
         MIN(CASE WHEN boat_number=2 THEN exhibition_time END) AS et2,
         MIN(CASE WHEN boat_number=3 THEN exhibition_time END) AS et3,
         MIN(CASE WHEN boat_number=4 THEN exhibition_time END) AS et4,
         MIN(CASE WHEN boat_number=5 THEN exhibition_time END) AS et5,
         MIN(CASE WHEN boat_number=6 THEN exhibition_time END) AS et6
    FROM race_previews GROUP BY race_id
)
SELECT r.race_id, r.race_date, r.stadium_number,
       e1.nat1, e1.loc1, e1.mot2,
       p1.weather_number, p1.wind_speed,
       p_all.et1, p_all.et2, p_all.et3, p_all.et4, p_all.et5, p_all.et6,
       res1.finishing_position AS fp1,
       res2.finishing_position AS fp2,
       res3.finishing_position AS fp3,
       pay123.payout AS pay123,
       min_tri.min_pay
  FROM races r
  JOIN min_tri ON min_tri.race_id=r.race_id
  JOIN e1      ON e1.race_id=r.race_id
  JOIN p1      ON p1.race_id=r.race_id
  JOIN p_all   ON p_all.race_id=r.race_id
  JOIN res1    ON res1.race_id=r.race_id
  LEFT JOIN res2  ON res2.race_id=r.race_id
  LEFT JOIN res3  ON res3.race_id=r.race_id
  LEFT JOIN pay123 ON pay123.race_id=r.race_id
 WHERE r.race_grade_number=5
   AND e1.class_number=1
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
    nat1 = row["nat1"]
    loc1 = row["loc1"]
    mot2 = row["mot2"]
    et1 = row["et1"]
    ws = row["wind_speed"]
    ets = [row[f"et{i}"] for i in range(1, 7)]
    ets_valid = [e for e in ets if e is not None]

    if name == "F1":
        return nat1 is not None and nat1 >= 7
    if name == "F2":
        return (nat1 is not None and nat1 >= 7 and
                mot2 is not None and mot2 >= 50)
    if name == "F3":
        if nat1 is None or nat1 < 7:
            return False
        if et1 is None or len(ets_valid) < 2:
            return False
        return et1 == min(ets_valid)
    if name == "F4":
        if nat1 is None or nat1 < 7:
            return False
        return ws is not None and 2 <= ws <= 3
    if name == "F5":
        if nat1 is None or nat1 < 7:
            return False
        if loc1 is None:
            return False
        return (nat1 + loc1) >= 14
    raise ValueError(name)


def evaluate(rows, name, bet=100, iters=500, seed=42):
    # one row per race; bet 1-2-3 trifecta
    pnl = []
    hits = 0
    for r in rows:
        if not passes_filter(r, name):
            continue
        pay = r["pay123"] if (r["fp1"] == 1 and r["fp2"] == 2 and r["fp3"] == 3) else 0
        if pay is None:
            pay = 0
        if pay > 0:
            hits += 1
        pnl.append(pay - bet)
    n = len(pnl)
    if n == 0:
        return {"n": 0, "hit": None, "roi": None, "ci_lo": None, "ci_hi": None}
    hit_rate = hits / n
    roi = sum(pnl) / (n * bet) + 1.0  # ROI as multiplier of stake (1.0 = breakeven)
    # bootstrap
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
    return {"n": n, "hit": hit_rate, "roi": roi, "ci_lo": lo, "ci_hi": hi}


def fmt(d):
    if d["n"] == 0:
        return "n=0"
    return f"n={d['n']} hit={d['hit']*100:.1f}% ROI={d['roi']*100:.0f}% CI[{d['ci_lo']*100:.0f},{d['ci_hi']*100:.0f}]"


def tier(results):
    # results: dict of label -> result dict
    if any(r["n"] is not None and r["n"] < 30 for r in results.values()):
        # at least one tiny n
        if any(r["n"] == 0 or (r["n"] is not None and r["n"] < 30) for r in results.values()):
            # may still be Tier 3
            pass
    all_n = [r["n"] for r in results.values()]
    if min(all_n) < 30:
        return "Tier 3 (n<30)"
    rois = [r["roi"] for r in results.values()]
    los = [r["ci_lo"] for r in results.values()]
    if all(roi > 1.5 for roi in rois) and all(lo > 1.2 for lo in los):
        return "Tier 1"
    # Count windows OK (ROI>1.5 and CI_lo>1.0)
    ok = sum(1 for roi, lo in zip(rois, los) if roi > 1.5 and lo > 1.0)
    if any(lo < 1.0 for lo in los):
        return "Tier 3"
    if ok >= 2:
        return "Tier 2"
    return "Tier 3"


WINDOWS = [
    ("A 2024",   "2024-01-01", "2024-12-31"),
    ("B 2023",   "2023-01-01", "2023-12-31"),
    ("C 25-26",  "2025-05-01", "2026-05-16"),
]
FILTERS = ["F1", "F2", "F3", "F4", "F5"]

print("Loading base data per window...")
base = {}
for lbl, s, e in WINDOWS:
    base[lbl] = fetch_base(s, e)
    print(f"  {lbl}: base n = {len(base[lbl])}")

print("\n## OOS 検証マトリックス\n")
print("| フィルタ | A 2024 | B 2023 | C 25-26 | 判定 |")
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
