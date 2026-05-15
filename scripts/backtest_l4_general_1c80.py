"""L4 [A1] (grade 問わず) × 1コース1着率 80%+ の ROI 検証。

仮説: 一般戦 (grade=5) で ROI 147.7% (低め) だが、1コース1着率 80%
以上の選手に絞れば ROI が大幅改善するのではないか。

backtest:
  1. L4 base (本命500-1000 + B除外 + 1号艇A1) を grade 別に分類
  2. 各 grade で「1コース1着率 ≥80% (過去 180 日、20 戦以上)」フィルタ
  3. フィルタ前/後の ROI 比較

usage:
    python scripts/backtest_l4_general_1c80.py
"""
import os
os.environ.pop("DATABASE_URL", None)
os.environ["DATABASE_URL"] = ""

import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.db.connection import connect

EXCLUDE_B = (2, 4, 7, 8, 10, 19, 21, 24)
COURSE1_WINDOW_DAYS = 180
COURSE1_MIN_STARTS = 20
COURSE1_THRESHOLD = 0.80


def main():
    with connect() as conn:
        # L4 候補レース (grade 問わず、A1+本命500-1000+B除外)
        print("Loading L4 candidates (all grades)...")
        cur = conn.execute(f"""
            SELECT
                r.race_id, r.race_date, r.race_grade_number,
                e.racer_number AS r1_number,
                res1.boat_number AS w1, res2.boat_number AS w2, res3.boat_number AS w3,
                pw.payout AS win_pay, pe.payout AS exa_pay, pt.payout AS tri_pay
            FROM races r
            JOIN race_entries e ON r.race_id=e.race_id AND e.boat_number=1
            JOIN (SELECT race_id, MIN(payout) AS min_pay FROM race_payouts WHERE bet_type='trifecta' GROUP BY race_id) pp ON pp.race_id=r.race_id
            LEFT JOIN race_results res1 ON res1.race_id=r.race_id AND res1.finishing_position=1
            LEFT JOIN race_results res2 ON res2.race_id=r.race_id AND res2.finishing_position=2
            LEFT JOIN race_results res3 ON res3.race_id=r.race_id AND res3.finishing_position=3
            LEFT JOIN race_payouts pw ON pw.race_id=r.race_id AND pw.bet_type='win' AND pw.combination='1'
            LEFT JOIN race_payouts pe ON pe.race_id=r.race_id AND pe.bet_type='exacta' AND pe.combination='1-2'
            LEFT JOIN race_payouts pt ON pt.race_id=r.race_id AND pt.bet_type='trifecta' AND pt.combination='1-2-3'
            WHERE e.class_number=1
              AND r.stadium_number NOT IN {EXCLUDE_B}
              AND pp.min_pay BETWEEN 500 AND 999
            ORDER BY r.race_date
        """)
        l4_races = cur.fetchall()
        print(f"  Total L4 [A1] candidates (all grades): {len(l4_races)}")

        print("Loading 1コース履歴...")
        cur = conn.execute("""
            SELECT e.racer_number, r.race_date, res.finishing_position
            FROM race_entries e
            JOIN races r ON e.race_id=r.race_id
            JOIN race_results res ON res.race_id=e.race_id AND res.boat_number=e.boat_number
            WHERE e.boat_number=1
              AND res.finishing_position IS NOT NULL
            ORDER BY e.racer_number, r.race_date
        """)
        history = defaultdict(list)
        for racer_number, race_date, place in cur.fetchall():
            history[racer_number].append((race_date, 1 if place == 1 else 0))
        print(f"  Racers: {len(history)}")

    # grade 別 × 1c80 該当別に集計
    GRADE_NAMES = {1: "SG", 2: "G1", 3: "G2", 4: "G3", 5: "一般戦", None: "grade不明"}
    # buckets[grade][is_1c80] = stats
    buckets = defaultdict(lambda: {
        "all":   {"n":0, "wh":0, "wp":0, "eh":0, "ep":0, "th":0, "tp":0},
        "1c80":  {"n":0, "wh":0, "wp":0, "eh":0, "ep":0, "th":0, "tp":0},
        "below": {"n":0, "wh":0, "wp":0, "eh":0, "ep":0, "th":0, "tp":0},
    })

    for rid, rdate, grade, r1, w1, w2, w3, wp, ep, tp in l4_races:
        if w1 is None or w2 is None or w3 is None:
            continue
        rh = history.get(r1, [])
        # race_date 以前、180 日以内
        cutoff = None
        try:
            from datetime import datetime
            rd = datetime.fromisoformat(str(rdate)).date()
            cutoff = (rd - timedelta(days=COURSE1_WINDOW_DAYS)).isoformat()
        except Exception:
            cutoff = "1900-01-01"
        past = [is_1st for d, is_1st in rh if cutoff <= d < str(rdate)]
        meets_min = len(past) >= COURSE1_MIN_STARTS
        is_1c80 = meets_min and (sum(past) / len(past) >= COURSE1_THRESHOLD)

        gkey = grade
        for layer in ["all", ("1c80" if is_1c80 else "below")]:
            b = buckets[gkey][layer]
            b["n"] += 1
            if w1 == 1:
                b["wh"] += 1
                b["wp"] += (wp or 0)
            if w1 == 1 and w2 == 2:
                b["eh"] += 1
                b["ep"] += (ep or 0)
            if w1 == 1 and w2 == 2 and w3 == 3:
                b["th"] += 1
                b["tp"] += (tp or 0)

    print(f"\n=== L4 [A1 + 本命500-1000 + B除外] × grade × 1c80 別 ROI (3連単 1-2-3) ===")
    print(f"集計期間: 過去 {COURSE1_WINDOW_DAYS} 日、最低 {COURSE1_MIN_STARTS} 戦、閾値 {COURSE1_THRESHOLD*100:.0f}%")
    print()
    print(f"{'grade':10s} {'cohort':8s} {'n':>5s} {'単勝 ROI':>9s} {'12連 ROI':>9s} {'123 ROI':>9s} {'123 損益':>10s}")
    print("-" * 90)
    for g in [1, 2, 3, 4, 5, None]:
        bdict = buckets.get(g)
        if not bdict: continue
        for layer in ["all", "1c80", "below"]:
            b = bdict[layer]
            n = b["n"]
            if n == 0:
                continue
            wp_roi = b["wp"]/(100*n)*100
            ep_roi = b["ep"]/(100*n)*100
            tp_roi = b["tp"]/(100*n)*100
            tp_pl = b["tp"] - 100*n
            label = {"all":"全 L4","1c80":"🚀1c80","below":"通常"}[layer]
            print(f"{GRADE_NAMES.get(g, str(g)):10s} {label:8s} {n:5d} {wp_roi:>8.1f}% {ep_roi:>8.1f}% {tp_roi:>8.1f}% {tp_pl:+10,}")


if __name__ == "__main__":
    main()
