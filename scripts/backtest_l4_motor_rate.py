"""L4 [A1] レースで、1号艇のモーター 2連率別に ROI を分析。

仮説: モーター 2連率 50% 以上のレースに絞れば ROI が上がる。

バックテスト:
  1. L4 候補レース全件 + 1号艇のモーター 2連率取得
  2. 閾値別に 単勝/12連/123 ROI 集計
  3. 「2連率 ≥50%」での ROI と通常 ROI を比較

usage:
    python scripts/backtest_l4_motor_rate.py
"""
import os
os.environ.pop("DATABASE_URL", None)
os.environ["DATABASE_URL"] = ""

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.db.connection import connect

EXCLUDE_B = (2, 4, 7, 8, 10, 19, 21, 24)


def main():
    with connect() as conn:
        print("Loading L4 [A1] candidates + motor info...")
        cur = conn.execute(f"""
            SELECT
                r.race_id, r.race_date, r.race_grade_number,
                e.assigned_motor_top_2_percent AS motor_2rate,
                e.assigned_motor_top_3_percent AS motor_3rate,
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
              AND r.race_grade_number IN (1,2,3,4)
              AND pp.min_pay BETWEEN 500 AND 999
            ORDER BY r.race_date
        """)
        rows = cur.fetchall()
        print(f"  Total L4 [A1] candidates: {len(rows)}")

    bucket = lambda: {"n":0,"wh":0,"wp":0,"eh":0,"ep":0,"th":0,"tp":0}
    buckets = {
        "≥55%": bucket(),
        "50-55%": bucket(),
        "45-50%": bucket(),
        "40-45%": bucket(),
        "35-40%": bucket(),
        "30-35%": bucket(),
        "<30%": bucket(),
        "不明": bucket(),
    }

    def update(b, w1, w2, w3, wp, ep, tp):
        b["n"] += 1
        if w1 == 1: b["wh"]+=1; b["wp"]+=(wp or 0)
        if w1==1 and w2==2: b["eh"]+=1; b["ep"]+=(ep or 0)
        if w1==1 and w2==2 and w3==3: b["th"]+=1; b["tp"]+=(tp or 0)

    for rid, rdate, grade, m2, m3, w1, w2, w3, wp, ep, tp in rows:
        if w1 is None or w2 is None or w3 is None: continue
        if m2 is None:
            key = "不明"
        else:
            r = float(m2)
            if r >= 55: key = "≥55%"
            elif r >= 50: key = "50-55%"
            elif r >= 45: key = "45-50%"
            elif r >= 40: key = "40-45%"
            elif r >= 35: key = "35-40%"
            elif r >= 30: key = "30-35%"
            else: key = "<30%"
        update(buckets[key], w1, w2, w3, wp, ep, tp)

    print(f"\n=== L4 [A1] × 1号艇モーター 2連率 別 ROI ===")
    print(f"{'2連率':10s} {'n':>5s} {'単勝':>8s} {'12連':>8s} {'1-2-3 ROI':>10s} {'1-2-3 損益':>12s}")
    print("-" * 60)
    order = ["≥55%","50-55%","45-50%","40-45%","35-40%","30-35%","<30%","不明"]
    for key in order:
        b = buckets[key]
        n = b["n"]
        if n == 0: continue
        wp_r = b["wp"]/(100*n)*100
        ep_r = b["ep"]/(100*n)*100
        tp_r = b["tp"]/(100*n)*100
        tp_p = b["tp"] - 100*n
        print(f"{key:10s} {n:5d} {wp_r:>7.1f}% {ep_r:>7.1f}% {tp_r:>9.1f}% {tp_p:>+12,}")

    # 集約: ≥50% vs <50%
    print(f"\n=== 集約: モーター 2連率 ≥50% vs <50% ===")
    over_n = sum(buckets[k]["n"] for k in ["≥55%","50-55%"])
    over_wp = sum(buckets[k]["wp"] for k in ["≥55%","50-55%"])
    over_ep = sum(buckets[k]["ep"] for k in ["≥55%","50-55%"])
    over_tp = sum(buckets[k]["tp"] for k in ["≥55%","50-55%"])
    under_n = sum(buckets[k]["n"] for k in ["45-50%","40-45%","35-40%","30-35%","<30%"])
    under_wp = sum(buckets[k]["wp"] for k in ["45-50%","40-45%","35-40%","30-35%","<30%"])
    under_tp = sum(buckets[k]["tp"] for k in ["45-50%","40-45%","35-40%","30-35%","<30%"])

    if over_n:
        wp_r = over_wp/(100*over_n)*100
        tp_r = over_tp/(100*over_n)*100
        print(f"  ≥50% n={over_n}, 単勝 {wp_r:.1f}%, 1-2-3 ROI {tp_r:.1f}%, 損益 {over_tp-100*over_n:+,}")
    if under_n:
        wp_r = under_wp/(100*under_n)*100
        tp_r = under_tp/(100*under_n)*100
        print(f"  <50% n={under_n}, 単勝 {wp_r:.1f}%, 1-2-3 ROI {tp_r:.1f}%, 損益 {under_tp-100*under_n:+,}")


if __name__ == "__main__":
    main()
