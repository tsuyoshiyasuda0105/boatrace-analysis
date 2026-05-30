"""ラウンド11: 会場横断 portfolio 統合 + 蒲郡 1-2-3 / 桐生 portfolio / 戸田 A2 ROI 評価"""
import sys
import os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from src.verification.backtest import _conn

SPLIT = "2026-01-01"
PH = "%s" if os.environ.get("DATABASE_URL") else "?"

KIRYU_MOTOR = "EXISTS (SELECT 1 FROM race_entries e1 WHERE e1.race_id=r.race_id AND e1.boat_number=1 AND e1.class_number=1 AND e1.assigned_motor_top_2_percent>=35 AND e1.national_top_1_percent>=6) AND (pv.weather_number IS NULL OR pv.weather_number != 3)"
KAMA_MOTOR = "EXISTS (SELECT 1 FROM race_entries e1 WHERE e1.race_id=r.race_id AND e1.boat_number=1 AND e1.class_number=1 AND e1.assigned_motor_top_2_percent>=35 AND e1.national_top_1_percent>=6) AND (pv.weather_number IS NULL OR pv.weather_number != 3)"
TODA_A2 = "EXISTS (SELECT 1 FROM race_entries e1 WHERE e1.race_id=r.race_id AND e1.boat_number=1 AND e1.class_number=2 AND e1.national_top_1_percent>=6)"


def single_roi_combo(stadium, where_extra, bet_type, combo, date_lo, date_hi):
    where = f"r.stadium_number={stadium} AND pv.boat_number=1 AND r.race_date >= {PH} AND r.race_date <= {PH}"
    if where_extra:
        where += f" AND {where_extra}"
    sql = f"""
SELECT COUNT(DISTINCT r.race_id), COALESCE(SUM(rpay.payout), 0)
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
SELECT COUNT(DISTINCT r.race_id),
       COALESCE(SUM(CASE WHEN rr.finishing_position=1 THEN rpay.payout ELSE 0 END), 0)
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
    print(f"  [{icon}] {label:<55} tr n={tr_b:>4} ROI={tr_r:>6.1f}% | te n={te_b:>4} ROI={te_r:>6.1f}%")


def main():
    global conn, cur
    conn = _conn()
    cur = conn.cursor()
    print(f"=== ラウンド11 split={SPLIT} 会場横断 portfolio ===\n")

    DATES_TR = ("0000-01-01", "2025-12-31")
    DATES_TE = ("2026-01-01", "9999-12-31")

    # --- 11-1. 蒲郡 1-2-3 単独 (motor35+国1≥6+雨除外) 再確認 ---
    print("--- 11-1. 蒲郡 1-2-3 (motor35+国1≥6+雨除外) ---")
    tr = single_roi_combo(7, KAMA_MOTOR, "trifecta", "1-2-3", *DATES_TR)
    te = single_roi_combo(7, KAMA_MOTOR, "trifecta", "1-2-3", *DATES_TE)
    report("蒲郡 1-2-3", *tr, *te)

    # --- 11-2. 戸田 A2 国1≥6 1-2-3 再確認 ---
    print("\n--- 11-2. 戸田 A2 国1≥6 1-2-3 ---")
    tr = single_roi_combo(2, TODA_A2, "trifecta", "1-2-3", *DATES_TR)
    te = single_roi_combo(2, TODA_A2, "trifecta", "1-2-3", *DATES_TE)
    report("戸田 A2 国1≥6 1-2-3", *tr, *te)

    # --- 11-3. 桐生 三段 portfolio ---
    print("\n--- 11-3. 桐生 三段 portfolio (確認) ---")
    def kiryu_triple(date_lo, date_hi):
        a_b, a_p, ar = single_roi_win(1, "pv.wind_direction_number=6", 4, date_lo, date_hi)
        wnot6 = f"{KIRYU_MOTOR} AND (pv.wind_direction_number IS NULL OR pv.wind_direction_number != 6)"
        b1_b, b1_p, b1r = single_roi_combo(1, wnot6, "trifecta", "5-1-2", date_lo, date_hi)
        b2_b, b2_p, b2r = single_roi_combo(1, wnot6, "trifecta", "4-5-2", date_lo, date_hi)
        total_b = a_b + b1_b + b2_b
        total_p = a_p + b1_p + b2_p
        roi = round(100.0 * total_p / max(1, 100 * total_b), 2) if total_b else 0
        return total_b, total_p, roi
    tr_kt = kiryu_triple(*DATES_TR)
    te_kt = kiryu_triple(*DATES_TE)
    report("桐生 三段 portfolio", *tr_kt, *te_kt)

    # --- 11-4. 全会場 統合 portfolio (桐生三段 + 蒲郡 1-2-3 + 戸田 A2 1-2-3) ---
    print("\n--- 11-4. 全会場 統合 portfolio ---")
    def all_portfolio(date_lo, date_hi):
        b1, p1, _ = kiryu_triple(date_lo, date_hi)[:3]
        # actually kiryu_triple returns (b, p, roi), so unpack right
        c1, c1p, _ = single_roi_combo(7, KAMA_MOTOR, "trifecta", "1-2-3", date_lo, date_hi)
        d1, d1p, _ = single_roi_combo(2, TODA_A2, "trifecta", "1-2-3", date_lo, date_hi)
        total_b = b1 + c1 + d1
        total_p = p1 + c1p + d1p
        roi = round(100.0 * total_p / max(1, 100 * total_b), 2) if total_b else 0
        return total_b, total_p, roi
    tr_all = all_portfolio(*DATES_TR)
    te_all = all_portfolio(*DATES_TE)
    report("全会場統合 portfolio", *tr_all, *te_all)

    # --- 11-5. 蒲郡 1-2-3 を後半R 限定 (Round 3 で robust) ---
    print("\n--- 11-5. 蒲郡 1-2-3 (motor35+国1≥6+雨除外) + 7-12R ---")
    tr = single_roi_combo(7, f"{KAMA_MOTOR} AND r.race_number IN (7,8,9,10,11,12)", "trifecta", "1-2-3", *DATES_TR)
    te = single_roi_combo(7, f"{KAMA_MOTOR} AND r.race_number IN (7,8,9,10,11,12)", "trifecta", "1-2-3", *DATES_TE)
    report("蒲郡 1-2-3 + 7-12R", *tr, *te)

    # --- 11-6. 全会場 portfolio + 蒲郡 7-12R 強化 ---
    print("\n--- 11-6. 全会場 portfolio (蒲郡=7-12R 限定) ---")
    def all_portfolio_v2(date_lo, date_hi):
        b1, p1, _ = kiryu_triple(date_lo, date_hi)[:3]
        c1, c1p, _ = single_roi_combo(7, f"{KAMA_MOTOR} AND r.race_number IN (7,8,9,10,11,12)", "trifecta", "1-2-3", date_lo, date_hi)
        d1, d1p, _ = single_roi_combo(2, TODA_A2, "trifecta", "1-2-3", date_lo, date_hi)
        total_b = b1 + c1 + d1
        total_p = p1 + c1p + d1p
        roi = round(100.0 * total_p / max(1, 100 * total_b), 2) if total_b else 0
        return total_b, total_p, roi
    tr = all_portfolio_v2(*DATES_TR)
    te = all_portfolio_v2(*DATES_TE)
    report("全会場v2 (蒲郡=7-12R)", *tr, *te)

    # --- 11-7. 蒲郡 1-3-2 / 1-2-4 など他 finish pattern も探索 ---
    print("\n--- 11-7. 蒲郡 (motor35+国1≥6+雨除外) 他 finish pattern ---")
    for combo in ["1-2-3", "1-3-2", "1-2-4", "1-4-2", "1-3-4", "2-1-3"]:
        tr = single_roi_combo(7, KAMA_MOTOR, "trifecta", combo, *DATES_TR)
        te = single_roi_combo(7, KAMA_MOTOR, "trifecta", combo, *DATES_TE)
        report(f"蒲郡 {combo}", *tr, *te)

    # --- 11-8. 桐生 4-5-2 base + wd 切り分け ---
    print("\n--- 11-8. 桐生 4-5-2 base × wd 切り分け ---")
    tr = single_roi_combo(1, f"{KIRYU_MOTOR} AND pv.wind_direction_number=6", "trifecta", "4-5-2", *DATES_TR)
    te = single_roi_combo(1, f"{KIRYU_MOTOR} AND pv.wind_direction_number=6", "trifecta", "4-5-2", *DATES_TE)
    report("桐生 4-5-2 (motor35+国1≥6+雨除外) wd=6", *tr, *te)
    tr = single_roi_combo(1, f"{KIRYU_MOTOR} AND (pv.wind_direction_number IS NULL OR pv.wind_direction_number != 6)", "trifecta", "4-5-2", *DATES_TR)
    te = single_roi_combo(1, f"{KIRYU_MOTOR} AND (pv.wind_direction_number IS NULL OR pv.wind_direction_number != 6)", "trifecta", "4-5-2", *DATES_TE)
    report("桐生 4-5-2 (motor35+国1≥6+雨除外) wd≠6", *tr, *te)

    # --- 11-9. 蒲郡 + wd 切り分けも? ---
    print("\n--- 11-9. 蒲郡 1-2-3 (motor35+国1≥6+雨除外) × wd 切り分け ---")
    tr = single_roi_combo(7, f"{KAMA_MOTOR} AND pv.wind_direction_number IS NOT NULL", "trifecta", "1-2-3", *DATES_TR)
    te = single_roi_combo(7, f"{KAMA_MOTOR} AND pv.wind_direction_number IS NOT NULL", "trifecta", "1-2-3", *DATES_TE)
    report("蒲郡 wd 既知のみ", *tr, *te)
    for wd in [2, 4, 6, 8, 10, 12, 14, 17]:
        tr = single_roi_combo(7, f"{KAMA_MOTOR} AND pv.wind_direction_number={wd}", "trifecta", "1-2-3", *DATES_TR)
        te = single_roi_combo(7, f"{KAMA_MOTOR} AND pv.wind_direction_number={wd}", "trifecta", "1-2-3", *DATES_TE)
        report(f"蒲郡 wd={wd} 1-2-3", *tr, *te)

    conn.close()


if __name__ == "__main__":
    main()
