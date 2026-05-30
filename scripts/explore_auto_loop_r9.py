"""ラウンド9: 桐生 wd=6 × 既存 robust (5-1-2 / 4-5-2 / 1-2-3) 組合せ + 新仮説"""
import sys
import os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from src.verification.backtest import _conn

SPLIT = "2026-01-01"
PH = "%s" if os.environ.get("DATABASE_URL") else "?"


def roi_trifecta_combo(stadium, combo, where_extra="", args_extra=()):
    """3連単 指定 combo の単独購入 ROI (preview JOIN は boat=1 で 1 row/race)"""
    where = f"r.stadium_number={stadium} AND pv.boat_number=1"
    if where_extra:
        where += f" AND {where_extra}"
    where_te = f"{where} AND r.race_date >= {PH}"
    where_tr = f"{where} AND r.race_date < {PH}"

    def go(w, a):
        sql = f"""
SELECT COUNT(DISTINCT r.race_id) AS bets,
       SUM(CASE WHEN rpay.payout IS NOT NULL THEN 1 ELSE 0 END) AS hits,
       COALESCE(SUM(rpay.payout), 0) AS pay
FROM races r
LEFT JOIN race_previews pv ON pv.race_id=r.race_id AND pv.boat_number=1
LEFT JOIN race_payouts rpay
  ON rpay.race_id=r.race_id AND rpay.bet_type='trifecta' AND rpay.combination='{combo}'
WHERE {w}"""
        cur.execute(sql, a)
        b, h, p = cur.fetchone()
        return b, h, round(100.0 * p / max(1, 100 * b), 2) if b else 0

    cur = conn.cursor()
    b1, h1, r1 = go(where_tr, args_extra + (SPLIT,))
    b2, h2, r2 = go(where_te, args_extra + (SPLIT,))
    return b1, r1, b2, r2


def roi_win(stadium, boat, where_extra="", args_extra=()):
    where = f"r.stadium_number={stadium} AND pv.boat_number=1"
    if where_extra:
        where += f" AND {where_extra}"
    where_te = f"{where} AND r.race_date >= {PH}"
    where_tr = f"{where} AND r.race_date < {PH}"

    def go(w, a):
        sql = f"""
SELECT COUNT(DISTINCT r.race_id) AS bets,
       SUM(CASE WHEN rr.finishing_position=1 THEN 1 ELSE 0 END) AS hits,
       COALESCE(SUM(CASE WHEN rr.finishing_position=1 THEN rpay.payout ELSE 0 END), 0) AS pay
FROM races r
LEFT JOIN race_previews pv ON pv.race_id=r.race_id AND pv.boat_number=1
JOIN race_results rr ON rr.race_id=r.race_id AND rr.boat_number={boat}
LEFT JOIN race_payouts rpay
  ON rpay.race_id=r.race_id AND rpay.bet_type='win' AND rpay.combination='{boat}'
WHERE {w}"""
        cur.execute(sql, a)
        b, h, p = cur.fetchone()
        return b, h, round(100.0 * p / max(1, 100 * b), 2) if b else 0

    cur = conn.cursor()
    b1, h1, r1 = go(where_tr, args_extra + (SPLIT,))
    b2, h2, r2 = go(where_te, args_extra + (SPLIT,))
    return b1, r1, b2, r2


def evaluate(label, b1, r1, b2, r2, robust):
    icon = "🏆" if (r1 >= 120 and r2 >= 120 and b1 >= 30 and b2 >= 30) else (
        "⚠" if (r1 >= 100 and r2 >= 100) else "❌")
    print(f"  [{icon}] {label:<60} tr n={b1:>4} ROI={r1:>6.1f}% | te n={b2:>4} ROI={r2:>6.1f}%")
    if icon == "🏆":
        robust.append((label, b1, r1, b2, r2))


def main():
    global conn
    conn = _conn()
    print(f"=== ラウンド9 split={SPLIT} (n は真の race 数, threshold n>=30) ===\n")
    robust = []

    # --- 9-1. 桐生 5-1-2 robust × wd=6 で n が稼げるか ---
    print("--- 9-1. 桐生 5-1-2 系 × wd=6 ---")
    evaluate("基準: 5-1-2 (motor35 + 雨除外 + 国1≥6)",
             *roi_trifecta_combo(1, "5-1-2",
                "EXISTS (SELECT 1 FROM race_entries e1 WHERE e1.race_id=r.race_id AND e1.boat_number=1 AND e1.class_number=1 AND e1.assigned_motor_top_2_percent>=35 AND e1.national_top_1_percent>=6) AND (pv.weather_number IS NULL OR pv.weather_number != 3)"),
             robust)
    # wd=6 を追加
    evaluate("5-1-2 + wd=6",
             *roi_trifecta_combo(1, "5-1-2",
                "pv.wind_direction_number=6 AND EXISTS (SELECT 1 FROM race_entries e1 WHERE e1.race_id=r.race_id AND e1.boat_number=1 AND e1.class_number=1 AND e1.assigned_motor_top_2_percent>=35 AND e1.national_top_1_percent>=6)"),
             robust)
    # wd=6 のみ (他制約緩和)
    evaluate("5-1-2 + wd=6 (no class/motor)",
             *roi_trifecta_combo(1, "5-1-2", "pv.wind_direction_number=6"),
             robust)
    # wd!=6 (反対側 - 5-1-2 が他風向で robust か)
    evaluate("5-1-2 + wd!=6 (motor35 + 雨除外 + 国1≥6)",
             *roi_trifecta_combo(1, "5-1-2",
                "(pv.wind_direction_number IS NULL OR pv.wind_direction_number != 6) AND EXISTS (SELECT 1 FROM race_entries e1 WHERE e1.race_id=r.race_id AND e1.boat_number=1 AND e1.class_number=1 AND e1.assigned_motor_top_2_percent>=35 AND e1.national_top_1_percent>=6) AND (pv.weather_number IS NULL OR pv.weather_number != 3)"),
             robust)

    # --- 9-2. 桐生 4-5-2 × wd=6 ---
    print("\n--- 9-2. 桐生 4-5-2 系 × wd=6 ---")
    evaluate("基準: 4-5-2 (motor35 + 雨除外 + 国1≥6)",
             *roi_trifecta_combo(1, "4-5-2",
                "EXISTS (SELECT 1 FROM race_entries e1 WHERE e1.race_id=r.race_id AND e1.boat_number=1 AND e1.class_number=1 AND e1.assigned_motor_top_2_percent>=35 AND e1.national_top_1_percent>=6) AND (pv.weather_number IS NULL OR pv.weather_number != 3)"),
             robust)
    evaluate("4-5-2 + wd=6",
             *roi_trifecta_combo(1, "4-5-2",
                "pv.wind_direction_number=6 AND EXISTS (SELECT 1 FROM race_entries e1 WHERE e1.race_id=r.race_id AND e1.boat_number=1 AND e1.class_number=1 AND e1.assigned_motor_top_2_percent>=35 AND e1.national_top_1_percent>=6)"),
             robust)
    evaluate("4-5-2 + wd=6 (no class/motor)",
             *roi_trifecta_combo(1, "4-5-2", "pv.wind_direction_number=6"),
             robust)

    # --- 9-3. 桐生 wd=6 で全 4-x-y / 5-x-y を試す ---
    print("\n--- 9-3. 桐生 wd=6 × 4頭/5頭 head 3連単 ---")
    for combo in ["4-1-2", "4-1-3", "4-1-5", "4-2-1", "4-2-3", "4-3-1", "4-5-1", "4-5-2", "4-5-3", "4-5-6",
                   "5-1-2", "5-1-3", "5-1-4", "5-2-1", "5-3-1", "5-4-1", "5-4-2", "5-6-1", "5-6-2"]:
        evaluate(f"wd=6 × {combo}",
                 *roi_trifecta_combo(1, combo, "pv.wind_direction_number=6"),
                 robust)

    # --- 9-4. 桐生 wd=6 + 4号艇 補強で n が出る組合せ深掘り ---
    print("\n--- 9-4. 桐生 wd=6 + 4号艇 各種補強 × 単勝 ---")
    extras = [
        ("4号艇 motor≥30", "EXISTS (SELECT 1 FROM race_entries re WHERE re.race_id=r.race_id AND re.boat_number=4 AND re.assigned_motor_top_2_percent>=30)"),
        ("4号艇 motor≥35", "EXISTS (SELECT 1 FROM race_entries re WHERE re.race_id=r.race_id AND re.boat_number=4 AND re.assigned_motor_top_2_percent>=35)"),
        ("4号艇 国1≥5", "EXISTS (SELECT 1 FROM race_entries re WHERE re.race_id=r.race_id AND re.boat_number=4 AND re.national_top_1_percent>=5)"),
        ("4号艇 国1≥4.5", "EXISTS (SELECT 1 FROM race_entries re WHERE re.race_id=r.race_id AND re.boat_number=4 AND re.national_top_1_percent>=4.5)"),
        ("4号艇 motor30+国1≥5", "EXISTS (SELECT 1 FROM race_entries re WHERE re.race_id=r.race_id AND re.boat_number=4 AND re.assigned_motor_top_2_percent>=30 AND re.national_top_1_percent>=5)"),
        ("4号艇 motor35+国1≥5", "EXISTS (SELECT 1 FROM race_entries re WHERE re.race_id=r.race_id AND re.boat_number=4 AND re.assigned_motor_top_2_percent>=35 AND re.national_top_1_percent>=5)"),
        ("7-12R + motor30", "r.race_number IN (7,8,9,10,11,12) AND EXISTS (SELECT 1 FROM race_entries re WHERE re.race_id=r.race_id AND re.boat_number=4 AND re.assigned_motor_top_2_percent>=30)"),
        ("10-12R", "r.race_number IN (10,11,12)"),
    ]
    for lbl, extra in extras:
        evaluate(f"wd=6 + {lbl} 単勝4",
                 *roi_win(1, 4, f"pv.wind_direction_number=6 AND {extra}"),
                 robust)

    # --- 9-5. wd∈{6,10} ws<2 系展開 ---
    print("\n--- 9-5. 桐生 wd∈(6,10) ws<2 + 4号艇 補強 単勝 ---")
    base = "pv.wind_direction_number IN (6,10) AND pv.wind_speed<2"
    for lbl, extra in [
        ("国1≥5", "EXISTS (SELECT 1 FROM race_entries re WHERE re.race_id=r.race_id AND re.boat_number=4 AND re.national_top_1_percent>=5)"),
        ("motor≥30", "EXISTS (SELECT 1 FROM race_entries re WHERE re.race_id=r.race_id AND re.boat_number=4 AND re.assigned_motor_top_2_percent>=30)"),
        ("motor30+国1≥5", "EXISTS (SELECT 1 FROM race_entries re WHERE re.race_id=r.race_id AND re.boat_number=4 AND re.assigned_motor_top_2_percent>=30 AND re.national_top_1_percent>=5)"),
    ]:
        evaluate(f"wd∈(6,10) ws<2 + {lbl} 単勝4",
                 *roi_win(1, 4, f"{base} AND {extra}"),
                 robust)

    # --- 9-6. ws 帯別 ---
    print("\n--- 9-6. 桐生 風速帯別 4号艇 単勝 ---")
    for ws_lo, ws_hi, lbl in [(0, 2, "0-1m"), (2, 4, "2-3m"), (4, 6, "4-5m"), (6, 99, "6m+")]:
        evaluate(f"all wd ws {lbl}",
                 *roi_win(1, 4, f"pv.wind_speed>={ws_lo} AND pv.wind_speed<{ws_hi}"),
                 robust)

    # --- 9-7. weather × wd (晴れ風 vs 曇り風) ---
    print("\n--- 9-7. 桐生 weather × wd=6 ---")
    for w, lbl in [(1, "晴"), (2, "曇"), (3, "雨"), (4, "雪")]:
        evaluate(f"wd=6 + weather={w}({lbl}) 単勝4",
                 *roi_win(1, 4, f"pv.wind_direction_number=6 AND pv.weather_number={w}"),
                 robust)

    print(f"\n=== ラウンド9 robust: {len(robust)} ===")
    for l, b1, r1, b2, r2 in sorted(robust, key=lambda x: -x[4]):
        print(f"  tr={r1:.1f}% (n={b1}) / te={r2:.1f}% (n={b2})  {l}")

    conn.close()


if __name__ == "__main__":
    main()
