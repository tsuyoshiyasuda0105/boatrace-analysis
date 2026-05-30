"""ラウンド12: ネット情報からの新仮説 — 戸田 外艇捲り / 大村 1-2-3 / 桐生 4頭まくり

データソース: kyoteibiyori.com の 2024 年集計から
- 桐生 (sta=1) 4号艇 捲り率 7.1% (全会場 2位)
- 戸田 (sta=2) 1号艇 1着率 43.9% (全会場 最低) + 3,4号艇 捲り率 8.5%/7.9%
- 大村 (sta=24) 1号艇 1着率 61.3% (全会場 最高)
"""
import sys
import os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from src.verification.backtest import _conn

SPLIT = "2026-01-01"
PH = "%s" if os.environ.get("DATABASE_URL") else "?"


def trifecta_roi(stadium, where_extra, combo, date_lo, date_hi):
    where = f"r.stadium_number={stadium} AND r.race_date >= {PH} AND r.race_date <= {PH}"
    if where_extra:
        where += f" AND {where_extra}"
    sql = f"""
SELECT COUNT(DISTINCT r.race_id), COALESCE(SUM(rpay.payout), 0)
FROM races r
LEFT JOIN race_payouts rpay
  ON rpay.race_id=r.race_id AND rpay.bet_type='trifecta' AND rpay.combination='{combo}'
WHERE {where}"""
    cur.execute(sql, (date_lo, date_hi))
    b, p = cur.fetchone()
    p = int(p or 0)
    roi = round(100.0 * p / max(1, 100 * b), 2) if b else 0
    return b, p, roi


def win_roi(stadium, where_extra, boat, date_lo, date_hi):
    where = f"r.stadium_number={stadium} AND r.race_date >= {PH} AND r.race_date <= {PH}"
    if where_extra:
        where += f" AND {where_extra}"
    sql = f"""
SELECT COUNT(DISTINCT r.race_id),
       COALESCE(SUM(CASE WHEN rr.finishing_position=1 THEN rpay.payout ELSE 0 END), 0)
FROM races r
JOIN race_results rr ON rr.race_id=r.race_id AND rr.boat_number={boat}
LEFT JOIN race_payouts rpay
  ON rpay.race_id=r.race_id AND rpay.bet_type='win' AND rpay.combination='{boat}'
WHERE {where}"""
    cur.execute(sql, (date_lo, date_hi))
    b, p = cur.fetchone()
    p = int(p or 0)
    roi = round(100.0 * p / max(1, 100 * b), 2) if b else 0
    return b, p, roi


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
    print(f"=== ラウンド12 split={SPLIT} (ネット仮説検証) ===\n")
    robust = []

    DATES_TR = ("0000-01-01", "2025-12-31")
    DATES_TE = ("2026-01-01", "9999-12-31")

    # --- 12-1. 戸田 外艇捲り (3頭/4頭 head) ---
    print("--- 12-1. 戸田 外艇 head 3連単 (捲り率高い) ---")
    base_toda = "EXISTS (SELECT 1 FROM race_entries e1 WHERE e1.race_id=r.race_id AND e1.boat_number=1 AND e1.class_number=1)"
    for combo in ["3-1-2", "3-1-4", "3-2-1", "3-2-4", "3-4-1", "3-4-2",
                   "4-1-2", "4-1-3", "4-2-1", "4-3-1", "4-3-2", "4-5-1", "4-5-2"]:
        tr = trifecta_roi(2, base_toda, combo, *DATES_TR)
        te = trifecta_roi(2, base_toda, combo, *DATES_TE)
        report(f"戸田 1号A1 {combo}", tr, te, robust)

    print("\n--- 12-2. 戸田 1号艇 崩れ条件 (B級1号艇) で外艇 ---")
    base = "EXISTS (SELECT 1 FROM race_entries e1 WHERE e1.race_id=r.race_id AND e1.boat_number=1 AND e1.class_number IN (3,4))"
    for combo in ["2-1-3", "3-1-2", "4-1-2", "5-1-2", "2-3-4", "3-2-1", "4-3-1"]:
        tr = trifecta_roi(2, base, combo, *DATES_TR)
        te = trifecta_roi(2, base, combo, *DATES_TE)
        report(f"戸田 1号B {combo}", tr, te, robust)

    # --- 12-3. 戸田 motor35+国1≥6 + 外艇 head ---
    print("\n--- 12-3. 戸田 motor35+国1≥6 + 外艇 head ---")
    base = "EXISTS (SELECT 1 FROM race_entries e1 WHERE e1.race_id=r.race_id AND e1.boat_number=1 AND e1.class_number=1 AND e1.assigned_motor_top_2_percent>=35 AND e1.national_top_1_percent>=6)"
    for combo in ["3-1-2", "4-1-2", "4-5-2", "3-1-4", "4-1-5"]:
        tr = trifecta_roi(2, base, combo, *DATES_TR)
        te = trifecta_roi(2, base, combo, *DATES_TE)
        report(f"戸田 enh {combo}", tr, te, robust)

    # --- 12-4. 大村 1-2-3 系 (1号艇強い会場) ---
    print("\n--- 12-4. 大村 1-2-3 系 ---")
    base_omu = "EXISTS (SELECT 1 FROM race_entries e1 WHERE e1.race_id=r.race_id AND e1.boat_number=1 AND e1.class_number=1)"
    for combo in ["1-2-3", "1-3-2", "1-2-4", "1-4-2", "1-3-4", "1-4-3"]:
        tr = trifecta_roi(24, base_omu, combo, *DATES_TR)
        te = trifecta_roi(24, base_omu, combo, *DATES_TE)
        report(f"大村 A1 {combo}", tr, te, robust)

    # --- 12-5. 大村 motor35+国1≥6 1-2-3 ---
    print("\n--- 12-5. 大村 motor35+国1≥6 1-2-3 ---")
    base = "EXISTS (SELECT 1 FROM race_entries e1 WHERE e1.race_id=r.race_id AND e1.boat_number=1 AND e1.class_number=1 AND e1.assigned_motor_top_2_percent>=35 AND e1.national_top_1_percent>=6)"
    for combo in ["1-2-3", "1-3-2", "1-2-4"]:
        tr = trifecta_roi(24, base, combo, *DATES_TR)
        te = trifecta_roi(24, base, combo, *DATES_TE)
        report(f"大村 enh {combo}", tr, te, robust)

    # --- 12-6. 桐生 4-1-x / 4-3-x 系 (4号艇 捲り率 7.1%) ---
    print("\n--- 12-6. 桐生 4頭 head (捲り率 7.1%) 全パターン ---")
    base_kr = "EXISTS (SELECT 1 FROM race_entries e1 WHERE e1.race_id=r.race_id AND e1.boat_number=1 AND e1.class_number=1)"
    for combo in ["4-1-2", "4-1-3", "4-1-5", "4-2-1", "4-2-3", "4-3-1", "4-3-2",
                   "4-5-1", "4-5-2", "4-5-3", "4-5-6"]:
        tr = trifecta_roi(1, base_kr, combo, *DATES_TR)
        te = trifecta_roi(1, base_kr, combo, *DATES_TE)
        report(f"桐生 A1 {combo}", tr, te, robust)

    # --- 12-7. 桐生 motor35+国1≥6+雨除外 で 4-1-x 系 (新発見の補強) ---
    print("\n--- 12-7. 桐生 motor35+国1≥6+雨除外 で 4頭 head ---")
    base = "EXISTS (SELECT 1 FROM race_entries e1 WHERE e1.race_id=r.race_id AND e1.boat_number=1 AND e1.class_number=1 AND e1.assigned_motor_top_2_percent>=35 AND e1.national_top_1_percent>=6) AND EXISTS (SELECT 1 FROM race_previews pv WHERE pv.race_id=r.race_id AND pv.boat_number=1 AND (pv.weather_number IS NULL OR pv.weather_number != 3))"
    for combo in ["4-1-2", "4-1-3", "4-1-5", "4-2-1", "4-3-1", "4-5-1", "4-5-2"]:
        tr = trifecta_roi(1, base, combo, *DATES_TR)
        te = trifecta_roi(1, base, combo, *DATES_TE)
        report(f"桐生 enh {combo}", tr, te, robust)

    print(f"\n=== ラウンド12 robust: {len(robust)} ===")
    for l, tr_b, tr_r, te_b, te_r in sorted(robust, key=lambda x: -x[4]):
        print(f"  tr={tr_r:.1f}% (n={tr_b}) / te={te_r:.1f}% (n={te_b})  {l}")

    conn.close()


if __name__ == "__main__":
    main()
