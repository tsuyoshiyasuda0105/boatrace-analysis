"""L4 [A1] × 選手会場別1着率 × 天候 別 ROI 検証。

仮説:
  A) 選手 X が会場 Y で 1コース 1着率 80%+ なら ROI 上昇するか
  B) 天候 (晴/曇/雨/雪) で ROI 変化するか
  C) 会場別 1着率 + 天候 の組み合わせで ROI 上昇するか

集計期間:
  - 選手×会場 1コース 1着率: 過去 180 日、最低 5 戦
  - 天候別 ROI: 全 L4 [A1] (grade不問) を母数

usage:
    python scripts/backtest_l4_stadium_weather.py
"""
import os
os.environ.pop("DATABASE_URL", None)
os.environ["DATABASE_URL"] = ""

import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.db.connection import connect

EXCLUDE_B = (2, 4, 7, 8, 10, 19, 21, 24)
WINDOW_DAYS = 180
MIN_STARTS_SV = 5  # 会場別は最低 5 戦
WEATHER_MAP = {1: "晴", 2: "曇", 3: "雨", 4: "雪", None: "不明"}


def fmt_roi(b, n):
    if n == 0: return "  -    -      "
    wp_roi = b["wp"]/(100*n)*100
    tp_roi = b["tp"]/(100*n)*100
    tp_pl = b["tp"] - 100*n
    return f"{n:5d}  単{wp_roi:5.1f}% 123 {tp_roi:6.1f}% {tp_pl:+10,}"


def update_bucket(b, w1, w2, w3, wp, ep, tp):
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


def main():
    with connect() as conn:
        # L4 候補レース全件 + 天候 + 1号艇選手 ID
        print("Loading L4 [A1] candidates + weather...")
        cur = conn.execute(f"""
            SELECT
                r.race_id, r.race_date, r.stadium_number, r.race_grade_number,
                e.racer_number AS r1_number,
                pv.weather_number,
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
            ORDER BY r.race_date
        """)
        l4_races = cur.fetchall()
        print(f"  L4 candidates (strict, SG/G1/G2/G3): {len(l4_races)}")

        # 選手×会場 1コース履歴 (会場別)
        print("Loading 選手×会場 1コース履歴...")
        cur = conn.execute("""
            SELECT e.racer_number, r.stadium_number, r.race_date, res.finishing_position
            FROM race_entries e
            JOIN races r ON e.race_id=r.race_id
            JOIN race_results res ON res.race_id=e.race_id AND res.boat_number=e.boat_number
            WHERE e.boat_number=1
              AND res.finishing_position IS NOT NULL
        """)
        # (racer_number, stadium) -> [(date, is_1st)]
        sv_history = defaultdict(list)
        # racer_number -> [(date, is_1st)] (会場不問)
        global_history = defaultdict(list)
        for racer_number, stadium, race_date, place in cur.fetchall():
            is_1st = 1 if place == 1 else 0
            sv_history[(racer_number, stadium)].append((race_date, is_1st))
            global_history[racer_number].append((race_date, is_1st))
        print(f"  選手×会場 ペア: {len(sv_history)}")

    # 各 race について cohort 分類
    # A: 選手×会場 1コース 1着率
    # B: 天候
    # C: 組み合わせ

    bucket_template = lambda: {"n":0,"wh":0,"wp":0,"eh":0,"ep":0,"th":0,"tp":0}

    # A: 選手×会場 1着率閾値別
    by_sv_winrate = {
        "≥85%": bucket_template(),
        "75-85%": bucket_template(),
        "60-75%": bucket_template(),
        "<60%": bucket_template(),
        "n<5": bucket_template(),
    }
    # B: 天候別
    by_weather = defaultdict(bucket_template)
    # C: 組み合わせ (天候 × 1着率高)
    by_combo = defaultdict(bucket_template)

    for rid, rdate, stadium, grade, r1, wn, w1, w2, w3, wp, ep, tp in l4_races:
        if w1 is None or w2 is None or w3 is None:
            continue
        # 選手×会場 1コース 1着率 (過去 180 日)
        try:
            rd = datetime.fromisoformat(str(rdate)).date()
            cutoff = (rd - timedelta(days=WINDOW_DAYS)).isoformat()
        except Exception:
            cutoff = "1900-01-01"
        sv_past = [is_1st for d, is_1st in sv_history.get((r1, stadium), []) if cutoff <= d < str(rdate)]
        n_sv = len(sv_past)
        if n_sv < MIN_STARTS_SV:
            sv_bucket = "n<5"
            sv_rate = None
        else:
            rate = sum(sv_past) / n_sv
            sv_rate = rate
            if rate >= 0.85: sv_bucket = "≥85%"
            elif rate >= 0.75: sv_bucket = "75-85%"
            elif rate >= 0.60: sv_bucket = "60-75%"
            else: sv_bucket = "<60%"

        weather = WEATHER_MAP.get(wn, "不明")

        update_bucket(by_sv_winrate[sv_bucket], w1, w2, w3, wp, ep, tp)
        update_bucket(by_weather[weather], w1, w2, w3, wp, ep, tp)
        # 組み合わせ: 1着率 75%+ × 天候
        if sv_rate is not None and sv_rate >= 0.75:
            combo_key = f"sv≥75% × {weather}"
            update_bucket(by_combo[combo_key], w1, w2, w3, wp, ep, tp)
        else:
            combo_key = f"sv<75% × {weather}"
            update_bucket(by_combo[combo_key], w1, w2, w3, wp, ep, tp)

    print(f"\n=== A: 選手×会場 1コース 1着率 別 (過去{WINDOW_DAYS}日, 最低{MIN_STARTS_SV}戦) ===")
    print(f"{'cohort':10s} {fmt_roi.__doc__ if False else 'n     単勝   3連単 1-2-3  損益'}")
    for key in ["≥85%", "75-85%", "60-75%", "<60%", "n<5"]:
        b = by_sv_winrate[key]
        print(f"{key:10s} {fmt_roi(b, b['n'])}")

    print(f"\n=== B: 天候別 ===")
    for w in ["晴", "曇", "雨", "雪", "不明"]:
        b = by_weather.get(w)
        if b is None or b["n"]==0: continue
        print(f"{w:5s}      {fmt_roi(b, b['n'])}")

    print(f"\n=== C: 組み合わせ (選手×会場 1着率 75%+ × 天候) ===")
    keys = sorted(by_combo.keys())
    for k in keys:
        b = by_combo[k]
        if b["n"] == 0: continue
        print(f"{k:30s} {fmt_roi(b, b['n'])}")


if __name__ == "__main__":
    main()
