"""ラウンド10: 桐生 風向別 portfolio (wd=6 → 単勝4 / wd≠6 → 5-1-2) を統合 ROI 評価"""
import sys
import os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from src.verification.backtest import _conn

SPLIT = "2026-01-01"
PH = "%s" if os.environ.get("DATABASE_URL") else "?"


def portfolio_roi(stadium, conds_with_combo, date_lo, date_hi):
    """conds_with_combo: list of (label, where_extra, bet_type, combination)
    複数 bet を 1 race ごとに同時購入する場合の合計 ROI を返す。
    各 race ごとに bet_count × 100 を投入、SUM(payouts) を回収。"""
    total_bets = 0
    total_pay = 0
    for label, where_extra, bet_type, combo in conds_with_combo:
        where = f"r.stadium_number={stadium} AND pv.boat_number=1 AND r.race_date >= {PH} AND r.race_date <= {PH}"
        if where_extra:
            where += f" AND {where_extra}"
        sql = f"""
SELECT COUNT(DISTINCT r.race_id) AS bets,
       COALESCE(SUM(rpay.payout), 0) AS pay
FROM races r
LEFT JOIN race_previews pv ON pv.race_id=r.race_id AND pv.boat_number=1
LEFT JOIN race_payouts rpay
  ON rpay.race_id=r.race_id AND rpay.bet_type='{bet_type}' AND rpay.combination='{combo}'
WHERE {where}"""
        cur.execute(sql, (date_lo, date_hi))
        b, p = cur.fetchone()
        total_bets += b
        total_pay += int(p or 0)
    roi = round(100.0 * total_pay / max(1, 100 * total_bets), 2) if total_bets else 0
    return total_bets, total_pay, roi


def single_roi(stadium, where_extra, bet_type, combo, date_lo, date_hi):
    where = f"r.stadium_number={stadium} AND pv.boat_number=1 AND r.race_date >= {PH} AND r.race_date <= {PH}"
    if where_extra:
        where += f" AND {where_extra}"
    sql = f"""
SELECT COUNT(DISTINCT r.race_id) AS bets,
       COALESCE(SUM(rpay.payout), 0) AS pay
FROM races r
LEFT JOIN race_previews pv ON pv.race_id=r.race_id AND pv.boat_number=1
LEFT JOIN race_payouts rpay
  ON rpay.race_id=r.race_id AND rpay.bet_type='{bet_type}' AND rpay.combination='{combo}'
WHERE {where}"""
    cur.execute(sql, (date_lo, date_hi))
    b, p = cur.fetchone()
    p = int(p or 0)
    roi = round(100.0 * p / max(1, 100 * b), 2) if b else 0
    return b, p, roi


def single_roi_win(stadium, where_extra, boat, date_lo, date_hi):
    where = f"r.stadium_number={stadium} AND pv.boat_number=1 AND r.race_date >= {PH} AND r.race_date <= {PH}"
    if where_extra:
        where += f" AND {where_extra}"
    sql = f"""
SELECT COUNT(DISTINCT r.race_id) AS bets,
       COALESCE(SUM(CASE WHEN rr.finishing_position=1 THEN rpay.payout ELSE 0 END), 0) AS pay
FROM races r
LEFT JOIN race_previews pv ON pv.race_id=r.race_id AND pv.boat_number=1
JOIN race_results rr ON rr.race_id=r.race_id AND rr.boat_number={boat}
LEFT JOIN race_payouts rpay
  ON rpay.race_id=r.race_id AND rpay.bet_type='win' AND rpay.combination='{boat}'
WHERE {where}"""
    cur.execute(sql, (date_lo, date_hi))
    b, p = cur.fetchone()
    p = int(p or 0)
    roi = round(100.0 * p / max(1, 100 * b), 2) if b else 0
    return b, p, roi


def report(label, tr_b, tr_p, tr_r, te_b, te_p, te_r):
    icon = "🏆" if (tr_r >= 120 and te_r >= 120 and tr_b >= 30 and te_b >= 30) else (
        "⚠" if (tr_r >= 100 and te_r >= 100) else "❌")
    print(f"  [{icon}] {label:<55} tr n={tr_b:>4} pay={tr_p:>7} ROI={tr_r:>6.1f}% | te n={te_b:>4} pay={te_p:>7} ROI={te_r:>6.1f}%")
    return icon == "🏆"


def main():
    global conn, cur
    conn = _conn()
    cur = conn.cursor()
    print(f"=== ラウンド10 split={SPLIT} portfolio 評価 ===\n")

    # --- 10-1. 単独戦略 (おさらい) ---
    print("--- 10-1. 個別 (おさらい) ---")
    # 桐生 motor35+国1≥6+雨除外 base condition
    motor_cond = "EXISTS (SELECT 1 FROM race_entries e1 WHERE e1.race_id=r.race_id AND e1.boat_number=1 AND e1.class_number=1 AND e1.assigned_motor_top_2_percent>=35 AND e1.national_top_1_percent>=6) AND (pv.weather_number IS NULL OR pv.weather_number != 3)"

    DATES_TR = ("0000-01-01", "2025-12-31")
    DATES_TE = ("2026-01-01", "9999-12-31")

    tr = single_roi(1, motor_cond, "trifecta", "5-1-2", *DATES_TR)
    te = single_roi(1, motor_cond, "trifecta", "5-1-2", *DATES_TE)
    report("5-1-2 (motor35+国1≥6+雨除外)", *tr, *te)

    tr = single_roi(1, motor_cond, "trifecta", "4-5-2", *DATES_TR)
    te = single_roi(1, motor_cond, "trifecta", "4-5-2", *DATES_TE)
    report("4-5-2 (motor35+国1≥6+雨除外)", *tr, *te)

    tr = single_roi_win(1, "pv.wind_direction_number=6", 4, *DATES_TR)
    te = single_roi_win(1, "pv.wind_direction_number=6", 4, *DATES_TE)
    report("単勝 4 (wd=6 のみ)", *tr, *te)

    # --- 10-2. portfolio: 桐生 motor35+国1≥6+雨除外 で 5-1-2 + 4-5-2 併買 ---
    print("\n--- 10-2. Portfolio: 5-1-2 + 4-5-2 併買 ---")
    tr = portfolio_roi(1, [
        ("5-1-2", motor_cond, "trifecta", "5-1-2"),
        ("4-5-2", motor_cond, "trifecta", "4-5-2"),
    ], *DATES_TR)
    te = portfolio_roi(1, [
        ("5-1-2", motor_cond, "trifecta", "5-1-2"),
        ("4-5-2", motor_cond, "trifecta", "4-5-2"),
    ], *DATES_TE)
    report("5-1-2 + 4-5-2 併買", *tr, *te)

    # --- 10-3. portfolio: 5-1-2 (motor35+国1≥6+雨除外) + 単勝4 (wd=6) ---
    print("\n--- 10-3. Portfolio: 5-1-2 (motor35+国1≥6+雨除外) + 単勝4 (wd=6) ---")
    # この場合 wd=6 race は 5-1-2 + 単勝4 の 2 件、wd!=6 race は 5-1-2 のみ
    # 統合 ROI のため、wd=6 race の bet=2, wd!=6 race の bet=1 を別々に計算

    def combined_strategy(date_lo, date_hi):
        # wd!=6 races: 5-1-2 だけ買う
        where_a = f"{motor_cond} AND (pv.wind_direction_number IS NULL OR pv.wind_direction_number != 6)"
        a_b, a_p, _ = single_roi(1, where_a, "trifecta", "5-1-2", date_lo, date_hi)
        # wd=6 races (motor35+国1≥6+雨除外): 5-1-2 + 単勝4 両方
        where_b = f"{motor_cond} AND pv.wind_direction_number=6"
        b1_b, b1_p, _ = single_roi(1, where_b, "trifecta", "5-1-2", date_lo, date_hi)
        b2_b, b2_p, _ = single_roi_win(1, where_b, 4, date_lo, date_hi)
        # ベット数: wd!=6 races 1 件 + wd=6 races 2 件
        total_b = a_b + b1_b + b2_b
        total_p = a_p + b1_p + b2_p
        roi = round(100.0 * total_p / max(1, 100 * total_b), 2) if total_b else 0
        return total_b, total_p, roi, a_b, b1_b
    tr = combined_strategy(*DATES_TR)
    te = combined_strategy(*DATES_TE)
    print(f"  [tr] races (wd≠6)={tr[3]} (wd=6)={tr[4]}  total bets={tr[0]} pay={tr[1]} ROI={tr[2]:.1f}%")
    print(f"  [te] races (wd≠6)={te[3]} (wd=6)={te[4]}  total bets={te[0]} pay={te[1]} ROI={te[2]:.1f}%")

    # --- 10-4. portfolio: 桐生 wd=6 → 単勝4 + 桐生 wd!=6 → 5-1-2 (split) ---
    print("\n--- 10-4. Portfolio: split strategy (wd=6→単勝4 / wd≠6→5-1-2) ---")
    def split_strategy(date_lo, date_hi):
        # wd=6 races: 単勝4 だけ
        where_a = f"pv.wind_direction_number=6"
        a_b, a_p, ar = single_roi_win(1, where_a, 4, date_lo, date_hi)
        # wd!=6 races + motor35+国1≥6+雨除外: 5-1-2
        where_b = f"{motor_cond} AND (pv.wind_direction_number IS NULL OR pv.wind_direction_number != 6)"
        b_b, b_p, br = single_roi(1, where_b, "trifecta", "5-1-2", date_lo, date_hi)
        total_b = a_b + b_b
        total_p = a_p + b_p
        roi = round(100.0 * total_p / max(1, 100 * total_b), 2) if total_b else 0
        return total_b, total_p, roi, a_b, ar, b_b, br
    tr = split_strategy(*DATES_TR)
    te = split_strategy(*DATES_TE)
    print(f"  [tr] wd=6→単4 (n={tr[3]}, ROI {tr[4]:.1f}%) + wd≠6→5-1-2 (n={tr[5]}, ROI {tr[6]:.1f}%)  total bets={tr[0]} pay={tr[1]} ROI={tr[2]:.1f}%")
    print(f"  [te] wd=6→単4 (n={te[3]}, ROI {te[4]:.1f}%) + wd≠6→5-1-2 (n={te[5]}, ROI {te[6]:.1f}%)  total bets={te[0]} pay={te[1]} ROI={te[2]:.1f}%")

    # --- 10-5. 桐生 三段重ね portfolio: wd=6 → 単勝4, wd!=6+motor35 → 5-1-2, wd!=6+motor35 → 4-5-2 ---
    print("\n--- 10-5. 三段 portfolio (wd=6→単勝4, 残→5-1-2+4-5-2) ---")
    def triple_strategy(date_lo, date_hi):
        where_a = f"pv.wind_direction_number=6"
        a_b, a_p, _ = single_roi_win(1, where_a, 4, date_lo, date_hi)
        where_b = f"{motor_cond} AND (pv.wind_direction_number IS NULL OR pv.wind_direction_number != 6)"
        b1_b, b1_p, _ = single_roi(1, where_b, "trifecta", "5-1-2", date_lo, date_hi)
        b2_b, b2_p, _ = single_roi(1, where_b, "trifecta", "4-5-2", date_lo, date_hi)
        total_b = a_b + b1_b + b2_b
        total_p = a_p + b1_p + b2_p
        roi = round(100.0 * total_p / max(1, 100 * total_b), 2) if total_b else 0
        return total_b, total_p, roi, a_b, b1_b
    tr = triple_strategy(*DATES_TR)
    te = triple_strategy(*DATES_TE)
    print(f"  [tr] 単4 n={tr[3]} + 5-1-2/4-5-2 each n={tr[4]}  total bets={tr[0]} pay={tr[1]} ROI={tr[2]:.1f}%")
    print(f"  [te] 単4 n={te[3]} + 5-1-2/4-5-2 each n={te[4]}  total bets={te[0]} pay={te[1]} ROI={te[2]:.1f}%")

    # --- 10-6. もっと積極的: wd=6 + 4号艇強化条件 ---
    print("\n--- 10-6. wd=6 + 4号艇 motor30+国1≥5 → 単勝4 を加えた強化 split ---")
    def enhanced_split(date_lo, date_hi):
        # wd=6 + 4号艇 motor30+国1≥5 races: 単勝4
        where_a = "pv.wind_direction_number=6 AND EXISTS (SELECT 1 FROM race_entries re WHERE re.race_id=r.race_id AND re.boat_number=4 AND re.assigned_motor_top_2_percent>=30 AND re.national_top_1_percent>=5)"
        a_b, a_p, ar = single_roi_win(1, where_a, 4, date_lo, date_hi)
        where_b = f"{motor_cond} AND (pv.wind_direction_number IS NULL OR pv.wind_direction_number != 6)"
        b_b, b_p, br = single_roi(1, where_b, "trifecta", "5-1-2", date_lo, date_hi)
        c_b, c_p, cr = single_roi(1, where_b, "trifecta", "4-5-2", date_lo, date_hi)
        total_b = a_b + b_b + c_b
        total_p = a_p + b_p + c_p
        roi = round(100.0 * total_p / max(1, 100 * total_b), 2) if total_b else 0
        return total_b, total_p, roi, a_b, ar, b_b, br, c_b, cr
    tr = enhanced_split(*DATES_TR)
    te = enhanced_split(*DATES_TE)
    print(f"  [tr] wd=6+強化単4 n={tr[3]}({tr[4]:.0f}%) + 5-1-2 n={tr[5]}({tr[6]:.0f}%) + 4-5-2 n={tr[7]}({tr[8]:.0f}%)  total bets={tr[0]} pay={tr[1]} ROI={tr[2]:.1f}%")
    print(f"  [te] wd=6+強化単4 n={te[3]}({te[4]:.0f}%) + 5-1-2 n={te[5]}({te[6]:.0f}%) + 4-5-2 n={te[7]}({te[8]:.0f}%)  total bets={te[0]} pay={te[1]} ROI={te[2]:.1f}%")

    conn.close()


if __name__ == "__main__":
    main()
