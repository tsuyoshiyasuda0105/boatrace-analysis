"""ラウンド13: チルト調整 (tilt_adjustment) 系 + Venus + 混合戦 仮説検証

ネット情報 (kyoteibiyori) からの仮説:
- チルト +1.0 以上の艇は 1着率が高い
- 女子戦 vs 混合戦で 1号艇 1着率が大きく異なる女性選手がいる
"""
import sys
import os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from src.verification.backtest import _conn

SPLIT = "2026-01-01"
PH = "%s" if os.environ.get("DATABASE_URL") else "?"


def win_roi_for_high_tilt(stadium_clause, tilt_min, date_lo, date_hi):
    """tilt>=tilt_min の艇を単勝で買った場合の ROI"""
    where = f"{stadium_clause} AND r.race_date >= {PH} AND r.race_date <= {PH}"
    sql = f"""
SELECT COUNT(*) AS bets,
       SUM(CASE WHEN rr.finishing_position=1 THEN 1 ELSE 0 END) AS hits,
       COALESCE(SUM(CASE WHEN rr.finishing_position=1 THEN rpay.payout ELSE 0 END), 0) AS pay
FROM race_previews pv
JOIN races r ON r.race_id=pv.race_id
JOIN race_results rr ON rr.race_id=pv.race_id AND rr.boat_number=pv.boat_number
LEFT JOIN race_payouts rpay
  ON rpay.race_id=pv.race_id AND rpay.bet_type='win'
 AND rpay.combination = CAST(pv.boat_number AS TEXT)
WHERE pv.tilt_adjustment >= {tilt_min} AND {where}"""
    cur.execute(sql, (date_lo, date_hi))
    b, h, p = cur.fetchone()
    p = int(p or 0)
    roi = round(100.0 * p / max(1, 100 * b), 2) if b else 0
    return b, h, roi


def win_roi_for_high_tilt_boat(stadium_clause, tilt_min, boat, date_lo, date_hi):
    """tilt>=tilt_min かつ 特定 boat の単勝 ROI"""
    where = f"{stadium_clause} AND r.race_date >= {PH} AND r.race_date <= {PH}"
    sql = f"""
SELECT COUNT(*) AS bets,
       SUM(CASE WHEN rr.finishing_position=1 THEN 1 ELSE 0 END) AS hits,
       COALESCE(SUM(CASE WHEN rr.finishing_position=1 THEN rpay.payout ELSE 0 END), 0) AS pay
FROM race_previews pv
JOIN races r ON r.race_id=pv.race_id
JOIN race_results rr ON rr.race_id=pv.race_id AND rr.boat_number=pv.boat_number
LEFT JOIN race_payouts rpay
  ON rpay.race_id=pv.race_id AND rpay.bet_type='win'
 AND rpay.combination = CAST(pv.boat_number AS TEXT)
WHERE pv.tilt_adjustment >= {tilt_min} AND pv.boat_number={boat} AND {where}"""
    cur.execute(sql, (date_lo, date_hi))
    b, h, p = cur.fetchone()
    p = int(p or 0)
    roi = round(100.0 * p / max(1, 100 * b), 2) if b else 0
    return b, h, roi


def report(label, tr, te, robust):
    tr_b, _, tr_r = tr
    te_b, _, te_r = te
    icon = "🏆" if (tr_r >= 120 and te_r >= 120 and tr_b >= 30 and te_b >= 30) else (
        "⚠" if (tr_r >= 100 and te_r >= 100) else "❌")
    print(f"  [{icon}] {label:<55} tr n={tr_b:>4} ROI={tr_r:>6.1f}% | te n={te_b:>4} ROI={te_r:>6.1f}%")
    if icon == "🏆":
        robust.append((label, tr_b, tr_r, te_b, te_r))


def main():
    global conn, cur
    conn = _conn()
    cur = conn.cursor()
    print(f"=== ラウンド13 split={SPLIT} (チルト + Venus 混合戦) ===\n")
    robust = []

    DATES_TR = ("0000-01-01", "2025-12-31")
    DATES_TE = ("2026-01-01", "9999-12-31")

    # --- 13-1. tilt>=X の艇 単勝 ROI (全会場 / 全コース) ---
    print("--- 13-1. 全会場 全コース tilt 別 単勝 ROI ---")
    for tilt in [0.5, 1.0, 1.5, 2.0, 3.0]:
        tr = win_roi_for_high_tilt("1=1", tilt, *DATES_TR)
        te = win_roi_for_high_tilt("1=1", tilt, *DATES_TE)
        report(f"tilt>={tilt} 全コース", tr, te, robust)

    # --- 13-2. tilt>=1.0 の各 boat 単勝 ---
    print("\n--- 13-2. tilt>=1.0 各 boat 単勝 ---")
    for boat in [1, 2, 3, 4, 5, 6]:
        tr = win_roi_for_high_tilt_boat("1=1", 1.0, boat, *DATES_TR)
        te = win_roi_for_high_tilt_boat("1=1", 1.0, boat, *DATES_TE)
        report(f"tilt>=1.0 boat={boat}", tr, te, robust)

    # --- 13-3. tilt>=3.0 各 boat 単勝 (極端な攻めセッティング) ---
    print("\n--- 13-3. tilt>=3.0 各 boat 単勝 ---")
    for boat in [1, 2, 3, 4, 5, 6]:
        tr = win_roi_for_high_tilt_boat("1=1", 3.0, boat, *DATES_TR)
        te = win_roi_for_high_tilt_boat("1=1", 3.0, boat, *DATES_TE)
        report(f"tilt>=3.0 boat={boat}", tr, te, robust)

    # --- 13-4. tilt>=1.0 × 桐生 各 boat ---
    print("\n--- 13-4. tilt>=1.0 × 桐生 各 boat 単勝 ---")
    for boat in [1, 4, 5]:
        tr = win_roi_for_high_tilt_boat("r.stadium_number=1", 1.0, boat, *DATES_TR)
        te = win_roi_for_high_tilt_boat("r.stadium_number=1", 1.0, boat, *DATES_TE)
        report(f"桐生 tilt>=1.0 boat={boat}", tr, te, robust)

    # --- 13-5. Venus (全女子戦) + 1号艇 motor35+国1≥6 → 単勝1 ---
    print("\n--- 13-5. Venus + 1号艇強化 単勝1 ---")
    venus_cond = """
EXISTS (SELECT 1 FROM (
  SELECT race_id, COUNT(*) AS nf FROM race_entries e2
  JOIN racers ra ON e2.racer_number=ra.racer_number
  WHERE ra.gender=2 GROUP BY race_id
) f WHERE f.race_id=r.race_id AND f.nf=6)
""".strip()
    motor_cond = "EXISTS (SELECT 1 FROM race_entries e1 WHERE e1.race_id=r.race_id AND e1.boat_number=1 AND e1.assigned_motor_top_2_percent>=35 AND e1.national_top_1_percent>=6)"
    sql_venus = f"""
SELECT COUNT(DISTINCT r.race_id) AS bets,
       COALESCE(SUM(CASE WHEN rr.finishing_position=1 THEN rpay.payout ELSE 0 END), 0) AS pay
FROM races r
JOIN race_results rr ON rr.race_id=r.race_id AND rr.boat_number=1
LEFT JOIN race_payouts rpay ON rpay.race_id=r.race_id AND rpay.bet_type='win' AND rpay.combination='1'
WHERE {venus_cond} AND {motor_cond} AND r.race_date >= {PH} AND r.race_date <= {PH}"""
    cur.execute(sql_venus, DATES_TR)
    b, p = cur.fetchone()
    p = int(p or 0)
    tr = (b, b, round(100.0 * p / max(1, 100 * b), 2) if b else 0)
    cur.execute(sql_venus, DATES_TE)
    b, p = cur.fetchone()
    p = int(p or 0)
    te = (b, b, round(100.0 * p / max(1, 100 * b), 2) if b else 0)
    report("Venus + 1号艇 motor35+国1≥6 単勝1", tr, te, robust)

    # --- 13-6. 混合戦 (女性1名以上 でも全員女性ではない) + 1号艇女性かどうかで切り分け ---
    print("\n--- 13-6. 混合戦 1号艇が女性 vs 男性 単勝1 ---")
    mixed_female_b1 = """
EXISTS (SELECT 1 FROM race_entries e1
  JOIN racers ra1 ON e1.racer_number=ra1.racer_number
  WHERE e1.race_id=r.race_id AND e1.boat_number=1 AND ra1.gender=2)
AND EXISTS (SELECT 1 FROM race_entries e2
  JOIN racers ra2 ON e2.racer_number=ra2.racer_number
  WHERE e2.race_id=r.race_id AND ra2.gender=1)
""".strip()
    mixed_male_b1 = """
EXISTS (SELECT 1 FROM race_entries e1
  JOIN racers ra1 ON e1.racer_number=ra1.racer_number
  WHERE e1.race_id=r.race_id AND e1.boat_number=1 AND ra1.gender=1)
AND EXISTS (SELECT 1 FROM race_entries e2
  JOIN racers ra2 ON e2.racer_number=ra2.racer_number
  WHERE e2.race_id=r.race_id AND ra2.gender=2)
""".strip()

    def gendered_win1(where_extra, date_lo, date_hi):
        sql = f"""
SELECT COUNT(DISTINCT r.race_id),
       COALESCE(SUM(CASE WHEN rr.finishing_position=1 THEN rpay.payout ELSE 0 END), 0)
FROM races r
JOIN race_results rr ON rr.race_id=r.race_id AND rr.boat_number=1
LEFT JOIN race_payouts rpay ON rpay.race_id=r.race_id AND rpay.bet_type='win' AND rpay.combination='1'
WHERE {where_extra} AND r.race_date >= {PH} AND r.race_date <= {PH}"""
        cur.execute(sql, (date_lo, date_hi))
        b, p = cur.fetchone()
        p = int(p or 0)
        return b, b, round(100.0 * p / max(1, 100 * b), 2) if b else 0

    tr = gendered_win1(mixed_female_b1, *DATES_TR)
    te = gendered_win1(mixed_female_b1, *DATES_TE)
    report("混合戦 1号艇=女性 単勝1", tr, te, robust)
    tr = gendered_win1(mixed_male_b1, *DATES_TR)
    te = gendered_win1(mixed_male_b1, *DATES_TE)
    report("混合戦 1号艇=男性 単勝1", tr, te, robust)

    # --- 13-7. 混合戦 1号艇女性 → 5-1-2 (1号艇崩れ→5頭まくり) ---
    print("\n--- 13-7. 混合戦 1号艇=女性 → 外艇 head 3連単 ---")
    def trif_with_cond(combo, where_extra, date_lo, date_hi):
        sql = f"""
SELECT COUNT(DISTINCT r.race_id),
       COALESCE(SUM(rpay.payout), 0)
FROM races r
LEFT JOIN race_payouts rpay ON rpay.race_id=r.race_id AND rpay.bet_type='trifecta' AND rpay.combination='{combo}'
WHERE {where_extra} AND r.race_date >= {PH} AND r.race_date <= {PH}"""
        cur.execute(sql, (date_lo, date_hi))
        b, p = cur.fetchone()
        p = int(p or 0)
        return b, b, round(100.0 * p / max(1, 100 * b), 2) if b else 0

    for combo in ["5-1-2", "4-5-2", "4-1-2", "3-1-2", "2-1-3", "2-3-1"]:
        tr = trif_with_cond(combo, mixed_female_b1, *DATES_TR)
        te = trif_with_cond(combo, mixed_female_b1, *DATES_TE)
        report(f"混合戦 1号艇=女性 {combo}", tr, te, robust)

    print(f"\n=== ラウンド13 robust: {len(robust)} ===")
    for l, tr_b, tr_r, te_b, te_r in sorted(robust, key=lambda x: -x[4]):
        print(f"  tr={tr_r:.1f}% (n={tr_b}) / te={te_r:.1f}% (n={te_b})  {l}")

    conn.close()


if __name__ == "__main__":
    main()
