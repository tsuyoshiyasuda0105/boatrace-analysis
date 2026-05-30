"""ラウンド15: 決まり手 (kimarite) を新 axis として加えた戦略総探索

Round 14 までで発見済の robust:
  - 桐生 K1/K2/K1_PRIME/K2_PRIME (5-1-2, 4-5-2 系)
  - L4 系 1号艇 universe (条件付き 1-2-3 / 1-2-* head)
  - 鳴門 A1 ensemble (4-2-3 + 4-2-6 + 2-3-6)

新規 axis: race_results.kimarite (1着艇のみ)
  値: 逃げ / まくり / 差し / まくり差し / 抜き / 恵まれ
  範囲: 2022-05-08 〜 2025-06-30 (174,031 件)
  ※ 2026-01-01 以降は kimarite NULL なので, test 期間は 2025-Q1 (Jan-Jun) を使う

判定基準:
  🏆 train/test 両方 ROI >= 130% かつ n >= 30
  ⚠  両方 ROI >= 100% (n >= 20)
  ❌ それ以外

複数比較: 上位は年別 (2022/23/24/25) で安定性チェック

注意:
  - kimarite はレース後にしか分からない → 「決まり手 X のとき」を bet 戦略には直接できない
  - しかし以下の使い方は可能:
    (1) 既存戦略の hit/miss 内訳を理解する (post-hoc)
    (2) 「決まり手 X が出やすい race」を pre-race 条件で抽出 → そこで X 起点の買い目
    (3) kimarite Y が多い venue × 適合買い目 (恒常傾向)
  - race_previews JOIN は pv.boat_number=1 必須 (n が 6倍 inflated になる)
"""
import sys
import os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from src.verification.backtest import _conn

PH = "%s" if os.environ.get("DATABASE_URL") else "?"

# kimarite の範囲は 2022-05-08 〜 2025-06-30 のみ
# train: 〜2024-12-31  test: 2025-01-01 〜 2025-06-30
KIM_TR = ("2022-05-08", "2024-12-31")
KIM_TE = ("2025-01-01", "2025-06-30")
# 全期間 (kimarite なしでも実行可能な戦略の比較用)
FULL_TR = ("0000-01-01", "2025-12-31")
FULL_TE = ("2026-01-01", "9999-12-31")

STADIUM_NAMES = {
    1: "桐生", 2: "戸田", 3: "江戸川", 4: "平和島", 5: "多摩川", 6: "浜名湖",
    7: "蒲郡", 8: "常滑", 9: "津", 10: "三国", 11: "びわこ", 12: "住之江",
    13: "尼崎", 14: "鳴門", 15: "丸亀", 16: "児島", 17: "宮島", 18: "徳山",
    19: "下関", 20: "若松", 21: "芦屋", 22: "福岡", 23: "唐津", 24: "大村",
}

KIMARITE_LIST = ["逃げ", "まくり", "差し", "まくり差し", "抜き", "恵まれ"]


def roi_metric(b, p):
    return round(100.0 * p / max(1, 100 * b), 2) if b else 0.0


def report(label, tr_b, tr_p, tr_r, te_b, te_p, te_r, robust, watch):
    """🏆: 両方 ROI>=130% かつ n>=30  /  ⚠: 両方 >=100% かつ n>=20"""
    if tr_r >= 130 and te_r >= 130 and tr_b >= 30 and te_b >= 30:
        icon = "🏆"
        robust.append((label, tr_b, tr_r, te_b, te_r))
    elif tr_r >= 100 and te_r >= 100 and tr_b >= 20 and te_b >= 20:
        icon = "⚠"
        watch.append((label, tr_b, tr_r, te_b, te_r))
    else:
        icon = "❌"
    print(f"  [{icon}] {label:<62} tr n={tr_b:>5} ROI={tr_r:>6.1f}% | te n={te_b:>4} ROI={te_r:>6.1f}%")


# ============================================================
# 共通クエリ関数
# ============================================================

def race_count(where_extra, date_lo, date_hi, use_preview=False):
    """期間内のレース数 (= bet 数)"""
    pv = "LEFT JOIN race_previews pv ON pv.race_id=r.race_id AND pv.boat_number=1" if use_preview else ""
    sql = f"""
SELECT COUNT(DISTINCT r.race_id)
FROM races r
{pv}
WHERE r.race_date >= {PH} AND r.race_date <= {PH}
  AND ({where_extra if where_extra else "1=1"})"""
    cur.execute(sql, (date_lo, date_hi))
    return cur.fetchone()[0]


def trifecta_roi(where_extra, combo, date_lo, date_hi, use_preview=False):
    base = f"r.race_date >= {PH} AND r.race_date <= {PH}"
    if where_extra:
        base += f" AND ({where_extra})"
    pv = "LEFT JOIN race_previews pv ON pv.race_id=r.race_id AND pv.boat_number=1" if use_preview else ""
    sql = f"""
SELECT COUNT(DISTINCT r.race_id), COALESCE(SUM(rpay.payout), 0)
FROM races r
{pv}
LEFT JOIN race_payouts rpay
  ON rpay.race_id=r.race_id AND rpay.bet_type='trifecta' AND rpay.combination='{combo}'
WHERE {base}"""
    cur.execute(sql, (date_lo, date_hi))
    b, p = cur.fetchone()
    return b, int(p or 0), roi_metric(b, int(p or 0))


def win_roi(where_extra, boat, date_lo, date_hi, use_preview=False):
    base = f"r.race_date >= {PH} AND r.race_date <= {PH}"
    if where_extra:
        base += f" AND ({where_extra})"
    pv = "LEFT JOIN race_previews pv ON pv.race_id=r.race_id AND pv.boat_number=1" if use_preview else ""
    sql = f"""
SELECT COUNT(DISTINCT r.race_id),
       COALESCE(SUM(CASE WHEN rr.finishing_position=1 THEN rpay.payout ELSE 0 END), 0)
FROM races r
{pv}
JOIN race_results rr ON rr.race_id=r.race_id AND rr.boat_number={boat}
LEFT JOIN race_payouts rpay
  ON rpay.race_id=r.race_id AND rpay.bet_type='win' AND rpay.combination='{boat}'
WHERE {base}"""
    cur.execute(sql, (date_lo, date_hi))
    b, p = cur.fetchone()
    return b, int(p or 0), roi_metric(b, int(p or 0))


def ensemble_roi(where_extra, combos, date_lo, date_hi, use_preview=False):
    total_n = 0
    total_pay = 0
    for c in combos:
        b, p, _ = trifecta_roi(where_extra, c, date_lo, date_hi, use_preview)
        total_n += b
        total_pay += p
    return total_n, total_pay, roi_metric(total_n, total_pay)


def kimarite_dist(where_extra, date_lo, date_hi, use_preview=False):
    """与えた条件で 1着艇の kimarite 分布を集計する"""
    base = f"r.race_date >= {PH} AND r.race_date <= {PH}"
    if where_extra:
        base += f" AND ({where_extra})"
    pv = "LEFT JOIN race_previews pv ON pv.race_id=r.race_id AND pv.boat_number=1" if use_preview else ""
    sql = f"""
SELECT rr.kimarite, COUNT(DISTINCT r.race_id)
FROM races r
{pv}
JOIN race_results rr ON rr.race_id=r.race_id AND rr.finishing_position=1
WHERE {base}
  AND rr.kimarite IS NOT NULL
GROUP BY rr.kimarite
ORDER BY 2 DESC"""
    cur.execute(sql, (date_lo, date_hi))
    return cur.fetchall()


def post_hoc_kim_for_combo(where_extra, combo, date_lo, date_hi):
    """戦略 (where_extra) で 3連単 combo が hit した race の kimarite 分布"""
    a, b, c = combo.split("-")
    base = f"r.race_date >= {PH} AND r.race_date <= {PH}"
    if where_extra:
        base += f" AND ({where_extra})"
    sql = f"""
SELECT rrk.kimarite, COUNT(DISTINCT r.race_id)
FROM races r
JOIN race_results rrk ON rrk.race_id=r.race_id AND rrk.boat_number={a} AND rrk.finishing_position=1
JOIN race_results rr2 ON rr2.race_id=r.race_id AND rr2.boat_number={b} AND rr2.finishing_position=2
JOIN race_results rr3 ON rr3.race_id=r.race_id AND rr3.boat_number={c} AND rr3.finishing_position=3
WHERE {base} AND rrk.kimarite IS NOT NULL
GROUP BY rrk.kimarite
ORDER BY 2 DESC"""
    cur.execute(sql, (date_lo, date_hi))
    return cur.fetchall()


# ============================================================
# Main
# ============================================================

def main():
    global conn, cur
    conn = _conn()
    cur = conn.cursor()
    print(f"=== Round 15: kimarite axis split tr={KIM_TR} te={KIM_TE} ===\n")
    robust = []
    watch = []
    hypotheses = 0  # try-count

    # ============================================================
    # SEC A. 既存戦略の決まり手分布 (post-hoc 理解)
    # ============================================================
    print("\n========== SEC A. 既存戦略の決まり手分布 ==========\n")

    print("--- A-1. 桐生 K1 風 (4-5-2 hit) の決まり手 ---")
    d = post_hoc_kim_for_combo("r.stadium_number=1", "4-5-2", *KIM_TR)
    total = sum(c for _, c in d)
    for k, c in d:
        pct = 100 * c / max(1, total)
        print(f"  桐生 4-5-2 hit kim={k:<8} n={c:>3} ({pct:.1f}%)")

    print("\n--- A-2. 桐生 5-1-2 hit の決まり手 ---")
    d = post_hoc_kim_for_combo("r.stadium_number=1", "5-1-2", *KIM_TR)
    total = sum(c for _, c in d)
    for k, c in d:
        pct = 100 * c / max(1, total)
        print(f"  桐生 5-1-2 hit kim={k:<8} n={c:>3} ({pct:.1f}%)")

    print("\n--- A-3. 鳴門 4-2-3 hit の決まり手 ---")
    d = post_hoc_kim_for_combo("r.stadium_number=14", "4-2-3", *KIM_TR)
    total = sum(c for _, c in d)
    for k, c in d:
        pct = 100 * c / max(1, total)
        print(f"  鳴門 4-2-3 hit kim={k:<8} n={c:>3} ({pct:.1f}%)")

    print("\n--- A-4. 鳴門 4-2-6 hit の決まり手 ---")
    d = post_hoc_kim_for_combo("r.stadium_number=14", "4-2-6", *KIM_TR)
    total = sum(c for _, c in d)
    for k, c in d:
        pct = 100 * c / max(1, total)
        print(f"  鳴門 4-2-6 hit kim={k:<8} n={c:>3} ({pct:.1f}%)")

    print("\n--- A-5. 鳴門 2-3-6 hit の決まり手 ---")
    d = post_hoc_kim_for_combo("r.stadium_number=14", "2-3-6", *KIM_TR)
    total = sum(c for _, c in d)
    for k, c in d:
        pct = 100 * c / max(1, total)
        print(f"  鳴門 2-3-6 hit kim={k:<8} n={c:>3} ({pct:.1f}%)")

    print("\n--- A-6. 全会場 1-2-3 hit の決まり手 (基準) ---")
    d = post_hoc_kim_for_combo(None, "1-2-3", *KIM_TR)
    total = sum(c for _, c in d)
    for k, c in d:
        pct = 100 * c / max(1, total)
        print(f"  全会場 1-2-3 hit kim={k:<8} n={c:>5} ({pct:.1f}%)")

    # ============================================================
    # SEC B. 会場別 決まり手出現率
    # ============================================================
    print("\n========== SEC B. 会場別 決まり手出現率 (全 race ベース) ==========\n")
    print(f"  {'会場':<6} {'逃げ':>6} {'まくり':>6} {'差し':>6} {'まく差':>6} {'抜き':>6} {'恵':>5} {'合計':>6}")
    venue_kim = {}  # stadium -> {kim: ratio}
    for sid in range(1, 25):
        d = kimarite_dist(f"r.stadium_number={sid}", *KIM_TR)
        total = sum(c for _, c in d)
        if total == 0:
            continue
        dist = {k: c for k, c in d}
        venue_kim[sid] = {k: dist.get(k, 0) / max(1, total) for k in KIMARITE_LIST}
        venue_kim[sid]["_total"] = total
        print(f"  {STADIUM_NAMES[sid]:<6}"
              f" {100 * dist.get('逃げ', 0) / total:>5.1f}%"
              f" {100 * dist.get('まくり', 0) / total:>5.1f}%"
              f" {100 * dist.get('差し', 0) / total:>5.1f}%"
              f" {100 * dist.get('まくり差し', 0) / total:>5.1f}%"
              f" {100 * dist.get('抜き', 0) / total:>5.1f}%"
              f" {100 * dist.get('恵まれ', 0) / total:>4.1f}%"
              f" {total:>6}")

    # 高 逃げ率 venue Top 5
    top_nige = sorted(venue_kim.items(), key=lambda x: -x[1]["逃げ"])[:5]
    print("\n  TOP5 逃げ率: " + ", ".join(f"{STADIUM_NAMES[s]}({100*v['逃げ']:.1f}%)" for s, v in top_nige))
    top_makuri = sorted(venue_kim.items(), key=lambda x: -x[1]["まくり"])[:5]
    print("  TOP5 まくり率: " + ", ".join(f"{STADIUM_NAMES[s]}({100*v['まくり']:.1f}%)" for s, v in top_makuri))
    top_sashi = sorted(venue_kim.items(), key=lambda x: -x[1]["差し"])[:5]
    print("  TOP5 差し率: " + ", ".join(f"{STADIUM_NAMES[s]}({100*v['差し']:.1f}%)" for s, v in top_sashi))

    # ============================================================
    # SEC C. 「逃げ率高い venue」 で 1-2-3 が高 ROI か
    # ============================================================
    print("\n========== SEC C. 高 逃げ率 venue で 1-2-3 ==========\n")
    nige_venues = [(s, v["逃げ"]) for s, v in venue_kim.items() if v["逃げ"] >= 0.50]
    nige_venues.sort(key=lambda x: -x[1])
    for sid, rate in nige_venues[:10]:
        for combo in ["1-2-3", "1-3-2", "1-2-4", "1-4-2"]:
            tr = trifecta_roi(f"r.stadium_number={sid}", combo, *KIM_TR)
            te = trifecta_roi(f"r.stadium_number={sid}", combo, *KIM_TE)
            label = f"{STADIUM_NAMES[sid]}(逃{100*rate:.0f}%) {combo}"
            report(label, *tr, *te, robust, watch)
            hypotheses += 1

    # ============================================================
    # SEC D. 高 まくり率 venue で外艇 head 3連単
    # ============================================================
    print("\n========== SEC D. 高 まくり率 venue で外艇 head ==========\n")
    makuri_venues = sorted(venue_kim.items(), key=lambda x: -x[1]["まくり"])[:6]
    for sid, vd in makuri_venues:
        rate = vd["まくり"]
        for combo in ["4-2-3", "4-2-6", "2-3-6", "5-1-2", "3-1-2", "2-1-3"]:
            tr = trifecta_roi(f"r.stadium_number={sid}", combo, *KIM_TR)
            te = trifecta_roi(f"r.stadium_number={sid}", combo, *KIM_TE)
            label = f"{STADIUM_NAMES[sid]}(まく{100*rate:.0f}%) {combo}"
            report(label, *tr, *te, robust, watch)
            hypotheses += 1

    # ============================================================
    # SEC E. 高 差し率 venue で 2-1-x ・ 差し系
    # ============================================================
    print("\n========== SEC E. 高 差し率 venue で 2-1-x 系 ==========\n")
    sashi_venues = sorted(venue_kim.items(), key=lambda x: -x[1]["差し"])[:6]
    for sid, vd in sashi_venues:
        rate = vd["差し"]
        for combo in ["2-1-3", "2-1-4", "2-3-1", "2-1-5", "3-1-2", "2-4-1"]:
            tr = trifecta_roi(f"r.stadium_number={sid}", combo, *KIM_TR)
            te = trifecta_roi(f"r.stadium_number={sid}", combo, *KIM_TE)
            label = f"{STADIUM_NAMES[sid]}(差{100*rate:.0f}%) {combo}"
            report(label, *tr, *te, robust, watch)
            hypotheses += 1

    # ============================================================
    # SEC F. 「決まり手 X が出るレース」を pre-race 条件で抽出する仮説
    # ============================================================
    print("\n========== SEC F. pre-race conditions × 決まり手 ==========\n")

    print("--- F-1. 1号艇 class=1 (A1) の決まり手分布 ---")
    cond_a1 = """EXISTS (
      SELECT 1 FROM race_entries e1
      WHERE e1.race_id=r.race_id AND e1.boat_number=1 AND e1.class_number=1
    )"""
    d = kimarite_dist(cond_a1, *KIM_TR)
    total = sum(c for _, c in d)
    for k, c in d:
        print(f"  1号艇A1 全会場 kim={k:<8} n={c:>5} ({100*c/total:.1f}%)")

    print("\n--- F-2. 1号艇 当地2連>=55 の決まり手分布 ---")
    cond_loc = """EXISTS (
      SELECT 1 FROM race_entries e1
      WHERE e1.race_id=r.race_id AND e1.boat_number=1 AND e1.local_top_2_percent>=55
    )"""
    d = kimarite_dist(cond_loc, *KIM_TR)
    total = sum(c for _, c in d)
    for k, c in d:
        print(f"  1号艇 当地>=55 kim={k:<8} n={c:>5} ({100*c/total:.1f}%)")

    print("\n--- F-3. 1号艇 A1 + 当地>=55 + 全国1>=6 (= L4-G++ F1 風) の決まり手分布 ---")
    cond_l4 = cond_a1 + " AND " + cond_loc + """ AND EXISTS (
      SELECT 1 FROM race_entries e1
      WHERE e1.race_id=r.race_id AND e1.boat_number=1 AND e1.national_top_1_percent>=6
    )"""
    d = kimarite_dist(cond_l4, *KIM_TR)
    total = sum(c for _, c in d)
    for k, c in d:
        print(f"  L4-G++F1 風 kim={k:<8} n={c:>5} ({100*c/total:.1f}%)")

    print("\n--- F-4. wind_speed (preview) >=5 / >=8 のとき決まり手分布 (全会場) ---")
    for wmin in [5, 8]:
        cond_w = f"pv.wind_speed >= {wmin}"
        d = kimarite_dist(cond_w, *KIM_TR, use_preview=True)
        total = sum(c for _, c in d)
        print(f"  wind_speed>={wmin}: total={total}")
        for k, c in d:
            print(f"    kim={k:<8} n={c:>5} ({100*c/total:.1f}%)")

    print("\n--- F-5. 戸田 wind_direction=6 (横風？) の決まり手分布 ---")
    for sid, sname in [(2, "戸田"), (4, "平和島"), (3, "江戸川")]:
        for wd in [6, 17]:
            cond = f"r.stadium_number={sid} AND pv.wind_direction_number={wd}"
            d = kimarite_dist(cond, *KIM_TR, use_preview=True)
            total = sum(c for _, c in d)
            if total == 0:
                continue
            print(f"  {sname} wd={wd}: total={total}")
            for k, c in d:
                print(f"    kim={k:<8} n={c:>4} ({100*c/total:.1f}%)")

    # ============================================================
    # SEC G. pre-race 「逃げ起きやすそう」conditions → 1-2-3 ROI
    # ============================================================
    print("\n========== SEC G. 逃げ予期 conditions × 1-2-3 / 1-2-* ==========\n")

    print("--- G-1. 1号艇 A1 + 当地>=55 + motor35+ で venue 別 1-2-3 ---")
    for sid in [12, 18, 24, 6, 8, 23]:  # 1号艇強会場
        cond = (f"r.stadium_number={sid} AND "
                f"EXISTS (SELECT 1 FROM race_entries e1 WHERE e1.race_id=r.race_id "
                f"AND e1.boat_number=1 AND e1.class_number=1 "
                f"AND e1.local_top_2_percent>=55 AND e1.assigned_motor_top_2_percent>=35)")
        tr = trifecta_roi(cond, "1-2-3", *KIM_TR)
        te = trifecta_roi(cond, "1-2-3", *KIM_TE)
        report(f"{STADIUM_NAMES[sid]} A1+地55+M35 1-2-3", *tr, *te, robust, watch)
        hypotheses += 1
        # 同条件で 1-3-2
        tr = trifecta_roi(cond, "1-3-2", *KIM_TR)
        te = trifecta_roi(cond, "1-3-2", *KIM_TE)
        report(f"{STADIUM_NAMES[sid]} A1+地55+M35 1-3-2", *tr, *te, robust, watch)
        hypotheses += 1

    print("\n--- G-2. 風弱 (preview wind_speed<=2) の 1号艇 A1 1-2-3 ---")
    for sid in [12, 24, 18, 6]:
        cond = (f"r.stadium_number={sid} AND pv.wind_speed<=2 AND "
                f"EXISTS (SELECT 1 FROM race_entries e1 WHERE e1.race_id=r.race_id "
                f"AND e1.boat_number=1 AND e1.class_number=1)")
        tr = trifecta_roi(cond, "1-2-3", *KIM_TR, use_preview=True)
        te = trifecta_roi(cond, "1-2-3", *KIM_TE, use_preview=True)
        report(f"{STADIUM_NAMES[sid]} 風弱 A1 1-2-3", *tr, *te, robust, watch)
        hypotheses += 1

    # ============================================================
    # SEC H. pre-race 「まくり / 差し起きやすそう」conditions → 外艇 head
    # ============================================================
    print("\n========== SEC H. まくり予期 → 外艇 head ==========\n")

    print("--- H-1. 1号艇 B級 (class>=3) のとき外艇 head 単勝 / 3連単 ---")
    cond_b1 = """EXISTS (
      SELECT 1 FROM race_entries e1
      WHERE e1.race_id=r.race_id AND e1.boat_number=1 AND e1.class_number>=3
    )"""
    for boat in [2, 3, 4, 5]:
        tr = win_roi(cond_b1, boat, *KIM_TR)
        te = win_roi(cond_b1, boat, *KIM_TE)
        report(f"1号B級 単勝{boat}", *tr, *te, robust, watch)
        hypotheses += 1

    print("\n--- H-2. 1号艇 B級 + 4号艇 A1 + 戸田/江戸川/平和島 外艇 head ---")
    cond_4a1 = """EXISTS (
      SELECT 1 FROM race_entries e4
      WHERE e4.race_id=r.race_id AND e4.boat_number=4 AND e4.class_number=1
    )"""
    for sid, sn in [(2, "戸田"), (3, "江戸川"), (4, "平和島")]:
        cond = f"r.stadium_number={sid} AND " + cond_b1 + " AND " + cond_4a1
        for boat in [4, 3, 2]:
            tr = win_roi(cond, boat, *KIM_TR)
            te = win_roi(cond, boat, *KIM_TE)
            report(f"{sn} 1号B+4号A1 単勝{boat}", *tr, *te, robust, watch)
            hypotheses += 1

    print("\n--- H-3. 4号艇 motor>=40% + 1号艇 B級 → 4頭 3連単 ---")
    cond_motor4 = """EXISTS (
      SELECT 1 FROM race_entries e4
      WHERE e4.race_id=r.race_id AND e4.boat_number=4 AND e4.assigned_motor_top_2_percent>=40
    )"""
    cond_h3 = cond_b1 + " AND " + cond_motor4
    for combo in ["4-1-2", "4-1-3", "4-2-3", "4-2-1", "4-3-1", "4-5-1"]:
        tr = trifecta_roi(cond_h3, combo, *KIM_TR)
        te = trifecta_roi(cond_h3, combo, *KIM_TE)
        report(f"1号B+4号M40 {combo}", *tr, *te, robust, watch)
        hypotheses += 1

    # ============================================================
    # SEC I. 3号艇 差し race の探索 (高 差し率 venue + 3号艇強)
    # ============================================================
    print("\n========== SEC I. 3号艇 差し race ==========\n")
    cond_3a1 = """EXISTS (
      SELECT 1 FROM race_entries e3
      WHERE e3.race_id=r.race_id AND e3.boat_number=3 AND e3.class_number=1
    )"""
    print("--- I-1. 高 差し率 venue + 3号艇 A1 → 3-1-2/3-2-1 ---")
    for sid in [10, 18, 15, 16, 24, 9]:
        cond = f"r.stadium_number={sid} AND " + cond_3a1
        for combo in ["3-1-2", "3-2-1", "3-1-4", "3-2-4", "3-4-1", "3-4-2"]:
            tr = trifecta_roi(cond, combo, *KIM_TR)
            te = trifecta_roi(cond, combo, *KIM_TE)
            report(f"{STADIUM_NAMES[sid]} 3号A1 {combo}", *tr, *te, robust, watch)
            hypotheses += 1

    # ============================================================
    # SEC J. 6号艇 まくり race (大穴)
    # ============================================================
    print("\n========== SEC J. 6号艇 まくり/単勝 ==========\n")
    cond_6a1 = """EXISTS (
      SELECT 1 FROM race_entries e6
      WHERE e6.race_id=r.race_id AND e6.boat_number=6 AND e6.class_number<=2
    )"""
    print("--- J-1. 6号艇 A級/B1 + 1号艇 B級 単勝6 ---")
    cond = cond_6a1 + " AND " + cond_b1
    tr = win_roi(cond, 6, *KIM_TR)
    te = win_roi(cond, 6, *KIM_TE)
    report("1号B+6号A/B1 単勝6", *tr, *te, robust, watch)
    hypotheses += 1
    for combo in ["6-1-2", "6-2-1", "6-3-1", "6-1-5", "6-5-1"]:
        tr = trifecta_roi(cond, combo, *KIM_TR)
        te = trifecta_roi(cond, combo, *KIM_TE)
        report(f"1号B+6号A/B1 {combo}", *tr, *te, robust, watch)
        hypotheses += 1

    # ============================================================
    # SEC K. 既存戦略の決まり手 hit 内訳と「全 hit ↔ 特定 kim hit」の差
    # ============================================================
    print("\n========== SEC K. 桐生 K1 戦略の決まり手別 hit 検証 ==========\n")

    print("--- K-1. 桐生 4-5-2 + class A1 motor35+ で 期間別 ROI ---")
    cond_k1 = ("r.stadium_number=1 AND "
               "EXISTS (SELECT 1 FROM race_entries e1 WHERE e1.race_id=r.race_id "
               "AND e1.boat_number=1 AND e1.class_number=1 "
               "AND e1.assigned_motor_top_2_percent>=35)")
    tr = trifecta_roi(cond_k1, "4-5-2", *KIM_TR)
    te = trifecta_roi(cond_k1, "4-5-2", *KIM_TE)
    report("桐生 K1風 4-5-2", *tr, *te, robust, watch)
    hypotheses += 1

    print("\n--- K-2. 桐生 5-1-2 + 同条件 ---")
    tr = trifecta_roi(cond_k1, "5-1-2", *KIM_TR)
    te = trifecta_roi(cond_k1, "5-1-2", *KIM_TE)
    report("桐生 K1風 5-1-2", *tr, *te, robust, watch)
    hypotheses += 1

    print("\n--- K-3. 桐生 K1 ensemble (4-5-2 + 5-1-2) ---")
    tr = ensemble_roi(cond_k1, ["4-5-2", "5-1-2"], *KIM_TR)
    te = ensemble_roi(cond_k1, ["4-5-2", "5-1-2"], *KIM_TE)
    report("桐生 K1風 ensemble", *tr, *te, robust, watch)
    hypotheses += 1

    # ============================================================
    # SEC L. 鳴門 4-2-3 + kimarite-correlated pre-race conditions
    # ============================================================
    print("\n========== SEC L. 鳴門 まくり起き race の事前抽出 ==========\n")

    print("--- L-1. 鳴門 1号艇 class>=3 の決まり手分布 ---")
    cond = f"r.stadium_number=14 AND " + cond_b1
    d = kimarite_dist(cond, *KIM_TR)
    total = sum(c for _, c in d)
    for k, c in d:
        print(f"  鳴門 1号B級 kim={k:<8} n={c:>4} ({100*c/total:.1f}%)")
    # その条件で 4-2-3 ROI
    tr = trifecta_roi(cond, "4-2-3", *KIM_TR)
    te = trifecta_roi(cond, "4-2-3", *KIM_TE)
    report("鳴門 1号B級 4-2-3", *tr, *te, robust, watch)
    hypotheses += 1
    tr = ensemble_roi(cond, ["4-2-3", "4-2-6", "2-3-6"], *KIM_TR)
    te = ensemble_roi(cond, ["4-2-3", "4-2-6", "2-3-6"], *KIM_TE)
    report("鳴門 1号B級 ensemble(4-2-3+4-2-6+2-3-6)", *tr, *te, robust, watch)
    hypotheses += 1

    print("\n--- L-2. 鳴門 1号艇 当地2連<=40 (1号弱) → 外艇 head ensemble ---")
    cond = ("r.stadium_number=14 AND EXISTS (SELECT 1 FROM race_entries e1 "
            "WHERE e1.race_id=r.race_id AND e1.boat_number=1 AND e1.local_top_2_percent<=40)")
    tr = ensemble_roi(cond, ["4-2-3", "4-2-6", "2-3-6"], *KIM_TR)
    te = ensemble_roi(cond, ["4-2-3", "4-2-6", "2-3-6"], *KIM_TE)
    report("鳴門 1号当地<=40 ensemble", *tr, *te, robust, watch)
    hypotheses += 1

    print("\n--- L-3. 鳴門 2号艇 A1 → 4-2-3 ensemble ---")
    cond_2a1 = ("r.stadium_number=14 AND EXISTS (SELECT 1 FROM race_entries e2 "
                "WHERE e2.race_id=r.race_id AND e2.boat_number=2 AND e2.class_number=1)")
    tr = ensemble_roi(cond_2a1, ["4-2-3", "4-2-6", "2-3-6"], *KIM_TR)
    te = ensemble_roi(cond_2a1, ["4-2-3", "4-2-6", "2-3-6"], *KIM_TE)
    report("鳴門 2号A1 ensemble", *tr, *te, robust, watch)
    hypotheses += 1

    # ============================================================
    # SEC M. 風 (preview) × kimarite × 1-2-3 / 外艇 head
    # ============================================================
    print("\n========== SEC M. 風 × 既存戦略再評価 ==========\n")

    print("--- M-1. 全会場 風弱 (ws<=1) で 1号艇 A1 1-2-3 ---")
    cond = "pv.wind_speed<=1 AND " + cond_a1
    tr = trifecta_roi(cond, "1-2-3", *KIM_TR, use_preview=True)
    te = trifecta_roi(cond, "1-2-3", *KIM_TE, use_preview=True)
    report("全会場 風弱<=1 A1 1-2-3", *tr, *te, robust, watch)
    hypotheses += 1

    print("\n--- M-2. 全会場 風弱<=1 A1 + 当地>=55 1-2-3 ---")
    cond = "pv.wind_speed<=1 AND " + cond_a1 + " AND " + cond_loc
    tr = trifecta_roi(cond, "1-2-3", *KIM_TR, use_preview=True)
    te = trifecta_roi(cond, "1-2-3", *KIM_TE, use_preview=True)
    report("風弱<=1 A1+地55 1-2-3", *tr, *te, robust, watch)
    hypotheses += 1

    print("\n--- M-3. 風強 (ws>=6) で 外艇 head 単勝 (全会場) ---")
    cond = "pv.wind_speed>=6"
    for boat in [2, 3, 4, 5]:
        tr = win_roi(cond, boat, *KIM_TR, use_preview=True)
        te = win_roi(cond, boat, *KIM_TE, use_preview=True)
        report(f"全会場 風強>=6 単勝{boat}", *tr, *te, robust, watch)
        hypotheses += 1

    # ============================================================
    # SEC N. 「決まり手 X の条件付き hit 率」 → expected 配当
    # ============================================================
    print("\n========== SEC N. 期待配当 (kimarite 別 / 全会場) ==========\n")

    print("--- N-1. 各 kimarite で 1着艇別の出現率 (1号艇 boat=1 hit と仮定) ---")
    for kim in KIMARITE_LIST:
        cur.execute(f"""SELECT rr.boat_number, COUNT(*)
FROM race_results rr
JOIN races r ON r.race_id=rr.race_id
WHERE rr.kimarite='{kim}' AND rr.finishing_position=1
  AND r.race_date >= {PH} AND r.race_date <= {PH}
GROUP BY rr.boat_number ORDER BY 1""", KIM_TR)
        cnts = dict(cur.fetchall())
        total = sum(cnts.values())
        print(f"  {kim}:", ", ".join(f"{b}号={cnts.get(b,0)}({100*cnts.get(b,0)/max(1,total):.1f}%)" for b in range(1, 7)),
              f" total={total}")

    # ============================================================
    # SEC O. L4 G++ F1 風 + 既存戦略の kimarite 分布
    # ============================================================
    print("\n========== SEC O. L4 universe + 風 → 1-2-3 期待値 ==========\n")
    # L4 G++ F1 風: 1号艇 A1 + 当地>=55 + 全国1>=6 + 1号艇国2>=55
    cond_l4_strong = """EXISTS (
      SELECT 1 FROM race_entries e1
      WHERE e1.race_id=r.race_id AND e1.boat_number=1 AND e1.class_number=1
        AND e1.local_top_2_percent>=55 AND e1.national_top_1_percent>=6
        AND e1.national_top_2_percent>=55
    )"""

    print("--- O-1. L4-strong 全会場 1-2-3 ---")
    tr = trifecta_roi(cond_l4_strong, "1-2-3", *KIM_TR)
    te = trifecta_roi(cond_l4_strong, "1-2-3", *KIM_TE)
    report("L4-strong 全会場 1-2-3", *tr, *te, robust, watch)
    hypotheses += 1

    print("\n--- O-2. L4-strong + 風弱<=1 で 1-2-3 ---")
    cond = cond_l4_strong + " AND pv.wind_speed<=1"
    tr = trifecta_roi(cond, "1-2-3", *KIM_TR, use_preview=True)
    te = trifecta_roi(cond, "1-2-3", *KIM_TE, use_preview=True)
    report("L4-strong 風弱<=1 1-2-3", *tr, *te, robust, watch)
    hypotheses += 1

    print("\n--- O-3. L4-strong 高 逃げ率 venue (12/18/24) 1-2-3 ---")
    for sid in [12, 18, 24, 8, 23]:
        cond = f"r.stadium_number={sid} AND " + cond_l4_strong
        tr = trifecta_roi(cond, "1-2-3", *KIM_TR)
        te = trifecta_roi(cond, "1-2-3", *KIM_TE)
        report(f"{STADIUM_NAMES[sid]} L4-strong 1-2-3", *tr, *te, robust, watch)
        hypotheses += 1

    # ============================================================
    # SEC P. 1号艇 ST 早 (avg_start_timing 早い) と 逃げ率
    # ============================================================
    print("\n========== SEC P. 1号艇 ST 早 ・ 逃げ → 1-2-3 ==========\n")

    print("--- P-1. 1号艇 avg_st <= 0.16 (とても早い) の決まり手分布 ---")
    cond = """EXISTS (
      SELECT 1 FROM race_entries e1
      WHERE e1.race_id=r.race_id AND e1.boat_number=1
        AND e1.avg_start_timing IS NOT NULL AND e1.avg_start_timing <= 0.16
    )"""
    d = kimarite_dist(cond, *KIM_TR)
    total = sum(c for _, c in d)
    for k, c in d:
        print(f"  1号 avgST<=0.16 kim={k:<8} n={c:>4} ({100*c/total:.1f}%)")
    tr = trifecta_roi(cond, "1-2-3", *KIM_TR)
    te = trifecta_roi(cond, "1-2-3", *KIM_TE)
    report("1号 avgST<=0.16 全会場 1-2-3", *tr, *te, robust, watch)
    hypotheses += 1

    print("\n--- P-2. 1号艇 avg_st<=0.15 + A1 1-2-3 (極限) ---")
    cond = ("EXISTS (SELECT 1 FROM race_entries e1 WHERE e1.race_id=r.race_id "
            "AND e1.boat_number=1 AND e1.class_number=1 "
            "AND e1.avg_start_timing IS NOT NULL AND e1.avg_start_timing<=0.15)")
    tr = trifecta_roi(cond, "1-2-3", *KIM_TR)
    te = trifecta_roi(cond, "1-2-3", *KIM_TE)
    report("1号 avgST<=0.15 A1 1-2-3", *tr, *te, robust, watch)
    hypotheses += 1

    # ============================================================
    # SEC Q. グランドフィナーレ: 年別安定性確認 robust 候補
    # ============================================================
    print("\n========== SEC Q. robust 候補 年別安定性確認 ==========\n")
    if robust:
        for label, *_ in robust[:8]:
            print(f"\n  ## {label} 年別:")
        # 鳴門 ensemble の年別 (基準として再記載)
        for sid_lbl, where in [
            ("鳴門 A1 ens", "r.stadium_number=14 AND " + cond_a1),
        ]:
            for yr in ["2022", "2023", "2024", "2025"]:
                lo = f"{yr}-01-01"
                hi = f"{yr}-12-31"
                n, p, _ = ensemble_roi(where, ["4-2-3", "4-2-6", "2-3-6"], lo, hi)
                r = roi_metric(n, p)
                print(f"    {sid_lbl} {yr}: n={n} pay={p} ROI={r:.1f}%")
    else:
        print("  (robust 候補なし — 全 ❌)")

    # ============================================================
    # 終了サマリ
    # ============================================================
    print(f"\n=== Round 15 robust 🏆 : {len(robust)} 件 (試行仮説 {hypotheses}) ===")
    for label, tr_b, tr_r, te_b, te_r in sorted(robust, key=lambda x: -x[4])[:20]:
        print(f"  tr={tr_r:.1f}% (n={tr_b}) / te={te_r:.1f}% (n={te_b})  {label}")

    print(f"\n=== Round 15 watch ⚠ : {len(watch)} 件 ===")
    for label, tr_b, tr_r, te_b, te_r in sorted(watch, key=lambda x: -x[4])[:15]:
        print(f"  tr={tr_r:.1f}% (n={tr_b}) / te={te_r:.1f}% (n={te_b})  {label}")


if __name__ == "__main__":
    main()
