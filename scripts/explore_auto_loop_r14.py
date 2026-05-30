"""ラウンド14: 桐生以外の新方向性 robust 戦略の再調査

これまで (Rounds 1-13) で発見された robust は全て桐生集中。今回は桐生に触れず、
未検証領域に踏み込む:

1. グレード別 (race_grade_number: 1=SG, 2=G1, 3=G2, 4=G3, 5=一般戦)
2. 選手出身地 (birthplace_number) ホームグラウンドアドバンテージ
3. 季節性 (race_date 月別) — 1号艇崩れ会場 (戸田/平和島/江戸川) を月別に
4. 別 bet type — quinella / exacta / wide
5. 桐生以外 × 風向 — wd 別に外艇/2号艇/3号艇単勝
6. 番外 — 当地連対率, フライング count, 年齢, 体重
7. 会場×3連単 mass scan (外艇 head + 高配当 long-shot)
   → 鳴門 (sta=14) A1 4-2-3/4-2-6/2-3-6 ensemble robust 発見

判定:
  🏆 : train/test 両方 ROI >= 130% かつ n >= 30 (両期間)
  ⚠ : 両方 ROI >= 100% (参考)
  ❌ : それ以外

複数比較問題に対するセーフガード:
- 上位候補は year-by-year split (2022/2023/2024/2025/2026) で安定性を再確認
- ensemble (複数 combo 合算) でばらつき抑制

race_previews を JOIN するときは AND pv.boat_number=1 で 1 row/race に
collapse することを必ず守る (これ無しだと n が 6倍 inflated)。
"""
import sys
import os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from src.verification.backtest import _conn

SPLIT = "2026-01-01"
PH = "%s" if os.environ.get("DATABASE_URL") else "?"

# 既知の robust 桐生条件 — 重複していないか後で確認する用 (今回 stadium=1 は除外)
KIRYU_STADIUM = 1

# stadium_number -> 都道府県コード (公式マスタ)
STADIUM_PREF = {
    1: 10,   # 桐生 = 群馬 (除外)
    2: 11,   # 戸田 = 埼玉
    3: 13,   # 江戸川 = 東京
    4: 13,   # 平和島 = 東京
    5: 13,   # 多摩川 = 東京
    6: 22,   # 浜名湖 = 静岡
    7: 23,   # 蒲郡 = 愛知
    8: 23,   # 常滑 = 愛知
    9: 24,   # 津 = 三重
    10: 18,  # 三国 = 福井
    11: 25,  # びわこ = 滋賀
    12: 27,  # 住之江 = 大阪
    13: 28,  # 尼崎 = 兵庫
    14: 36,  # 鳴門 = 徳島
    15: 37,  # 丸亀 = 香川
    16: 33,  # 児島 = 岡山
    17: 34,  # 宮島 = 広島
    18: 35,  # 徳山 = 山口
    19: 35,  # 下関 = 山口
    20: 40,  # 若松 = 福岡
    21: 40,  # 芦屋 = 福岡
    22: 40,  # 福岡 = 福岡
    23: 41,  # 唐津 = 佐賀
    24: 42,  # 大村 = 長崎
}

STADIUM_NAMES = {
    1: "桐生", 2: "戸田", 3: "江戸川", 4: "平和島", 5: "多摩川", 6: "浜名湖",
    7: "蒲郡", 8: "常滑", 9: "津", 10: "三国", 11: "びわこ", 12: "住之江",
    13: "尼崎", 14: "鳴門", 15: "丸亀", 16: "児島", 17: "宮島", 18: "徳山",
    19: "下関", 20: "若松", 21: "芦屋", 22: "福岡", 23: "唐津", 24: "大村",
}


def roi_metric(b, p):
    return round(100.0 * p / max(1, 100 * b), 2) if b else 0.0


def report(label, tr_b, tr_p, tr_r, te_b, te_p, te_r, robust, watch):
    """🏆: 両方 ROI>=130% かつ n>=30 (両期間)
       ⚠ : 両方 ROI>=100% (n>=20 で記録)
       ❌ : それ以外"""
    if tr_r >= 130 and te_r >= 130 and tr_b >= 30 and te_b >= 30:
        icon = "🏆"
        robust.append((label, tr_b, tr_r, te_b, te_r))
    elif tr_r >= 100 and te_r >= 100 and tr_b >= 20 and te_b >= 20:
        icon = "⚠"
        watch.append((label, tr_b, tr_r, te_b, te_r))
    else:
        icon = "❌"
    print(f"  [{icon}] {label:<60} tr n={tr_b:>5} ROI={tr_r:>6.1f}% | te n={te_b:>4} ROI={te_r:>6.1f}%")


# ============================================================
# 検証関数 (race_previews JOIN するときは pv.boat_number=1 必須)
# ============================================================

def trifecta_roi(where_extra, combo, date_lo, date_hi, use_preview=False):
    """3連単 ROI. where_extra は races/race_previews 参照可能 (use_preview=True で pv JOIN)."""
    base = f"r.race_date >= {PH} AND r.race_date <= {PH}"
    if where_extra:
        base += f" AND {where_extra}"
    pv_join = ""
    if use_preview:
        pv_join = "LEFT JOIN race_previews pv ON pv.race_id=r.race_id AND pv.boat_number=1"
    sql = f"""
SELECT COUNT(DISTINCT r.race_id), COALESCE(SUM(rpay.payout), 0)
FROM races r
{pv_join}
LEFT JOIN race_payouts rpay
  ON rpay.race_id=r.race_id AND rpay.bet_type='trifecta' AND rpay.combination='{combo}'
WHERE {base}"""
    cur.execute(sql, (date_lo, date_hi))
    b, p = cur.fetchone()
    p = int(p or 0)
    return b, p, roi_metric(b, p)


def quinella_roi(where_extra, combo_a, combo_b, date_lo, date_hi, use_preview=False):
    """2連複 ROI. combo は orderable: '1-2' (旧, ~2025-06) または '1=2' (新, 2025-07~).
       両方を OR で集計する."""
    base = f"r.race_date >= {PH} AND r.race_date <= {PH}"
    if where_extra:
        base += f" AND {where_extra}"
    pv_join = ""
    if use_preview:
        pv_join = "LEFT JOIN race_previews pv ON pv.race_id=r.race_id AND pv.boat_number=1"
    sql = f"""
SELECT COUNT(DISTINCT r.race_id), COALESCE(SUM(rpay.payout), 0)
FROM races r
{pv_join}
LEFT JOIN race_payouts rpay
  ON rpay.race_id=r.race_id AND rpay.bet_type='quinella'
 AND rpay.combination IN ('{combo_a}', '{combo_b}')
WHERE {base}"""
    cur.execute(sql, (date_lo, date_hi))
    b, p = cur.fetchone()
    p = int(p or 0)
    return b, p, roi_metric(b, p)


def exacta_roi(where_extra, combo, date_lo, date_hi, use_preview=False):
    """2連単 ROI."""
    base = f"r.race_date >= {PH} AND r.race_date <= {PH}"
    if where_extra:
        base += f" AND {where_extra}"
    pv_join = ""
    if use_preview:
        pv_join = "LEFT JOIN race_previews pv ON pv.race_id=r.race_id AND pv.boat_number=1"
    sql = f"""
SELECT COUNT(DISTINCT r.race_id), COALESCE(SUM(rpay.payout), 0)
FROM races r
{pv_join}
LEFT JOIN race_payouts rpay
  ON rpay.race_id=r.race_id AND rpay.bet_type='exacta' AND rpay.combination='{combo}'
WHERE {base}"""
    cur.execute(sql, (date_lo, date_hi))
    b, p = cur.fetchone()
    p = int(p or 0)
    return b, p, roi_metric(b, p)


def win_roi(where_extra, boat, date_lo, date_hi, use_preview=False):
    """単勝 ROI."""
    base = f"r.race_date >= {PH} AND r.race_date <= {PH}"
    if where_extra:
        base += f" AND {where_extra}"
    pv_join = ""
    if use_preview:
        pv_join = "LEFT JOIN race_previews pv ON pv.race_id=r.race_id AND pv.boat_number=1"
    sql = f"""
SELECT COUNT(DISTINCT r.race_id),
       COALESCE(SUM(CASE WHEN rr.finishing_position=1 THEN rpay.payout ELSE 0 END), 0)
FROM races r
{pv_join}
JOIN race_results rr ON rr.race_id=r.race_id AND rr.boat_number={boat}
LEFT JOIN race_payouts rpay
  ON rpay.race_id=r.race_id AND rpay.bet_type='win' AND rpay.combination='{boat}'
WHERE {base}"""
    cur.execute(sql, (date_lo, date_hi))
    b, p = cur.fetchone()
    p = int(p or 0)
    return b, p, roi_metric(b, p)


def ensemble_roi(where_extra, combos, date_lo, date_hi, use_preview=False):
    """複数 trifecta combo の合算 ROI. n は (combos 数) * (該当 race 数) になる (each combo = 1 bet)."""
    total_n = 0
    total_pay = 0
    for c in combos:
        b, p, _ = trifecta_roi(where_extra, c, date_lo, date_hi, use_preview)
        total_n += b
        total_pay += p
    return total_n, total_pay, roi_metric(total_n, total_pay)


# ============================================================
# Main
# ============================================================

def main():
    global conn, cur
    conn = _conn()
    cur = conn.cursor()
    print(f"=== ラウンド14 split={SPLIT} 桐生以外の新方向性 ===\n")
    robust = []
    watch = []

    DATES_TR = ("0000-01-01", "2025-12-31")
    DATES_TE = ("2026-01-01", "9999-12-31")

    # ============================================================
    # セクション 14-1. グレード別 仮説
    # ============================================================
    print("--- 14-1. グレード別 1号艇 1-2-3 全会場 ---")
    for grade, gname in [(1, "SG"), (2, "G1"), (3, "G2"), (4, "G3"), (5, "一般")]:
        cond = f"r.race_grade_number={grade}"
        tr = trifecta_roi(cond, "1-2-3", *DATES_TR)
        te = trifecta_roi(cond, "1-2-3", *DATES_TE)
        report(f"{gname} 全会場 1-2-3", *tr, *te, robust, watch)

    print("\n--- 14-2. グレード別 単勝1 全会場 ---")
    for grade, gname in [(1, "SG"), (2, "G1"), (3, "G2"), (4, "G3"), (5, "一般")]:
        cond = f"r.race_grade_number={grade}"
        tr = win_roi(cond, 1, *DATES_TR)
        te = win_roi(cond, 1, *DATES_TE)
        report(f"{gname} 単勝1", *tr, *te, robust, watch)

    print("\n--- 14-3. グレード別 外艇 head 単勝 (SG/G1 は荒れる説) ---")
    for grade, gname in [(1, "SG"), (2, "G1")]:
        cond = f"r.race_grade_number={grade}"
        for boat in [2, 3, 4, 5, 6]:
            tr = win_roi(cond, boat, *DATES_TR)
            te = win_roi(cond, boat, *DATES_TE)
            report(f"{gname} 単勝{boat}", *tr, *te, robust, watch)

    # ============================================================
    # 14-4. 出身地アドバンテージ — 1号艇選手の出身県が会場の県と一致
    # ============================================================
    print("\n--- 14-4. 1号艇選手 ホームグラウンド 単勝1 (全会場集約) ---")
    home_cond = """EXISTS (
      SELECT 1 FROM race_entries e1
      WHERE e1.race_id=r.race_id AND e1.boat_number=1
        AND e1.birthplace_number = (
          CASE r.stadium_number
            WHEN 1 THEN 10 WHEN 2 THEN 11 WHEN 3 THEN 13 WHEN 4 THEN 13
            WHEN 5 THEN 13 WHEN 6 THEN 22 WHEN 7 THEN 23 WHEN 8 THEN 23
            WHEN 9 THEN 24 WHEN 10 THEN 18 WHEN 11 THEN 25 WHEN 12 THEN 27
            WHEN 13 THEN 28 WHEN 14 THEN 36 WHEN 15 THEN 37 WHEN 16 THEN 33
            WHEN 17 THEN 34 WHEN 18 THEN 35 WHEN 19 THEN 35 WHEN 20 THEN 40
            WHEN 21 THEN 40 WHEN 22 THEN 40 WHEN 23 THEN 41 WHEN 24 THEN 42
          END
        )
    )"""
    tr = win_roi(home_cond, 1, *DATES_TR)
    te = win_roi(home_cond, 1, *DATES_TE)
    report("1号艇選手 地元 単勝1", *tr, *te, robust, watch)

    print("\n--- 14-5. 1号艇 地元 + A1 + 1-2-3 ---")
    home_a1_cond = home_cond + " AND EXISTS (SELECT 1 FROM race_entries e1 WHERE e1.race_id=r.race_id AND e1.boat_number=1 AND e1.class_number=1)"
    tr = trifecta_roi(home_a1_cond, "1-2-3", *DATES_TR)
    te = trifecta_roi(home_a1_cond, "1-2-3", *DATES_TE)
    report("1号艇 地元A1 1-2-3", *tr, *te, robust, watch)

    print("\n--- 14-6. 1号艇 地元A1 当地2連>=45 1-2-3 ---")
    home_local_cond = home_a1_cond + " AND EXISTS (SELECT 1 FROM race_entries e1 WHERE e1.race_id=r.race_id AND e1.boat_number=1 AND e1.local_top_2_percent>=45)"
    tr = trifecta_roi(home_local_cond, "1-2-3", *DATES_TR)
    te = trifecta_roi(home_local_cond, "1-2-3", *DATES_TE)
    report("1号艇 地元A1 当地2連>=45 1-2-3", *tr, *te, robust, watch)

    # ============================================================
    # 14-7. 戸田/平和島/大村 月別 単勝 外艇
    # ============================================================
    print("\n--- 14-7. 戸田 月別 単勝 外艇 (2,3,4) ---")
    for mo in ["01", "05"]:
        cond_mo = f"r.stadium_number=2 AND substr(r.race_date,6,2)='{mo}'"
        for boat in [2, 3, 4]:
            tr = win_roi(cond_mo, boat, *DATES_TR)
            te = win_roi(cond_mo, boat, *DATES_TE)
            report(f"戸田 月{mo} 単勝{boat}", *tr, *te, robust, watch)

    print("\n--- 14-8. 戸田 1号艇崩れ月 (春秋) 外艇 trif head ---")
    cond_spring = "r.stadium_number=2 AND substr(r.race_date,6,2) IN ('01','03','09')"
    for combo in ["3-1-2", "3-2-1", "4-1-2", "4-3-1", "2-3-4", "2-3-1"]:
        tr = trifecta_roi(cond_spring, combo, *DATES_TR)
        te = trifecta_roi(cond_spring, combo, *DATES_TE)
        report(f"戸田 春秋月 {combo}", *tr, *te, robust, watch)

    print("\n--- 14-9. 大村 (1号艇最強) 1月-2月 1-2-3 ---")
    cond_omu = "r.stadium_number=24 AND substr(r.race_date,6,2) IN ('01','02')"
    for combo in ["1-2-3", "1-2-4", "1-3-2", "1-3-4", "1-4-2", "1-4-3"]:
        tr = trifecta_roi(cond_omu, combo, *DATES_TR)
        te = trifecta_roi(cond_omu, combo, *DATES_TE)
        report(f"大村 1-2月 {combo}", *tr, *te, robust, watch)

    # ============================================================
    # 14-10~12. 別 bet type — 旧format `1-2` / 新format `1=2` 両方統合
    # ============================================================
    print("\n--- 14-10. 1号艇強会場 (12,18,24) で quinella 1-2 (両format) ---")
    for sta in [12, 18, 24]:
        cond = f"r.stadium_number={sta} AND EXISTS (SELECT 1 FROM race_entries e1 WHERE e1.race_id=r.race_id AND e1.boat_number=1 AND e1.class_number=1)"
        tr = quinella_roi(cond, "1-2", "1=2", *DATES_TR)
        te = quinella_roi(cond, "1-2", "1=2", *DATES_TE)
        report(f"{STADIUM_NAMES[sta]} A1 quinella 1-2", *tr, *te, robust, watch)

    print("\n--- 14-11. 1号艇強会場 exacta 1-2 ---")
    for sta in [12, 18, 24]:
        cond = f"r.stadium_number={sta} AND EXISTS (SELECT 1 FROM race_entries e1 WHERE e1.race_id=r.race_id AND e1.boat_number=1 AND e1.class_number=1)"
        tr = exacta_roi(cond, "1-2", *DATES_TR)
        te = exacta_roi(cond, "1-2", *DATES_TE)
        report(f"{STADIUM_NAMES[sta]} A1 exacta 1-2", *tr, *te, robust, watch)

    # ============================================================
    # 14-13. 他会場 × 風向 wd (wd は 2025-07 以降のみ)
    # ============================================================
    print("\n--- 14-13. 戸田 wd 別 単勝 (主要 wd) ---")
    for wd in [6, 17]:
        cond = f"r.stadium_number=2 AND pv.wind_direction_number={wd}"
        for boat in [1, 2, 3, 4]:
            tr = win_roi(cond, boat, *DATES_TR, use_preview=True)
            te = win_roi(cond, boat, *DATES_TE, use_preview=True)
            report(f"戸田 wd={wd} 単勝{boat}", *tr, *te, robust, watch)

    print("\n--- 14-14. 宮島 wd=13 単勝5 (mass scan で観察された候補) ---")
    cond = "r.stadium_number=17 AND pv.wind_direction_number=13"
    for boat in [1, 4, 5]:
        tr = win_roi(cond, boat, *DATES_TR, use_preview=True)
        te = win_roi(cond, boat, *DATES_TE, use_preview=True)
        report(f"宮島 wd=13 単勝{boat}", *tr, *te, robust, watch)

    # ============================================================
    # 14-15. 番外 — 1号艇 F count (フライング)
    # ============================================================
    print("\n--- 14-15. 1号艇 F count>=1 1号艇崩れ会場 外艇 単勝 ---")
    for sta in [2, 3, 4]:
        cond = f"r.stadium_number={sta} AND EXISTS (SELECT 1 FROM race_entries e1 WHERE e1.race_id=r.race_id AND e1.boat_number=1 AND e1.flying_count>=1)"
        for boat in [2, 3, 4]:
            tr = win_roi(cond, boat, *DATES_TR)
            te = win_roi(cond, boat, *DATES_TE)
            report(f"{STADIUM_NAMES[sta]} 1号F 単勝{boat}", *tr, *te, robust, watch)

    # ============================================================
    # 14-16. 当地連対率
    # ============================================================
    print("\n--- 14-16. 1号艇 当地2連>=55 1号艇崩れ会場 1-2-3 ---")
    for sta in [2, 3, 4]:
        cond = f"r.stadium_number={sta} AND EXISTS (SELECT 1 FROM race_entries e1 WHERE e1.race_id=r.race_id AND e1.boat_number=1 AND e1.local_top_2_percent>=55 AND e1.class_number IN (1,2))"
        tr = trifecta_roi(cond, "1-2-3", *DATES_TR)
        te = trifecta_roi(cond, "1-2-3", *DATES_TE)
        report(f"{STADIUM_NAMES[sta]} 1号当地2連>=55 1-2-3", *tr, *te, robust, watch)

    # ============================================================
    # 14-17. SG/G1 × 1号艇崩れ会場
    # ============================================================
    print("\n--- 14-17. SG/G1 × 戸田/平和島 外艇 head 単勝 ---")
    for grade, gname in [(2, "G1")]:
        for sta in [2, 4]:
            cond = f"r.stadium_number={sta} AND r.race_grade_number={grade}"
            for boat in [2, 3, 4, 5]:
                tr = win_roi(cond, boat, *DATES_TR)
                te = win_roi(cond, boat, *DATES_TE)
                report(f"{gname}×{STADIUM_NAMES[sta]} 単勝{boat}", *tr, *te, robust, watch)

    # ============================================================
    # 14-18. 桐生戦略 transfer test
    # ============================================================
    print("\n--- 14-18. 戸田/平和島/江戸川 motor35+国1≥6+雨除外 5-1-2 (桐生戦略 transfer) ---")
    for sta in [2, 3, 4]:
        cond = (f"r.stadium_number={sta} "
                "AND EXISTS (SELECT 1 FROM race_entries e1 WHERE e1.race_id=r.race_id AND e1.boat_number=1 "
                "    AND e1.class_number=1 AND e1.assigned_motor_top_2_percent>=35 AND e1.national_top_1_percent>=6) "
                "AND (pv.weather_number IS NULL OR pv.weather_number!=3)")
        for combo in ["5-1-2", "4-5-2", "4-1-2", "3-1-2"]:
            tr = trifecta_roi(cond, combo, *DATES_TR, use_preview=True)
            te = trifecta_roi(cond, combo, *DATES_TE, use_preview=True)
            report(f"{STADIUM_NAMES[sta]} enh {combo}", *tr, *te, robust, watch)

    # ============================================================
    # 14-19. 鳴門 (sta=14) 外艇 head 3連単 candidates (mass scan 由来)
    # ============================================================
    print("\n--- 14-19. 鳴門 A1 外艇 head 3連単 individual ---")
    sta = 14
    a1_naruto = f"r.stadium_number={sta} AND EXISTS (SELECT 1 FROM race_entries e1 WHERE e1.race_id=r.race_id AND e1.boat_number=1 AND e1.class_number=1)"
    for combo in ["2-3-6", "4-2-3", "4-2-6", "3-2-6", "2-4-3", "5-2-4", "4-3-2", "4-5-2"]:
        tr = trifecta_roi(a1_naruto, combo, *DATES_TR)
        te = trifecta_roi(a1_naruto, combo, *DATES_TE)
        report(f"鳴門 A1 {combo}", *tr, *te, robust, watch)

    # ============================================================
    # 14-20. 鳴門 A1 ensemble (本命候補)
    # ============================================================
    print("\n--- 14-20. 鳴門 A1 ensemble candidates (本命) ---")
    ensembles = [
        ("鳴門 A1 ensemble (4-2-3 + 4-2-6 + 2-3-6)", ["4-2-3", "4-2-6", "2-3-6"]),
        ("鳴門 A1 ensemble (4-2-3 + 2-3-6)", ["4-2-3", "2-3-6"]),
        ("鳴門 A1 ensemble (4-2-3 + 4-2-6)", ["4-2-3", "4-2-6"]),
    ]
    for label, combos in ensembles:
        tr = ensemble_roi(a1_naruto, combos, *DATES_TR)
        te = ensemble_roi(a1_naruto, combos, *DATES_TE)
        report(label, *tr, *te, robust, watch)

    # ============================================================
    # 14-21. 鳴門 A1 ensemble × motor35 (条件強化したらどうなるか)
    # ============================================================
    print("\n--- 14-21. 鳴門 A1+motor35 ensemble ---")
    a1m_naruto = a1_naruto + " AND EXISTS (SELECT 1 FROM race_entries e1 WHERE e1.race_id=r.race_id AND e1.boat_number=1 AND e1.assigned_motor_top_2_percent>=35)"
    for label, combos in ensembles:
        tr = ensemble_roi(a1m_naruto, combos, *DATES_TR)
        te = ensemble_roi(a1m_naruto, combos, *DATES_TE)
        report(label + " +motor35", *tr, *te, robust, watch)

    # ============================================================
    # 14-22. 安定性チェック — 鳴門 本命候補の年別 ROI
    # ============================================================
    print("\n--- 14-22. 鳴門 A1 ensemble (4-2-3 + 4-2-6 + 2-3-6) 年別 ---")
    combos = ["4-2-3", "4-2-6", "2-3-6"]
    for yr in ['2022', '2023', '2024', '2025', '2026']:
        cond_yr = a1_naruto
        total_n, total_p = 0, 0
        for c in combos:
            b, p, _ = trifecta_roi(cond_yr, c, f"{yr}-01-01", f"{yr}-12-31")
            total_n += b
            total_p += p
        roi = roi_metric(total_n, total_p)
        icon = "🏆" if roi >= 130 else "⚠" if roi >= 100 else "❌"
        print(f"  [{icon}] {yr}: n={total_n} pay={total_p} ROI={roi:.1f}%")

    # ============================================================
    # 14-23. 別会場の同戦略 (4-2-3 系外艇 head ensemble) チェック
    # ============================================================
    print("\n--- 14-23. 他会場 A1 4-2-3/2-3-6 ensemble (鳴門の比較対象) ---")
    for sta in [2, 3, 4, 5, 6, 9, 10, 13, 17]:
        cond_sta = f"r.stadium_number={sta} AND EXISTS (SELECT 1 FROM race_entries e1 WHERE e1.race_id=r.race_id AND e1.boat_number=1 AND e1.class_number=1)"
        tr = ensemble_roi(cond_sta, ["4-2-3", "4-2-6", "2-3-6"], *DATES_TR)
        te = ensemble_roi(cond_sta, ["4-2-3", "4-2-6", "2-3-6"], *DATES_TE)
        report(f"{STADIUM_NAMES[sta]} A1 ensemble", *tr, *te, robust, watch)

    # ============================================================
    # 結果集計
    # ============================================================
    print(f"\n=== ラウンド14 robust 🏆 : {len(robust)} 件 ===")
    for l, tr_b, tr_r, te_b, te_r in sorted(robust, key=lambda x: -x[4]):
        print(f"  tr={tr_r:.1f}% (n={tr_b}) / te={te_r:.1f}% (n={te_b})  {l}")

    print(f"\n=== ラウンド14 watch ⚠ (>=100% 両期間): {len(watch)} 件 ===")
    for l, tr_b, tr_r, te_b, te_r in sorted(watch, key=lambda x: -x[4]):
        print(f"  tr={tr_r:.1f}% (n={tr_b}) / te={te_r:.1f}% (n={te_b})  {l}")

    conn.close()


if __name__ == "__main__":
    main()
