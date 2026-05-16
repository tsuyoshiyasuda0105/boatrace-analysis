"""2-year & 4-year OOS validation for L4 general-race Boat 2 strength filters.

Filters:
  Base : L4+ (1号A1, nat1>=7), general (grade=5), B除外, pay [500,1000), 雨除外
  F1   : Base x boat2 national_top_2_percent >= 40
  F2   : Base x boat2 national_top_2_percent >= 35 x (weather NULL or 2; sun=1 excluded)

Windows:
  W1   : 2023-01-01 .. 2024-12-31  (clean OOS, no hot bias)
  W2   : 2024-05-16 .. 2026-05-15  (recent 2y, real-world expectation)
  W3   : 2022-05-08 .. 2026-05-15  (4y, full DB)

Bet: 3rentan 1-2-3 x 100yen. Bootstrap 95% CI, 2000 iter, percentile.
Read-only.
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


def fetch_base(start, end):
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute(BASE_SQL, (start, end)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def passes_filter(row, name):
    nat2 = row["nat2_top2"]
    wn = row["weather_number"]
    if name == "Base":
        return True
    if nat2 is None:
        return None  # skip
    if name == "F1":
        return nat2 >= 40
    if name == "F2":
        if nat2 < 35:
            return False
        return (wn is None) or (wn == 2)
    raise ValueError(name)


def evaluate(rows, name, bet=100, iters=2000, seed=42):
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
        return {"n": 0, "hit": None, "roi": None, "ci_lo": None, "ci_hi": None,
                "skipped": skipped, "sd": None}
    hit_rate = hits / n
    roi = sum(pnl) / (n * bet) + 1.0
    # sample SD of per-bet ROI (for safety margin point estimate)
    mean_pnl = sum(pnl) / n
    var = sum((x - mean_pnl) ** 2 for x in pnl) / max(n - 1, 1)
    sd_roi = (var ** 0.5) / bet  # per-bet ROI sd
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
    return {"n": n, "hit": hit_rate, "roi": roi, "ci_lo": lo, "ci_hi": hi,
            "skipped": skipped, "sd": sd_roi}


def months_between(start, end):
    """Approx months between yyyy-mm-dd strings."""
    sy, sm, sd = map(int, start.split("-"))
    ey, em, ed = map(int, end.split("-"))
    return (ey - sy) * 12 + (em - sm) + (ed - sd) / 30.0


WINDOWS = [
    ("W1 clean 2y",  "2023-01-01", "2024-12-31"),
    ("W2 recent 2y", "2024-05-16", "2026-05-15"),
    ("W3 full 4y",   "2022-05-08", "2026-05-15"),
]
FILTERS = ["Base", "F1", "F2"]

print("Loading base data per window...")
base = {}
for lbl, s, e in WINDOWS:
    base[lbl] = fetch_base(s, e)
    print(f"  {lbl} [{s}..{e}]: base rows = {len(base[lbl])}")

print("\n## 2-year + 4-year matrix\n")
print("| Window | Filter | n | hit% | ROI | 95% CI | mo-avg |")
print("|---|---|---|---|---|---|---|")

all_results = {}
for lbl, s, e in WINDOWS:
    mo = months_between(s, e)
    for f in FILTERS:
        d = evaluate(base[lbl], f)
        all_results[(lbl, f)] = (d, mo)
        if d["n"] == 0:
            print(f"| {lbl} | {f} | 0 | - | - | - | - |")
            continue
        mo_avg = d["n"] / mo
        ci = f"[{d['ci_lo']*100:.0f}%, {d['ci_hi']*100:.0f}%]"
        print(f"| {lbl} | {f} | {d['n']} | {d['hit']*100:.1f}% | "
              f"{d['roi']*100:.0f}% | {ci} | {mo_avg:.1f} |")

print("\n## Integrated judgement\n")
for f in ["F1", "F2"]:
    print(f"\n### {f}")
    rows = [(lbl, all_results[(lbl, f)][0]) for lbl, _, _ in WINDOWS]
    for lbl, d in rows:
        if d["n"] == 0:
            continue
        width = (d["ci_hi"] - d["ci_lo"]) * 100
        ok130 = "OK" if d["ci_lo"] >= 1.30 else "NG"
        ok150 = "OK" if d["ci_lo"] >= 1.50 else "NG"
        print(f"  {lbl}: CI lo={d['ci_lo']*100:.0f}% (>=130:{ok130}, >=150:{ok150}) "
              f"width={width:.0f}pt sd={d['sd']*100:.0f}%")

print("\n## Point estimate with safety margin (W3 median - 0.5*sd / n_persqrt)\n")
for f in ["F1", "F2"]:
    d, mo = all_results[("W3 full 4y", f)]
    if d["n"] == 0:
        continue
    # se of mean ROI = sd / sqrt(n)
    se = d["sd"] / (d["n"] ** 0.5)
    safe = d["roi"] - 0.5 * se
    print(f"  {f}: W3 ROI={d['roi']*100:.0f}%, SE={se*100:.1f}pt, "
          f"safe(ROI-0.5SE)={safe*100:.0f}%, monthly bets ~ {d['n']/mo:.1f}")
