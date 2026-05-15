"""L4 [A1] レースで「1号艇モーター不調なのに本命」のときに 1号艇切りで穴狙い ROI を検証。

仮説:
  L4 候補 (本命 500-1000) は通常「1号艇 A1 で 1-2-3 本命」だが、
  もし 1号艇のモーター 2連率が低い (例: <30%) なら 1号艇 1着率が
  期待値より低く、穴 (2号艇 or 3号艇 1着) のパターンの方が利益が
  出るのではないか。

検証:
  1. L4 候補 (本命 500-1000 + A1 + B除外 + SG/G1/G2/G3) のうち
     1号艇モーター 2連率の閾値別に絞る
  2. 1号艇 1着率を集計
  3. 1号艇が 1着でない場合 (= 逆転、本命ハズレ) の払戻分布
  4. 穴目 (2-1-3, 3-1-2 等の 2着 1号艇パターン) の ROI を試算

usage:
    python scripts/backtest_l4_motor_weak_anti.py
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
        print("Loading L4 [A1] candidates with motor + all payouts...")
        cur = conn.execute(f"""
            SELECT
                r.race_id, r.race_date, r.race_grade_number,
                e.assigned_motor_top_2_percent AS motor_2rate,
                res1.boat_number AS w1, res2.boat_number AS w2, res3.boat_number AS w3,
                pp.min_pay AS fav_pay,
                pt.payout AS tri_pay_123,
                pt2.combination AS tri_combo, pt2.payout AS tri_pay_actual
            FROM races r
            JOIN race_entries e ON r.race_id=e.race_id AND e.boat_number=1
            JOIN (SELECT race_id, MIN(payout) AS min_pay FROM race_payouts WHERE bet_type='trifecta' GROUP BY race_id) pp ON pp.race_id=r.race_id
            LEFT JOIN race_results res1 ON res1.race_id=r.race_id AND res1.finishing_position=1
            LEFT JOIN race_results res2 ON res2.race_id=r.race_id AND res2.finishing_position=2
            LEFT JOIN race_results res3 ON res3.race_id=r.race_id AND res3.finishing_position=3
            LEFT JOIN race_payouts pt ON pt.race_id=r.race_id AND pt.bet_type='trifecta' AND pt.combination='1-2-3'
            LEFT JOIN race_payouts pt2 ON pt2.race_id=r.race_id AND pt2.bet_type='trifecta'
            WHERE e.class_number=1
              AND r.stadium_number NOT IN {EXCLUDE_B}
              AND r.race_grade_number IN (1,2,3,4)
              AND pp.min_pay BETWEEN 500 AND 999
            ORDER BY r.race_date
        """)
        rows = cur.fetchall()
    # race_id 単位で整理 (LEFT JOIN で行が複数になる場合 trifecta combo は通常 1 件)
    by_rid = {}
    for r in rows:
        rid = r[0]
        if rid not in by_rid:
            by_rid[rid] = r
    print(f"  Total L4 [A1] candidates: {len(by_rid)}")

    # モーター 2連率閾値別に「1号艇 1着率」+ 穴狙い (1号艇 非1着のときの平均払戻)
    bucket = lambda: {"n":0, "n_w1_1st":0, "n_w1_not_1st":0,
                      "tri_combos": defaultdict(int),  # 結果 combination の分布
                      "anti_payouts": []}  # 1号艇 非1着のときの 3連単払戻
    buckets = {
        "≥45%": bucket(),
        "40-45%": bucket(),
        "35-40%": bucket(),
        "30-35%": bucket(),
        "25-30%": bucket(),
        "<25%": bucket(),
        "不明": bucket(),
    }

    for rid, rdate, grade, m2, w1, w2, w3, fav_pay, tri_pay_123, tri_combo, tri_pay_actual in by_rid.values():
        if w1 is None or w2 is None or w3 is None: continue
        if m2 is None: key = "不明"
        else:
            r = float(m2)
            if r >= 45: key = "≥45%"
            elif r >= 40: key = "40-45%"
            elif r >= 35: key = "35-40%"
            elif r >= 30: key = "30-35%"
            elif r >= 25: key = "25-30%"
            else: key = "<25%"
        b = buckets[key]
        b["n"] += 1
        if w1 == 1:
            b["n_w1_1st"] += 1
        else:
            b["n_w1_not_1st"] += 1
            # 1号艇 非1着 → 穴 → 実際の 3連単払戻 (= tri_pay_actual)
            if tri_pay_actual:
                b["anti_payouts"].append(int(tri_pay_actual))
                combo = f"{w1}-{w2}-{w3}"
                b["tri_combos"][combo] += 1

    print(f"\n=== モーター 2連率別 1号艇 1着率 + 穴狙い payout ===")
    print(f"{'帯':10s} {'n':>5s} {'1号艇 1着':>10s} {'非1着 n':>8s} {'穴平均払戻':>10s} {'最頻穴 combo':>20s}")
    print("-" * 80)
    for key in ["≥45%","40-45%","35-40%","30-35%","25-30%","<25%","不明"]:
        b = buckets[key]
        n = b["n"]
        if n == 0: continue
        win_rate = b["n_w1_1st"] / n * 100
        anti_payouts = b["anti_payouts"]
        anti_avg = sum(anti_payouts) / len(anti_payouts) if anti_payouts else 0
        top_combo = max(b["tri_combos"].items(), key=lambda x: x[1])[0] if b["tri_combos"] else "-"
        print(f"{key:10s} {n:5d} {win_rate:>9.1f}% {b['n_w1_not_1st']:>7d}  ¥{anti_avg:>8.0f}  {top_combo}")

    # 穴狙い戦略: 「モーター不調 (<35%) のとき 1号艇 切って 2-1-3 を買う」
    print(f"\n=== 穴狙い戦略試算: モーター <35% のとき 2-1-3 を買う ===")
    weak_buckets = [buckets[k] for k in ["30-35%","25-30%","<25%"]]
    n_weak = sum(b["n"] for b in weak_buckets)
    n_213 = sum(b["tri_combos"].get("2-1-3", 0) for b in weak_buckets)
    pay_213 = 0
    # 2-1-3 の payouts 合算
    # buckets には tri_pay_actual の総和は持っていない → 再集計
    print(f"  L4 候補 (モーター <35%): {n_weak}")
    print(f"  うち 2-1-3 結果: {n_213} 件 ({n_213/n_weak*100:.1f}%)")
    print(f"  ※ 2-1-3 の payouts 取得は別クエリ必要 (本検証では combination 頻度のみ)")

    # 各帯で「2-1-3」「3-1-2」「2-3-1」など 1号艇切り combo の発生率
    print(f"\n=== 各モーター帯で発生した穴 combo (上位 5) ===")
    for key in ["<25%","25-30%","30-35%","≥45%"]:
        b = buckets[key]
        if not b["tri_combos"]: continue
        top5 = sorted(b["tri_combos"].items(), key=lambda x: -x[1])[:5]
        print(f"  {key:10s}: {top5}")


if __name__ == "__main__":
    main()
