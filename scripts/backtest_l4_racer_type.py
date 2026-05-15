"""L4 [A1] レースで「1号艇選手のタイプ」別 ROI 検証。

検証する選手属性:
  A) 平均 ST (avg_start_timing): スタート型 (低 ST) vs 慎重型 (高 ST)
  B) フライング/出遅れ数 (flying_count, late_count): リスク取りタイプ
  C) 年齢: ベテラン vs 若手
  D) 体重: 軽量 vs 重量
  E) 展示タイム (exhibition_time): モーター乗り良い vs 悪い
  F) 展示 ST (start_timing_exhibition): スタート練習で良い・悪い

各 cohort で L4 [A1] の ROI を集計し、効くシグナルを抽出。

usage:
    python scripts/backtest_l4_racer_type.py
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


def update(b, w1, w2, w3, wp, ep, tp):
    b["n"] += 1
    if w1 == 1: b["wh"]+=1; b["wp"]+=(wp or 0)
    if w1==1 and w2==2: b["eh"]+=1; b["ep"]+=(ep or 0)
    if w1==1 and w2==2 and w3==3: b["th"]+=1; b["tp"]+=(tp or 0)


def report(name, buckets, order):
    print(f"\n=== {name} ===")
    print(f"{'cohort':12s} {'n':>5s} {'単勝':>7s} {'12連':>7s} {'1-2-3 ROI':>10s} {'1-2-3 損益':>11s}")
    print("-" * 60)
    for key in order:
        b = buckets.get(key, {"n":0})
        n = b["n"]
        if n == 0:
            print(f"{key:12s} {n:5d} (n=0)")
            continue
        wp_r = b["wp"]/(100*n)*100
        ep_r = b["ep"]/(100*n)*100
        tp_r = b["tp"]/(100*n)*100
        tp_p = b["tp"] - 100*n
        print(f"{key:12s} {n:5d} {wp_r:>6.1f}% {ep_r:>6.1f}% {tp_r:>9.1f}% {tp_p:>+11,}")


def main():
    with connect() as conn:
        print("Loading L4 [A1] + 1号艇選手属性 + 展示データ...")
        cur = conn.execute(f"""
            SELECT
                r.race_id,
                e.avg_start_timing,
                e.flying_count, e.late_count, e.age, e.weight,
                pv.exhibition_time, pv.start_timing_exhibition,
                res1.boat_number AS w1, res2.boat_number AS w2, res3.boat_number AS w3,
                pw.payout AS win_pay, pe.payout AS exa_pay, pt.payout AS tri_pay
            FROM races r
            JOIN race_entries e ON r.race_id=e.race_id AND e.boat_number=1
            JOIN (SELECT race_id, MIN(payout) AS min_pay FROM race_payouts WHERE bet_type='trifecta' GROUP BY race_id) pp ON pp.race_id=r.race_id
            LEFT JOIN race_previews pv ON pv.race_id=r.race_id AND pv.boat_number=1
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
        """)
        rows = cur.fetchall()
    print(f"  Total L4 [A1]: {len(rows)}")

    # cohort 定義
    bucket = lambda: {"n":0,"wh":0,"wp":0,"eh":0,"ep":0,"th":0,"tp":0}
    by_st = defaultdict(bucket)         # 平均 ST
    by_fl = defaultdict(bucket)         # フライング数
    by_age = defaultdict(bucket)        # 年齢
    by_wt = defaultdict(bucket)         # 体重
    by_ex_time = defaultdict(bucket)    # 展示タイム
    by_ex_st = defaultdict(bucket)      # 展示 ST

    for rid, ast, fly, lat, age, wt, ex_t, ex_st, w1, w2, w3, wp, ep, tp in rows:
        if w1 is None or w2 is None or w3 is None: continue
        args = (w1, w2, w3, wp, ep, tp)
        # 平均 ST
        if ast is not None:
            v = float(ast)
            if   v < 0.13: key="<0.13(超速)"
            elif v < 0.15: key="0.13-0.15"
            elif v < 0.17: key="0.15-0.17"
            elif v < 0.19: key="0.17-0.19"
            else:          key="≥0.19(慎重)"
            update(by_st[key], *args)
        # フライング
        if fly is not None:
            f = int(fly)
            if f == 0: key="0回"
            elif f == 1: key="1回"
            elif f == 2: key="2回"
            else: key="3回以上"
            update(by_fl[key], *args)
        # 年齢
        if age is not None:
            a = int(age)
            if   a < 25: key="<25"
            elif a < 30: key="25-29"
            elif a < 35: key="30-34"
            elif a < 40: key="35-39"
            elif a < 45: key="40-44"
            elif a < 50: key="45-49"
            else:        key="≥50"
            update(by_age[key], *args)
        # 体重
        if wt is not None:
            w = int(wt)
            if   w < 50: key="<50kg"
            elif w < 53: key="50-52"
            elif w < 55: key="53-54"
            elif w < 57: key="55-56"
            else:        key="≥57"
            update(by_wt[key], *args)
        # 展示タイム
        if ex_t is not None:
            t = float(ex_t)
            if   t < 6.7: key="<6.70 速"
            elif t < 6.8: key="6.70-6.79"
            elif t < 6.9: key="6.80-6.89"
            elif t < 7.0: key="6.90-6.99"
            else:         key="≥7.00 遅"
            update(by_ex_time[key], *args)
        # 展示 ST
        if ex_st is not None:
            s = float(ex_st)
            if   s < 0.10: key="<0.10(超速)"
            elif s < 0.14: key="0.10-0.13"
            elif s < 0.18: key="0.14-0.17"
            elif s < 0.22: key="0.18-0.21"
            else:          key="≥0.22(遅)"
            update(by_ex_st[key], *args)

    report("A) 1号艇 平均 ST (低=スタート型)", by_st,
           ["<0.13(超速)","0.13-0.15","0.15-0.17","0.17-0.19","≥0.19(慎重)"])
    report("B) フライング数 (キャリア通算)", by_fl,
           ["0回","1回","2回","3回以上"])
    report("C) 年齢", by_age,
           ["<25","25-29","30-34","35-39","40-44","45-49","≥50"])
    report("D) 体重", by_wt,
           ["<50kg","50-52","53-54","55-56","≥57"])
    report("E) 展示タイム (低=モーター速い)", by_ex_time,
           ["<6.70 速","6.70-6.79","6.80-6.89","6.90-6.99","≥7.00 遅"])
    report("F) 展示 ST (低=スタート練習良好)", by_ex_st,
           ["<0.10(超速)","0.10-0.13","0.14-0.17","0.18-0.21","≥0.22(遅)"])


if __name__ == "__main__":
    main()
