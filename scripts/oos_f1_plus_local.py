"""F1 strategy enhancement: add boat2 local_top_2_percent filter.

Base F1 (per user spec):
  - boat1 class_number = 1 (A1)
  - race_grade_number = 5 (general race)
  - boat1 national_top_1_percent >= 7.0
  - boat2 national_top_2_percent >= 40.0
  - min trifecta payout 500-1000 (favorite 1-2-3 odds 5.00-10.00)
  - exclude B-list stadiums (2,18,10,22,4,14,20,23)
  - exclude rain (race_previews.weather_number != 4)
  - bet trifecta 1-2-3 @ 100 yen

Period: 2022-04-01 ~ 2026-03-31 (4 year).

Additional filter being tested: boat2 local_top_2_percent >= X
  X in {None, 30, 35, 40, 45, 50}

Bootstrap 95% CI with 2000 iterations.
"""
import sqlite3
import random
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "data" / "boatrace.db"

START = "2022-04-01"
END = "2026-03-31"
BET = 100
ITERS = 2000
SEED = 42

# B-list stadium exclusion per user spec
EXCLUDE_STADIUMS = (2, 18, 10, 22, 4, 14, 20, 23)

BASE_SQL = f"""
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
  SELECT race_id,
         national_top_2_percent AS nat2_top2,
         local_top_2_percent    AS loc2_top2
    FROM race_entries WHERE boat_number=2
),
p1 AS (
  SELECT race_id, weather_number FROM race_previews WHERE boat_number=1
)
SELECT r.race_id, r.race_date, r.stadium_number,
       e1.nat1, e2.nat2_top2, e2.loc2_top2,
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
 WHERE r.race_grade_number=5
   AND e1.class_number=1
   AND e1.nat1 IS NOT NULL AND e1.nat1 >= 7.0
   AND e2.nat2_top2 IS NOT NULL AND e2.nat2_top2 >= 40.0
   AND r.stadium_number NOT IN {EXCLUDE_STADIUMS}
   AND min_tri.min_pay >= 500 AND min_tri.min_pay < 1000
   AND (p1.weather_number IS NULL OR p1.weather_number != 4)
   AND r.race_date BETWEEN ? AND ?
"""


def fetch_base(start: str, end: str):
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute(BASE_SQL, (start, end)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def evaluate(rows, local_thresh, bet=BET, iters=ITERS, seed=SEED):
    pnl = []
    hits = 0
    skipped_no_local = 0
    for r in rows:
        if local_thresh is not None:
            lt2 = r["loc2_top2"]
            if lt2 is None:
                skipped_no_local += 1
                continue
            if lt2 < local_thresh:
                continue
        pay = r["pay123"] if (r["fp1"] == 1 and r["fp2"] == 2 and r["fp3"] == 3) else 0
        if pay is None:
            pay = 0
        if pay > 0:
            hits += 1
        pnl.append(pay - bet)
    n = len(pnl)
    if n == 0:
        return {
            "n": 0, "hits": 0, "roi": None, "profit": 0,
            "ci_lo": None, "ci_hi": None, "skipped_no_local": skipped_no_local,
        }
    profit = sum(pnl)
    roi = profit / (n * bet) + 1.0
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
    return {
        "n": n, "hits": hits, "roi": roi, "profit": profit,
        "ci_lo": lo, "ci_hi": hi, "skipped_no_local": skipped_no_local,
    }


def fmt_row(label, d):
    if d["n"] == 0:
        return f"| {label} | 0 | 0 | - | 0 | - |"
    return (f"| {label} | {d['n']} | {d['hits']} | "
            f"{d['roi']*100:.1f}% | {d['profit']:+,} | "
            f"[{d['ci_lo']*100:.0f}%, {d['ci_hi']*100:.0f}%] |")


def main():
    print(f"DB: {DB}")
    print(f"Period: {START} ~ {END}")
    print("Loading F1 base data...")
    rows = fetch_base(START, END)
    print(f"  base n = {len(rows)}")
    # how many rows have local_top_2_percent
    with_loc = sum(1 for r in rows if r["loc2_top2"] is not None)
    print(f"  with local_top_2_percent: {with_loc} / {len(rows)}")

    results = []
    print("\n## F1 + boat2 local_top_2_percent threshold sweep\n")
    print("| 閾値 (当地 >= X) | n | hits | ROI | profit (yen) | bootstrap CI 95% |")
    print("|---|---|---|---|---|---|")

    d_base = evaluate(rows, None)
    print(fmt_row("なし (F1 base)", d_base))
    results.append(("base", d_base))

    for X in (30, 35, 40, 45, 50):
        d = evaluate(rows, float(X))
        print(fmt_row(str(X), d))
        results.append((str(X), d))

    print("\n## Diagnostics")
    for label, d in results:
        if d["n"] == 0:
            print(f"  {label}: n=0  skipped_no_local={d['skipped_no_local']}")
        else:
            print(f"  {label}: n={d['n']}  hit={d['hits']/d['n']*100:.2f}%  "
                  f"ROI={d['roi']*100:.1f}%  CI=[{d['ci_lo']*100:.1f}, {d['ci_hi']*100:.1f}]  "
                  f"skipped_no_local={d['skipped_no_local']}")

    # decision rules
    print("\n## Decision (per user rules)")
    print("  n>=500, CI_lo>=150%, maximize ROI")
    candidates = []
    for label, d in results:
        if label == "base":
            continue
        if d["n"] is None or d["n"] < 500:
            continue
        if d["ci_lo"] is None or d["ci_lo"] * 100 < 150:
            continue
        candidates.append((label, d))
    if not candidates:
        print("  -> No threshold satisfies both n>=500 and CI_lo>=150%.")
    else:
        best = max(candidates, key=lambda x: x[1]["roi"])
        print(f"  -> Best valid threshold: X={best[0]}  "
              f"ROI={best[1]['roi']*100:.1f}%  n={best[1]['n']}  "
              f"CI_lo={best[1]['ci_lo']*100:.1f}%")


if __name__ == "__main__":
    main()
