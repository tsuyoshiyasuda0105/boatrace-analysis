"""ラウンド8b: race_previews 6行/race 問題を修正、別賭式 / 他会場 transfer 再検証"""
import sys
import os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from src.verification.backtest import _conn

SPLIT = "2026-01-01"
PH = "%s" if os.environ.get("DATABASE_URL") else "?"

# 各クエリで rp.boat_number=1 を入れて 1 row/race にする
PREVIEW_FILTER = "rp.boat_number=1"


def roi_win_boat(where, args, boat):
    where = where.replace("PH", PH)
    sql = f"""
SELECT COUNT(*) AS bets,
       SUM(CASE WHEN rr.finishing_position = 1 THEN 1 ELSE 0 END) AS hits,
       COALESCE(SUM(CASE WHEN rr.finishing_position = 1 THEN rpay.payout ELSE 0 END), 0) AS pay
FROM race_previews rp
JOIN races r ON r.race_id = rp.race_id
JOIN race_results rr ON rr.race_id = rp.race_id AND rr.boat_number = {boat}
LEFT JOIN race_payouts rpay
  ON rpay.race_id = rr.race_id
 AND rpay.bet_type = 'win'
 AND rpay.combination = '{boat}'
WHERE {PREVIEW_FILTER} AND {where}"""
    cur = conn.cursor()
    cur.execute(sql, args)
    b, h, p = cur.fetchone()
    roi = round(100.0 * p / max(1, 100 * b), 2) if b else 0
    return b, h, roi


def roi_exacta(where, args, head, second):
    where = where.replace("PH", PH)
    combo = f"{head}-{second}"
    sql = f"""
SELECT COUNT(*) AS bets,
       SUM(CASE WHEN rpay.payout IS NOT NULL THEN 1 ELSE 0 END) AS hits,
       COALESCE(SUM(rpay.payout), 0) AS pay
FROM race_previews rp
JOIN races r ON r.race_id = rp.race_id
LEFT JOIN race_payouts rpay
  ON rpay.race_id = rp.race_id
 AND rpay.bet_type = 'exacta'
 AND rpay.combination = '{combo}'
WHERE {PREVIEW_FILTER} AND {where}"""
    cur = conn.cursor()
    cur.execute(sql, args)
    b, h, p = cur.fetchone()
    roi = round(100.0 * p / max(1, 100 * b), 2) if b else 0
    return b, h, roi


def roi_quinella(where, args, combo):
    where = where.replace("PH", PH)
    sql = f"""
SELECT COUNT(*) AS bets,
       SUM(CASE WHEN rpay.payout IS NOT NULL THEN 1 ELSE 0 END) AS hits,
       COALESCE(SUM(rpay.payout), 0) AS pay
FROM race_previews rp
JOIN races r ON r.race_id = rp.race_id
LEFT JOIN race_payouts rpay
  ON rpay.race_id = rp.race_id
 AND rpay.bet_type = 'quinella'
 AND rpay.combination = '{combo}'
WHERE {PREVIEW_FILTER} AND {where}"""
    cur = conn.cursor()
    cur.execute(sql, args)
    b, h, p = cur.fetchone()
    roi = round(100.0 * p / max(1, 100 * b), 2) if b else 0
    return b, h, roi


def roi_trifecta(where, args, combo):
    where = where.replace("PH", PH)
    sql = f"""
SELECT COUNT(*) AS bets,
       SUM(CASE WHEN rpay.payout IS NOT NULL THEN 1 ELSE 0 END) AS hits,
       COALESCE(SUM(rpay.payout), 0) AS pay
FROM race_previews rp
JOIN races r ON r.race_id = rp.race_id
LEFT JOIN race_payouts rpay
  ON rpay.race_id = rp.race_id
 AND rpay.bet_type = 'trifecta'
 AND rpay.combination = '{combo}'
WHERE {PREVIEW_FILTER} AND {where}"""
    cur = conn.cursor()
    cur.execute(sql, args)
    b, h, p = cur.fetchone()
    roi = round(100.0 * p / max(1, 100 * b), 2) if b else 0
    return b, h, roi


def split_test(label, base_where, fn, *fn_args):
    b1, h1, r1 = fn(f"{base_where} AND r.race_date < PH", (SPLIT,), *fn_args)
    b2, h2, r2 = fn(f"{base_where} AND r.race_date >= PH", (SPLIT,), *fn_args)
    icon = "🏆" if (r1 >= 120 and r2 >= 120 and b1 >= 50 and b2 >= 50) else ("⚠" if (r1 >= 100 or r2 >= 100) else "❌")
    print(f"  [{icon}] {label:<48} tr n={b1:>4} ROI={r1:>6.1f}% | te n={b2:>4} ROI={r2:>6.1f}%")
    return (icon == "🏆", label, b1, r1, b2, r2)


def main():
    global conn
    conn = _conn()
    print(f"=== ラウンド8b split={SPLIT} (修正版 — preview rows collapsed) ===\n")

    robust = []

    # --- 確認: 単勝 4 in wd=6 (修正版で 1/6 になるはず) ---
    print("--- 確認: 桐生 wd=6 単勝 4 (修正版) ---")
    base = "r.stadium_number=1 AND rp.wind_direction_number=6"
    ok, l, *r = split_test("単勝 4", base, roi_win_boat, 4)
    if ok: robust.append((l, *r))

    # --- 8b-1. 桐生 wd=6 4号艇 別賭式 (正しい ROI で) ---
    print("\n--- 8b-1. 桐生 wd=6 4号艇 別賭式 ---")
    for second in [1, 2, 3, 5, 6]:
        ok, l, *r = split_test(f"2連単 4-{second}", base, roi_exacta, 4, second)
        if ok: robust.append((l, *r))
    for combo in ["1=4", "4=5", "2=4", "3=4", "4=6"]:
        ok, l, *r = split_test(f"quinella {combo}", base, roi_quinella, combo)
        if ok: robust.append((l, *r))
    for combo in ["4-1-2", "4-1-3", "4-1-5", "4-1-6", "4-2-1", "4-2-3", "4-2-5",
                   "4-3-1", "4-3-2", "4-5-1", "4-5-2", "4-5-3", "4-5-6", "4-6-1", "4-6-5"]:
        ok, l, *r = split_test(f"3連単 {combo}", base, roi_trifecta, combo)
        if ok: robust.append((l, *r))

    # --- 8b-2. 桐生 wd=6 + motor30 別賭式 ---
    print("\n--- 8b-2. 桐生 wd=6 + 4号艇 motor≥30 別賭式 ---")
    base = ("r.stadium_number=1 AND rp.wind_direction_number=6 AND EXISTS "
            "(SELECT 1 FROM race_entries re WHERE re.race_id=rp.race_id "
            "AND re.boat_number=4 AND re.assigned_motor_top_2_percent>=30)")
    ok, l, *r = split_test("単勝 4 (確認 wd=6 + motor30)", base, roi_win_boat, 4)
    if ok: robust.append((l, *r))
    for second in [1, 2, 3, 5, 6]:
        ok, l, *r = split_test(f"2連単 4-{second}", base, roi_exacta, 4, second)
        if ok: robust.append((l, *r))
    for combo in ["1=4", "4=5"]:
        ok, l, *r = split_test(f"quinella {combo}", base, roi_quinella, combo)
        if ok: robust.append((l, *r))
    for combo in ["4-1-2", "4-1-3", "4-2-1", "4-2-3", "4-5-1", "4-5-2", "4-5-6"]:
        ok, l, *r = split_test(f"3連単 {combo}", base, roi_trifecta, combo)
        if ok: robust.append((l, *r))

    # --- 8b-3. 他会場 transfer 4号艇 wd=6 単勝 ---
    print("\n--- 8b-3. 他会場 transfer 4号艇 wd=6 単勝 ---")
    for sta, lbl in [(2, "戸田"), (3, "江戸川"), (4, "平和島"), (5, "多摩川"),
                      (6, "浜名湖"), (7, "蒲郡"), (8, "常滑"), (9, "津"),
                      (10, "三国"), (11, "びわこ"), (12, "住之江"),
                      (17, "宮島"), (18, "徳山"), (21, "芦屋"), (22, "福岡"),
                      (23, "唐津"), (24, "大村")]:
        base = f"r.stadium_number={sta} AND rp.wind_direction_number=6"
        ok, l, *r = split_test(f"{lbl}(sta={sta}) wd=6 単勝 4", base, roi_win_boat, 4)
        if ok: robust.append((l, *r))

    # --- 8b-4. 桐生 wd=10 ws<2 4号艇 別賭式 ---
    print("\n--- 8b-4. 桐生 wd=10 ws<2 4号艇 別賭式 ---")
    base = "r.stadium_number=1 AND rp.wind_direction_number=10 AND rp.wind_speed<2"
    for second in [1, 2, 5, 6]:
        ok, l, *r = split_test(f"2連単 4-{second}", base, roi_exacta, 4, second)
        if ok: robust.append((l, *r))
    for combo in ["1=4", "4=5"]:
        ok, l, *r = split_test(f"quinella {combo}", base, roi_quinella, combo)
        if ok: robust.append((l, *r))
    for combo in ["4-1-2", "4-1-3", "4-2-1", "4-5-1", "4-5-2"]:
        ok, l, *r = split_test(f"3連単 {combo}", base, roi_trifecta, combo)
        if ok: robust.append((l, *r))

    # --- 8b-5. 5号艇 / 6号艇 単勝 wd=6 (外艇全般に風向効果?) ---
    print("\n--- 8b-5. 桐生 wd=6 他艇単勝 ---")
    base = "r.stadium_number=1 AND rp.wind_direction_number=6"
    for boat in [1, 2, 3, 5, 6]:
        ok, l, *r = split_test(f"単勝 {boat}", base, roi_win_boat, boat)
        if ok: robust.append((l, *r))

    print(f"\n=== ラウンド8b robust: {len(robust)} ===")
    for l, b1, r1, b2, r2 in sorted(robust, key=lambda x: -x[4]):
        print(f"  tr={r1:.1f}% (n={b1}) / te={r2:.1f}% (n={b2})  {l}")

    conn.close()


if __name__ == "__main__":
    main()
