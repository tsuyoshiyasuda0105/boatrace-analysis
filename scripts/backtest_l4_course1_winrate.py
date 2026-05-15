"""L4 [A1] レースで、1号艇選手の過去 1コース 1着率別に ROI を分析。

仮説: 「1コース逃げ切りが強い選手」が 1号艇に入った場合、L4 戦略の
期待値が上がる。

実装:
  1. L4 候補レース全件 (本命500-1000 + B除外 + 1号艇A1)
  2. 各レースの 1号艇 racer_number を取得
  3. その選手の「該当レース前」の 1コース 1着率を計算 (過去 N レース)
  4. 1コース 1着率の閾値別に 単勝/12連/123 の ROI 集計

usage:
    python scripts/backtest_l4_course1_winrate.py
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
        # L4 候補レース一覧 (race_id, race_date, 1号艇 racer_number, w1, w2, w3, payouts)
        print("Loading L4 candidate races...")
        cur = conn.execute(f"""
            SELECT
                r.race_id, r.race_date,
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
              AND r.race_grade_number IN (1,2,3,4)
              AND pp.min_pay BETWEEN 500 AND 999
            ORDER BY r.race_date
        """)
        l4_races = cur.fetchall()
        print(f"  L4 candidates: {len(l4_races)}")

        # 各選手の 1コース成績履歴 (累積)
        # racer_number -> [(race_date, is_winner)]
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
        history = defaultdict(list)  # racer_number -> [(date, is_1st), ...]
        for racer_number, race_date, place in cur.fetchall():
            is_1st = 1 if place == 1 else 0
            history[racer_number].append((race_date, is_1st))
        print(f"  選手数: {len(history)}")

    # 各 L4 レースで、1号艇選手の「該当レース前 50 戦」 1コース 1着率を計算
    # 閾値別に集計
    buckets = {
        "≥85%": {"n":0, "wp":0, "ep":0, "tp":0, "wh":0, "eh":0, "th":0},
        "80-85%": {"n":0, "wp":0, "ep":0, "tp":0, "wh":0, "eh":0, "th":0},
        "75-80%": {"n":0, "wp":0, "ep":0, "tp":0, "wh":0, "eh":0, "th":0},
        "70-75%": {"n":0, "wp":0, "ep":0, "tp":0, "wh":0, "eh":0, "th":0},
        "65-70%": {"n":0, "wp":0, "ep":0, "tp":0, "wh":0, "eh":0, "th":0},
        "<65%": {"n":0, "wp":0, "ep":0, "tp":0, "wh":0, "eh":0, "th":0},
        "n<10": {"n":0, "wp":0, "ep":0, "tp":0, "wh":0, "eh":0, "th":0},  # 履歴不足
    }

    for rid, rdate, r1, w1, w2, w3, wp, ep, tp in l4_races:
        if w1 is None or w2 is None or w3 is None:
            continue  # 未確定
        # その選手の race_date 以前の 1コース成績
        rh = history.get(r1, [])
        # race_date より前のレースのみ (look-ahead bias 防止)
        past = [is_1st for d, is_1st in rh if d < rdate]
        # 直近 50 戦に限定
        past = past[-50:]
        if len(past) < 10:
            bucket = "n<10"
        else:
            win_rate = sum(past) / len(past)
            if win_rate >= 0.85: bucket = "≥85%"
            elif win_rate >= 0.80: bucket = "80-85%"
            elif win_rate >= 0.75: bucket = "75-80%"
            elif win_rate >= 0.70: bucket = "70-75%"
            elif win_rate >= 0.65: bucket = "65-70%"
            else: bucket = "<65%"
        b = buckets[bucket]
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

    print(f"\n=== L4 [A1 + SG/G1/G2/G3 + B除外 + 本命500-1000] 内訳: 1号艇選手の過去50戦 1コース1着率別 ===")
    print(f"{'1コース1着率':10s} {'n':>5s} | 単勝 hit/n ROI 損益 | 12連 hit/n ROI 損益 | 123 hit/n ROI 損益")
    print("-" * 110)
    order = ["≥85%", "80-85%", "75-80%", "70-75%", "65-70%", "<65%", "n<10"]
    for key in order:
        b = buckets[key]
        n = b["n"]
        if n == 0:
            print(f"{key:10s} {n:5d} | (該当なし)")
            continue
        wp_pl = b["wp"] - 100*n
        ep_pl = b["ep"] - 100*n
        tp_pl = b["tp"] - 100*n
        wp_roi = b["wp"]/(100*n)*100
        ep_roi = b["ep"]/(100*n)*100
        tp_roi = b["tp"]/(100*n)*100
        print(f"{key:10s} {n:5d} | {b['wh']:3d}/{n:3d} {wp_roi:6.1f}% {wp_pl:+7,}円 | "
              f"{b['eh']:3d}/{n:3d} {ep_roi:6.1f}% {ep_pl:+7,}円 | "
              f"{b['th']:3d}/{n:3d} {tp_roi:6.1f}% {tp_pl:+7,}円")


if __name__ == "__main__":
    main()
