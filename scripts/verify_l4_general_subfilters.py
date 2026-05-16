"""L4 一般戦 (grade=5) サブフィルタ別 ROI 検証 (直近 12ヶ月)

base:
  - 1号艇 class_number=1 (A1)
  - B除外会場 (stadium NOT IN {2,4,7,8,10,19,21,24})
  - race_grade_number=5 (一般戦)
  - fav_payout (MIN trifecta payout) in [500, 1000)
  - 1点100円ベット、3連単 1-2-3
"""
import os
os.environ.pop("DATABASE_URL", None)
os.environ["DATABASE_URL"] = ""

import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
import sqlite3

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

EXCLUDE_B = (2, 4, 7, 8, 10, 19, 21, 24)
COURSE1_WINDOW_DAYS = 180
COURSE1_MIN_STARTS = 20
COURSE1_THRESHOLD = 0.80

DB = "data/boatrace.db"
END_DATE = "2026-05-15"
START_DATE = "2025-05-16"


def main():
    conn = sqlite3.connect(DB)

    # base L4 一般戦 候補
    sql = f"""
        SELECT
            r.race_id, r.race_date, r.stadium_number,
            e.racer_number AS r1_number,
            e.avg_start_timing, e.age,
            e.national_top_1_percent, e.local_top_1_percent,
            e.assigned_motor_top_2_percent,
            res1.boat_number AS w1, res2.boat_number AS w2, res3.boat_number AS w3,
            pt.payout AS tri_pay,
            pv.weather_number,
            pv.exhibition_time AS ex_time_b1
        FROM races r
        JOIN race_entries e ON r.race_id=e.race_id AND e.boat_number=1
        JOIN (
            SELECT race_id, MIN(payout) AS min_pay
            FROM race_payouts WHERE bet_type='trifecta' GROUP BY race_id
        ) pp ON pp.race_id=r.race_id
        LEFT JOIN race_results res1 ON res1.race_id=r.race_id AND res1.finishing_position=1
        LEFT JOIN race_results res2 ON res2.race_id=r.race_id AND res2.finishing_position=2
        LEFT JOIN race_results res3 ON res3.race_id=r.race_id AND res3.finishing_position=3
        LEFT JOIN race_payouts pt ON pt.race_id=r.race_id AND pt.bet_type='trifecta' AND pt.combination='1-2-3'
        LEFT JOIN race_previews pv ON pv.race_id=r.race_id AND pv.boat_number=1
        WHERE e.class_number=1
          AND r.stadium_number NOT IN {EXCLUDE_B}
          AND r.race_grade_number=5
          AND pp.min_pay BETWEEN 500 AND 999
          AND r.race_date BETWEEN '{START_DATE}' AND '{END_DATE}'
        ORDER BY r.race_date
    """
    rows = conn.execute(sql).fetchall()
    print(f"L4 一般戦 base candidates: {len(rows)}")

    # 1コース履歴 (1c80 判定用)
    print("Loading 1c history...")
    cur = conn.execute("""
        SELECT e.racer_number, r.race_date, res.finishing_position
        FROM race_entries e
        JOIN races r ON e.race_id=r.race_id
        JOIN race_results res ON res.race_id=e.race_id AND res.boat_number=e.boat_number
        WHERE e.boat_number=1 AND res.finishing_position IS NOT NULL
    """)
    history = defaultdict(list)
    for racer, rd, place in cur.fetchall():
        history[racer].append((rd, 1 if place == 1 else 0))
    for k in history:
        history[k].sort()

    # exhibition_time 1st-place check: need all 6 boats in race_previews
    print("Loading exhibition time ranks...")
    cur = conn.execute("""
        SELECT race_id, boat_number, exhibition_time FROM race_previews
        WHERE exhibition_time IS NOT NULL
    """)
    ex_by_race = defaultdict(dict)
    for rid, bn, et in cur.fetchall():
        ex_by_race[rid][bn] = et

    # まとめ
    def new_bucket():
        return {"n": 0, "th": 0, "tp": 0}

    filters = [
        "1.ベースライン",
        "2.×1c80",
        "3.×L4 PRO (ST<0.16 & 30-49歳)",
        "4.×L4+ (国1%≥7)",
        "5.×L4++ (国1%+局1%≥7)",
        "6.×天候=曇 (w=2)",
        "7.×天候=晴 (w=1)",
        "8.×モーター2連率≥50%",
        "9.×展示タイム1位",
        "10.重畳: 1c80 × L4 PRO",
        "11.重畳: 1c80 × 曇",
    ]
    stats = {f: new_bucket() for f in filters}

    def is_1c80(racer, rdate):
        rh = history.get(racer, [])
        try:
            rd = datetime.fromisoformat(str(rdate)).date()
        except Exception:
            return False
        cutoff = (rd - timedelta(days=COURSE1_WINDOW_DAYS)).isoformat()
        past = [is_1st for d, is_1st in rh if cutoff <= d < str(rdate)]
        if len(past) < COURSE1_MIN_STARTS:
            return False
        return (sum(past) / len(past)) >= COURSE1_THRESHOLD

    for row in rows:
        (rid, rdate, sta, r1, ast, age, n1p, l1p, m2p,
         w1, w2, w3, tp, weather, ex_b1) = row
        if w1 is None or w2 is None or w3 is None:
            continue
        hit = (w1 == 1 and w2 == 2 and w3 == 3)
        pay = (tp or 0) if hit else 0

        # フラグ評価
        c_1c80 = is_1c80(r1, rdate)
        c_pro = (ast is not None and ast < 0.16
                 and age is not None and 30 <= age <= 49)
        c_lp = (n1p is not None and n1p >= 7.0)
        c_lpp = (n1p is not None and l1p is not None
                 and (n1p + l1p) >= 7.0)
        c_cloud = (weather == 2)
        c_sun = (weather == 1)
        c_mot50 = (m2p is not None and m2p >= 50.0)
        # 展示タイム 1位 (全6艇のデータが揃っていて最速)
        ex_d = ex_by_race.get(rid, {})
        if len(ex_d) >= 6 and ex_b1 is not None:
            c_extop = (ex_b1 == min(ex_d.values()))
        else:
            c_extop = False

        # 各フィルタに足し込み
        def add(key, ok):
            if ok:
                s = stats[key]
                s["n"] += 1
                if hit:
                    s["th"] += 1
                    s["tp"] += pay

        add("1.ベースライン", True)
        add("2.×1c80", c_1c80)
        add("3.×L4 PRO (ST<0.16 & 30-49歳)", c_pro)
        add("4.×L4+ (国1%≥7)", c_lp)
        add("5.×L4++ (国1%+局1%≥7)", c_lpp)
        add("6.×天候=曇 (w=2)", c_cloud)
        add("7.×天候=晴 (w=1)", c_sun)
        add("8.×モーター2連率≥50%", c_mot50)
        add("9.×展示タイム1位", c_extop)
        add("10.重畳: 1c80 × L4 PRO", c_1c80 and c_pro)
        add("11.重畳: 1c80 × 曇", c_1c80 and c_cloud)

    base = stats["1.ベースライン"]
    base_roi = (base["tp"] / (100 * base["n"]) * 100) if base["n"] else 0
    base_hit = (base["th"] / base["n"] * 100) if base["n"] else 0

    print(f"\n期間: {START_DATE} ～ {END_DATE}")
    print(f"\n## 一般戦サブフィルタ別 ROI 実測 (直近 12ヶ月)\n")
    print(f"| # | フィルタ | n | hit% | ROI | vs ベース |")
    print(f"|---|---|---:|---:|---:|---:|")
    for f in filters:
        s = stats[f]
        n = s["n"]
        if n == 0:
            print(f"| {f} | 0 | - | - | - |")
            continue
        hit_pct = s["th"] / n * 100
        roi = s["tp"] / (100 * n) * 100
        diff = roi - base_roi
        print(f"| {f} | {n} | {hit_pct:.1f}% | {roi:.1f}% | {diff:+.1f}pt |")

    print("\n## 有力候補 (ROI ≥ 200% かつ n ≥ 30)\n")
    found = False
    for f in filters:
        s = stats[f]
        n = s["n"]
        if n < 30: continue
        roi = s["tp"] / (100 * n) * 100
        if roi >= 200:
            found = True
            monthly = n / 12
            print(f"- {f}: n={n}, ROI={roi:.1f}%, 月次≈{monthly:.1f}件")
    if not found:
        print("(該当なし)")


if __name__ == "__main__":
    main()
