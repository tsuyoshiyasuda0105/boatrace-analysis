"""桐生 K1 / K2 戦略 (3連単 5-1-2 / 4-5-2) の的中率を上げる追加条件を系統的に検証。

ベース条件 (K1):
  stadium=1 AND boat1 class=A1 AND motor>=35 AND 国1>=6 AND weather!=雨(3)

ベース条件 (K2 5-1-2):
  K1 ∧ (wd IS NULL OR wd!=6)

検証目的: 的中率 (hits / bets) を 2-3% 以上に上げる「追加条件」を 1 件以上発見。
ROI 改善も併記。重要: ROI が下がれば的中率を上げる意味なし。

split: 2026-01-01 (train: <2026-01-01, test: >=2026-01-01)

n inflation 注意: race_previews を JOIN する場合は必ず `AND pv.boat_number=1`。
"""
from __future__ import annotations

import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.verification.backtest import _conn  # noqa: E402

SPLIT = "2026-01-01"
PH = "%s" if os.environ.get("DATABASE_URL") else "?"

# K1 ベース条件 (1号艇 強化済)
BASE_K1 = (
    "EXISTS (SELECT 1 FROM race_entries e1 WHERE e1.race_id=r.race_id "
    "AND e1.boat_number=1 AND e1.class_number=1 "
    "AND e1.assigned_motor_top_2_percent>=35 "
    "AND e1.national_top_1_percent>=6) "
    "AND (pv.weather_number IS NULL OR pv.weather_number != 3)"
)

# K2 (wd!=6 限定 5-1-2)
BASE_K2 = (
    f"{BASE_K1} "
    "AND (pv.wind_direction_number IS NULL OR pv.wind_direction_number != 6)"
)


def trifecta_metrics(where_extra: str, combo: str, date_lo: str, date_hi: str):
    """指定 where + 3連単 combo の (n_bets, n_hits, total_pay, hit_rate%, roi%) を返す。

    必ず `pv.boat_number=1` を入れて n inflation を防ぐ。
    """
    where = (
        "r.stadium_number=1 AND pv.boat_number=1 "
        f"AND r.race_date >= {PH} AND r.race_date < {PH}"
    )
    if where_extra:
        where += f" AND {where_extra}"
    sql = f"""
SELECT COUNT(DISTINCT r.race_id) AS n,
       SUM(CASE WHEN rpay.payout IS NOT NULL THEN 1 ELSE 0 END) AS hits,
       COALESCE(SUM(rpay.payout), 0) AS pay
FROM races r
LEFT JOIN race_previews pv ON pv.race_id=r.race_id AND pv.boat_number=1
LEFT JOIN race_payouts rpay
  ON rpay.race_id=r.race_id AND rpay.bet_type='trifecta' AND rpay.combination='{combo}'
WHERE {where}"""
    cur.execute(sql, (date_lo, date_hi))
    n, h, p = cur.fetchone()
    n, h, p = int(n or 0), int(h or 0), int(p or 0)
    hit = round(100.0 * h / n, 2) if n else 0
    roi = round(100.0 * p / max(1, 100 * n), 2) if n else 0
    return n, h, p, hit, roi


def eval_condition(label: str, extra: str, combo: str, base: str, results: list):
    """train / test の的中率と ROI を出力。

    base はベース条件 (K1 or K2)。extra はその上に追加する条件。
    """
    full = base if not extra else f"{base} AND {extra}"
    tr = trifecta_metrics(full, combo, "0000-01-01", SPLIT)
    te = trifecta_metrics(full, combo, SPLIT, "9999-12-31")
    # icon: 的中率改善判定。test 的中率 >=2.5% かつ ROI >=120% を 🏆
    base_hit_te = None  # ベース比較は後で計算
    tr_hit, tr_roi = tr[3], tr[4]
    te_hit, te_roi = te[3], te[4]
    if te[0] >= 20 and te_hit >= 2.5 and tr_roi >= 100 and te_roi >= 120:
        icon = "🏆"
    elif te_hit >= 2.0 and te_roi >= 100:
        icon = "✓"
    elif te[0] < 10:
        icon = "·"  # 小サンプル
    else:
        icon = "-"
    print(
        f"  [{icon}] {label:<55} "
        f"tr {tr[1]:>3}/{tr[0]:>4}={tr_hit:>4.2f}% ROI={tr_roi:>5.1f}% | "
        f"te {te[1]:>2}/{te[0]:>3}={te_hit:>5.2f}% ROI={te_roi:>6.1f}%"
    )
    results.append({
        "label": label,
        "extra": extra,
        "combo": combo,
        "base": "K1" if base is BASE_K1 else "K2",
        "tr_n": tr[0], "tr_h": tr[1], "tr_pay": tr[2], "tr_hit": tr_hit, "tr_roi": tr_roi,
        "te_n": te[0], "te_h": te[1], "te_pay": te[2], "te_hit": te_hit, "te_roi": te_roi,
        "icon": icon,
    })


def section(title: str):
    print(f"\n--- {title} ---")


def main():
    global conn, cur
    conn = _conn()
    cur = conn.cursor()
    print(f"=== 桐生 K1/K2 的中率改善 探索 split={SPLIT} ===")
    print(f"K1 base = {BASE_K1}")
    print(f"K2 base = {BASE_K2}\n")

    all_results: list[dict] = []

    # =========================================================================
    # 0. ベースライン (再確認)
    # =========================================================================
    section("0. ベースライン")
    for combo, base, name in [
        ("5-1-2", BASE_K1, "K1 5-1-2"),
        ("4-5-2", BASE_K1, "K1 4-5-2"),
        ("5-1-2", BASE_K2, "K2 5-1-2 (wd!=6)"),
    ]:
        eval_condition(f"BASE {name}", "", combo, base, all_results)

    # =========================================================================
    # 1. 1号艇のさらなる強化
    # =========================================================================
    section("1-A. 1号艇 国1着率 強化 (K1 5-1-2)")
    for thr in [6.5, 7.0, 7.5, 8.0]:
        extra = (
            f"EXISTS (SELECT 1 FROM race_entries e1b WHERE e1b.race_id=r.race_id "
            f"AND e1b.boat_number=1 AND e1b.national_top_1_percent>={thr})"
        )
        eval_condition(f"K1 + boat1 国1>={thr}", extra, "5-1-2", BASE_K1, all_results)

    section("1-B. 1号艇 局1着率 強化 (K1 5-1-2)")
    for thr in [5.0, 6.0, 7.0, 8.0]:
        extra = (
            f"EXISTS (SELECT 1 FROM race_entries e1b WHERE e1b.race_id=r.race_id "
            f"AND e1b.boat_number=1 AND e1b.local_top_1_percent>={thr})"
        )
        eval_condition(f"K1 + boat1 局1>={thr}", extra, "5-1-2", BASE_K1, all_results)

    section("1-C. 1号艇 motor 2連率 強化 (K1 5-1-2)")
    for thr in [40, 45, 50]:
        extra = (
            f"EXISTS (SELECT 1 FROM race_entries e1b WHERE e1b.race_id=r.race_id "
            f"AND e1b.boat_number=1 AND e1b.assigned_motor_top_2_percent>={thr})"
        )
        eval_condition(f"K1 + boat1 motor>={thr}", extra, "5-1-2", BASE_K1, all_results)

    section("1-D. 1号艇 国1+motor 重ね合わせ (K1 5-1-2)")
    for n1, mt in [(7.0, 40), (7.0, 45), (7.5, 40), (8.0, 40)]:
        extra = (
            f"EXISTS (SELECT 1 FROM race_entries e1b WHERE e1b.race_id=r.race_id "
            f"AND e1b.boat_number=1 AND e1b.national_top_1_percent>={n1} "
            f"AND e1b.assigned_motor_top_2_percent>={mt})"
        )
        eval_condition(f"K1 + boat1 国1>={n1} motor>={mt}", extra, "5-1-2", BASE_K1, all_results)

    section("1-E. 1号艇 avg_ST 早い (K1 5-1-2)")
    for st in [0.17, 0.16, 0.15, 0.14]:
        extra = (
            f"EXISTS (SELECT 1 FROM race_entries e1b WHERE e1b.race_id=r.race_id "
            f"AND e1b.boat_number=1 AND e1b.avg_start_timing<={st})"
        )
        eval_condition(f"K1 + boat1 avg_ST<={st}", extra, "5-1-2", BASE_K1, all_results)

    section("1-F. 1号艇 年齢帯 (K1 5-1-2)")
    for lo, hi, lbl in [(20, 49, "20-49"), (30, 49, "30-49"), (50, 99, "50+"), (30, 45, "30-45")]:
        extra = (
            f"EXISTS (SELECT 1 FROM race_entries e1b WHERE e1b.race_id=r.race_id "
            f"AND e1b.boat_number=1 AND e1b.age BETWEEN {lo} AND {hi})"
        )
        eval_condition(f"K1 + boat1 age {lbl}", extra, "5-1-2", BASE_K1, all_results)

    # =========================================================================
    # 2. 他艇の条件 (5号艇 head, 4号艇 head, 2/3号艇 弱体化)
    # =========================================================================
    section("2-A. 5号艇 強化 (K1 5-1-2)")
    for n1 in [5.0, 6.0, 7.0]:
        extra = (
            f"EXISTS (SELECT 1 FROM race_entries e5 WHERE e5.race_id=r.race_id "
            f"AND e5.boat_number=5 AND e5.national_top_1_percent>={n1})"
        )
        eval_condition(f"K1 + boat5 国1>={n1}", extra, "5-1-2", BASE_K1, all_results)
    for mt in [35, 40, 45]:
        extra = (
            f"EXISTS (SELECT 1 FROM race_entries e5 WHERE e5.race_id=r.race_id "
            f"AND e5.boat_number=5 AND e5.assigned_motor_top_2_percent>={mt})"
        )
        eval_condition(f"K1 + boat5 motor>={mt}", extra, "5-1-2", BASE_K1, all_results)
    for cls in [1, 2]:
        extra = (
            f"EXISTS (SELECT 1 FROM race_entries e5 WHERE e5.race_id=r.race_id "
            f"AND e5.boat_number=5 AND e5.class_number={cls})"
        )
        eval_condition(f"K1 + boat5 class={cls}", extra, "5-1-2", BASE_K1, all_results)
    # 重ね
    extra = (
        "EXISTS (SELECT 1 FROM race_entries e5 WHERE e5.race_id=r.race_id "
        "AND e5.boat_number=5 AND e5.class_number=1 "
        "AND e5.national_top_1_percent>=5 AND e5.assigned_motor_top_2_percent>=35)"
    )
    eval_condition("K1 + boat5 A1+国1>=5+motor>=35", extra, "5-1-2", BASE_K1, all_results)

    section("2-B. 5号艇 強化 (K2 5-1-2)")
    for n1 in [5.0, 6.0, 7.0]:
        extra = (
            f"EXISTS (SELECT 1 FROM race_entries e5 WHERE e5.race_id=r.race_id "
            f"AND e5.boat_number=5 AND e5.national_top_1_percent>={n1})"
        )
        eval_condition(f"K2 + boat5 国1>={n1}", extra, "5-1-2", BASE_K2, all_results)
    for mt in [35, 40]:
        extra = (
            f"EXISTS (SELECT 1 FROM race_entries e5 WHERE e5.race_id=r.race_id "
            f"AND e5.boat_number=5 AND e5.assigned_motor_top_2_percent>={mt})"
        )
        eval_condition(f"K2 + boat5 motor>={mt}", extra, "5-1-2", BASE_K2, all_results)
    extra = (
        "EXISTS (SELECT 1 FROM race_entries e5 WHERE e5.race_id=r.race_id "
        "AND e5.boat_number=5 AND e5.class_number=1)"
    )
    eval_condition("K2 + boat5 class=A1", extra, "5-1-2", BASE_K2, all_results)

    section("2-C. 4号艇 強化 (K1 4-5-2)")
    for n1 in [5.0, 6.0, 7.0]:
        extra = (
            f"EXISTS (SELECT 1 FROM race_entries e4 WHERE e4.race_id=r.race_id "
            f"AND e4.boat_number=4 AND e4.national_top_1_percent>={n1})"
        )
        eval_condition(f"K1 + boat4 国1>={n1}", extra, "4-5-2", BASE_K1, all_results)
    for mt in [35, 40]:
        extra = (
            f"EXISTS (SELECT 1 FROM race_entries e4 WHERE e4.race_id=r.race_id "
            f"AND e4.boat_number=4 AND e4.assigned_motor_top_2_percent>={mt})"
        )
        eval_condition(f"K1 + boat4 motor>={mt}", extra, "4-5-2", BASE_K1, all_results)
    extra = (
        "EXISTS (SELECT 1 FROM race_entries e4 WHERE e4.race_id=r.race_id "
        "AND e4.boat_number=4 AND e4.class_number=1)"
    )
    eval_condition("K1 + boat4 class=A1", extra, "4-5-2", BASE_K1, all_results)
    extra = (
        "EXISTS (SELECT 1 FROM race_entries e4 WHERE e4.race_id=r.race_id "
        "AND e4.boat_number=4 AND e4.class_number=1 AND e4.national_top_1_percent>=5)"
    )
    eval_condition("K1 + boat4 A1+国1>=5", extra, "4-5-2", BASE_K1, all_results)

    section("2-D. 2号艇 弱体化 (K1 5-1-2)")
    # 2号艇が弱いほど 2着が 1 になりやすい?  否、3連単 5-1-2 の 2着は 1, 3着が 2 だから
    # 2号艇 弱体化は 3着が 2 になるのを邪魔する。3 や 4 に取られる可能性 → 2号艇 中等が良い?
    # まずは弱体化を試して反応を見る。
    for n1 in [4.0, 5.0, 6.0]:
        extra = (
            f"EXISTS (SELECT 1 FROM race_entries e2 WHERE e2.race_id=r.race_id "
            f"AND e2.boat_number=2 AND e2.national_top_1_percent<{n1})"
        )
        eval_condition(f"K1 + boat2 国1<{n1}", extra, "5-1-2", BASE_K1, all_results)
    # 2号艇 B級
    extra = (
        "EXISTS (SELECT 1 FROM race_entries e2 WHERE e2.race_id=r.race_id "
        "AND e2.boat_number=2 AND e2.class_number IN (3,4))"
    )
    eval_condition("K1 + boat2 class=B(3,4)", extra, "5-1-2", BASE_K1, all_results)
    # 2号艇 motor 弱
    for mt in [30, 35]:
        extra = (
            f"EXISTS (SELECT 1 FROM race_entries e2 WHERE e2.race_id=r.race_id "
            f"AND e2.boat_number=2 AND e2.assigned_motor_top_2_percent<{mt})"
        )
        eval_condition(f"K1 + boat2 motor<{mt}", extra, "5-1-2", BASE_K1, all_results)
    # 2号艇 強い (逆) — 2号艇が強いと 5-1-2 の 3着 2 が来やすい?
    for n1 in [5.0, 6.0]:
        extra = (
            f"EXISTS (SELECT 1 FROM race_entries e2 WHERE e2.race_id=r.race_id "
            f"AND e2.boat_number=2 AND e2.national_top_1_percent>={n1})"
        )
        eval_condition(f"K1 + boat2 国1>={n1}", extra, "5-1-2", BASE_K1, all_results)

    section("2-E. 3号艇 弱体化 (K1 5-1-2)")
    for n1 in [4.0, 5.0]:
        extra = (
            f"EXISTS (SELECT 1 FROM race_entries e3 WHERE e3.race_id=r.race_id "
            f"AND e3.boat_number=3 AND e3.national_top_1_percent<{n1})"
        )
        eval_condition(f"K1 + boat3 国1<{n1}", extra, "5-1-2", BASE_K1, all_results)
    extra = (
        "EXISTS (SELECT 1 FROM race_entries e3 WHERE e3.race_id=r.race_id "
        "AND e3.boat_number=3 AND e3.class_number IN (3,4))"
    )
    eval_condition("K1 + boat3 class=B(3,4)", extra, "5-1-2", BASE_K1, all_results)
    # 3号艇 motor 弱
    for mt in [30, 35]:
        extra = (
            f"EXISTS (SELECT 1 FROM race_entries e3 WHERE e3.race_id=r.race_id "
            f"AND e3.boat_number=3 AND e3.assigned_motor_top_2_percent<{mt})"
        )
        eval_condition(f"K1 + boat3 motor<{mt}", extra, "5-1-2", BASE_K1, all_results)

    # =========================================================================
    # 3. レース番号
    # =========================================================================
    section("3. レース番号 (K1 5-1-2)")
    for rs, lbl in [
        ("1,2,3,4,5,6", "1-6R"),
        ("7,8,9,10,11,12", "7-12R"),
        ("10,11,12", "10-12R"),
        ("12", "12R only"),
        ("1,2,3", "1-3R"),
        ("4,5,6", "4-6R"),
        ("8,9,10,11,12", "8-12R"),
    ]:
        eval_condition(f"K1 + race {lbl}", f"r.race_number IN ({rs})", "5-1-2", BASE_K1, all_results)
    # 4-5-2 でも
    section("3'. レース番号 (K1 4-5-2)")
    for rs, lbl in [
        ("7,8,9,10,11,12", "7-12R"),
        ("10,11,12", "10-12R"),
        ("1,2,3,4,5,6", "1-6R"),
    ]:
        eval_condition(f"K1 + race {lbl}", f"r.race_number IN ({rs})", "4-5-2", BASE_K1, all_results)

    # =========================================================================
    # 4. 月別 / 季節
    # =========================================================================
    section("4. 月別 (K1 5-1-2)  ※ test 期間は 2026-01〜05")
    # SQLite と Postgres の月抽出: SUBSTR(race_date, 6, 2) で portable
    for months, lbl in [
        ("'06','07','08'", "夏(6-8)"),
        ("'12','01','02'", "冬(12-2)"),
        ("'03','04','05'", "春(3-5)"),
        ("'09','10','11'", "秋(9-11)"),
        ("'01','02','03','04','05'", "1-5月"),
        ("'06','07','08','09','10','11','12'", "6-12月"),
    ]:
        extra = f"SUBSTR(r.race_date,6,2) IN ({months})"
        eval_condition(f"K1 + month {lbl}", extra, "5-1-2", BASE_K1, all_results)

    # =========================================================================
    # 5. 風速帯 (wind_speed)
    # =========================================================================
    section("5. 風速帯 (K1 5-1-2)")
    for lo, hi, lbl in [(0, 2, "ws 0-1"), (2, 4, "ws 2-3"), (4, 6, "ws 4-5"), (6, 99, "ws 6+")]:
        extra = f"pv.wind_speed>={lo} AND pv.wind_speed<{hi}"
        eval_condition(f"K1 + {lbl}", extra, "5-1-2", BASE_K1, all_results)
    # K2 でも
    section("5'. 風速帯 (K2 5-1-2)")
    for lo, hi, lbl in [(0, 2, "ws 0-1"), (2, 4, "ws 2-3"), (4, 6, "ws 4-5")]:
        extra = f"pv.wind_speed>={lo} AND pv.wind_speed<{hi}"
        eval_condition(f"K2 + {lbl}", extra, "5-1-2", BASE_K2, all_results)
    # 風向 wd!=6 でも風速で切る
    section("5''. K1 wd!=6 + 風速帯")
    for lo, hi, lbl in [(0, 2, "ws 0-1"), (2, 4, "ws 2-3"), (4, 99, "ws 4+")]:
        extra = (
            f"(pv.wind_direction_number IS NULL OR pv.wind_direction_number != 6) "
            f"AND pv.wind_speed>={lo} AND pv.wind_speed<{hi}"
        )
        eval_condition(f"K1 + wd!=6 + {lbl}", extra, "5-1-2", BASE_K1, all_results)

    # =========================================================================
    # 6. 展示 ST
    # =========================================================================
    section("6-A. 5号艇 展示 ST が早い (K1 5-1-2)")
    # boat5 の展示 STは preview の boat_number=5 行を見る必要あり
    # EXISTS ... pv5.boat_number=5
    for st in [0.15, 0.16, 0.17, 0.18, 0.20]:
        extra = (
            "EXISTS (SELECT 1 FROM race_previews pv5 WHERE pv5.race_id=r.race_id "
            f"AND pv5.boat_number=5 AND pv5.start_timing_exhibition<={st})"
        )
        eval_condition(f"K1 + boat5 展示ST<={st}", extra, "5-1-2", BASE_K1, all_results)

    section("6-B. 4号艇 展示 ST が早い (K1 4-5-2)")
    for st in [0.15, 0.17]:
        extra = (
            "EXISTS (SELECT 1 FROM race_previews pv4 WHERE pv4.race_id=r.race_id "
            f"AND pv4.boat_number=4 AND pv4.start_timing_exhibition<={st})"
        )
        eval_condition(f"K1 + boat4 展示ST<={st}", extra, "4-5-2", BASE_K1, all_results)

    section("6-C. 展示時間 (K1 5-1-2) boat5 速い艇")
    # 1号艇とboat5 の展示時間差
    for et in [6.70, 6.75, 6.80]:
        extra = (
            "EXISTS (SELECT 1 FROM race_previews pv5 WHERE pv5.race_id=r.race_id "
            f"AND pv5.boat_number=5 AND pv5.exhibition_time<={et})"
        )
        eval_condition(f"K1 + boat5 展示時間<={et}", extra, "5-1-2", BASE_K1, all_results)

    # =========================================================================
    # 7. tilt_adjustment
    # =========================================================================
    section("7-A. 5号艇 tilt 強気 (K1 5-1-2)")
    for tl in [0.5, 1.0, 1.5, 3.0]:
        extra = (
            "EXISTS (SELECT 1 FROM race_previews pv5 WHERE pv5.race_id=r.race_id "
            f"AND pv5.boat_number=5 AND pv5.tilt_adjustment>={tl})"
        )
        eval_condition(f"K1 + boat5 tilt>={tl}", extra, "5-1-2", BASE_K1, all_results)

    section("7-B. 4号艇 tilt 強気 (K1 4-5-2)")
    for tl in [0.5, 1.0, 3.0]:
        extra = (
            "EXISTS (SELECT 1 FROM race_previews pv4 WHERE pv4.race_id=r.race_id "
            f"AND pv4.boat_number=4 AND pv4.tilt_adjustment>={tl})"
        )
        eval_condition(f"K1 + boat4 tilt>={tl}", extra, "4-5-2", BASE_K1, all_results)

    # =========================================================================
    # 8. 組合せ (有望条件の重ね合わせ)
    # =========================================================================
    section("8. 有望条件の組合せ (K2 5-1-2 を中心に)")
    # 8-1. K2 + 5号艇 国1>=5 + 7-12R
    extra = (
        "EXISTS (SELECT 1 FROM race_entries e5 WHERE e5.race_id=r.race_id "
        "AND e5.boat_number=5 AND e5.national_top_1_percent>=5) "
        "AND r.race_number IN (7,8,9,10,11,12)"
    )
    eval_condition("K2 + boat5 国1>=5 + 7-12R", extra, "5-1-2", BASE_K2, all_results)
    # 8-2. K2 + 5号艇 国1>=5 + 1号艇 国1>=7
    extra = (
        "EXISTS (SELECT 1 FROM race_entries e5 WHERE e5.race_id=r.race_id "
        "AND e5.boat_number=5 AND e5.national_top_1_percent>=5) "
        "AND EXISTS (SELECT 1 FROM race_entries e1b WHERE e1b.race_id=r.race_id "
        "AND e1b.boat_number=1 AND e1b.national_top_1_percent>=7)"
    )
    eval_condition("K2 + boat5 国1>=5 + boat1 国1>=7", extra, "5-1-2", BASE_K2, all_results)
    # 8-3. K2 + 5号艇 motor>=35 + 1号艇 国1>=7
    extra = (
        "EXISTS (SELECT 1 FROM race_entries e5 WHERE e5.race_id=r.race_id "
        "AND e5.boat_number=5 AND e5.assigned_motor_top_2_percent>=35) "
        "AND EXISTS (SELECT 1 FROM race_entries e1b WHERE e1b.race_id=r.race_id "
        "AND e1b.boat_number=1 AND e1b.national_top_1_percent>=7)"
    )
    eval_condition("K2 + boat5 motor>=35 + boat1 国1>=7", extra, "5-1-2", BASE_K2, all_results)
    # 8-4. K2 + 5号艇 class=A1
    extra = (
        "EXISTS (SELECT 1 FROM race_entries e5 WHERE e5.race_id=r.race_id "
        "AND e5.boat_number=5 AND e5.class_number=1)"
    )
    eval_condition("K2 + boat5 class=A1", extra, "5-1-2", BASE_K2, all_results)
    # 8-5. K2 + 7-12R only
    eval_condition("K2 + 7-12R", "r.race_number IN (7,8,9,10,11,12)", "5-1-2", BASE_K2, all_results)
    # 8-6. K2 + 1号艇 国1>=7 + 7-12R
    extra = (
        "EXISTS (SELECT 1 FROM race_entries e1b WHERE e1b.race_id=r.race_id "
        "AND e1b.boat_number=1 AND e1b.national_top_1_percent>=7) "
        "AND r.race_number IN (7,8,9,10,11,12)"
    )
    eval_condition("K2 + boat1 国1>=7 + 7-12R", extra, "5-1-2", BASE_K2, all_results)
    # 8-7. K2 + 2号艇 弱 + 7-12R
    extra = (
        "EXISTS (SELECT 1 FROM race_entries e2 WHERE e2.race_id=r.race_id "
        "AND e2.boat_number=2 AND e2.national_top_1_percent<5) "
        "AND r.race_number IN (7,8,9,10,11,12)"
    )
    eval_condition("K2 + boat2 国1<5 + 7-12R", extra, "5-1-2", BASE_K2, all_results)
    # 8-8. K2 + 3号艇 弱
    extra = (
        "EXISTS (SELECT 1 FROM race_entries e3 WHERE e3.race_id=r.race_id "
        "AND e3.boat_number=3 AND e3.national_top_1_percent<5)"
    )
    eval_condition("K2 + boat3 国1<5", extra, "5-1-2", BASE_K2, all_results)
    # 8-9. K2 + 2/3号艇 両方弱
    extra = (
        "EXISTS (SELECT 1 FROM race_entries e2 WHERE e2.race_id=r.race_id "
        "AND e2.boat_number=2 AND e2.national_top_1_percent<5) "
        "AND EXISTS (SELECT 1 FROM race_entries e3 WHERE e3.race_id=r.race_id "
        "AND e3.boat_number=3 AND e3.national_top_1_percent<5)"
    )
    eval_condition("K2 + boat2&3 国1<5", extra, "5-1-2", BASE_K2, all_results)
    # 8-10. K2 + 5号艇 国1>=5 + 2号艇 国1<5
    extra = (
        "EXISTS (SELECT 1 FROM race_entries e5 WHERE e5.race_id=r.race_id "
        "AND e5.boat_number=5 AND e5.national_top_1_percent>=5) "
        "AND EXISTS (SELECT 1 FROM race_entries e2 WHERE e2.race_id=r.race_id "
        "AND e2.boat_number=2 AND e2.national_top_1_percent<5)"
    )
    eval_condition("K2 + boat5 国1>=5 + boat2 国1<5", extra, "5-1-2", BASE_K2, all_results)

    # =========================================================================
    # 9. K1 4-5-2 用組合せ
    # =========================================================================
    section("9. K1 4-5-2 組合せ")
    # 9-1. 4号艇 国1>=5 + 5号艇 国1>=5
    extra = (
        "EXISTS (SELECT 1 FROM race_entries e4 WHERE e4.race_id=r.race_id "
        "AND e4.boat_number=4 AND e4.national_top_1_percent>=5) "
        "AND EXISTS (SELECT 1 FROM race_entries e5 WHERE e5.race_id=r.race_id "
        "AND e5.boat_number=5 AND e5.national_top_1_percent>=5)"
    )
    eval_condition("K1 + boat4 国1>=5 + boat5 国1>=5", extra, "4-5-2", BASE_K1, all_results)
    # 9-2. 4号艇 motor>=35 + 7-12R
    extra = (
        "EXISTS (SELECT 1 FROM race_entries e4 WHERE e4.race_id=r.race_id "
        "AND e4.boat_number=4 AND e4.assigned_motor_top_2_percent>=35) "
        "AND r.race_number IN (7,8,9,10,11,12)"
    )
    eval_condition("K1 + boat4 motor>=35 + 7-12R", extra, "4-5-2", BASE_K1, all_results)
    # 9-3. 4号艇 国1>=5 + 7-12R
    extra = (
        "EXISTS (SELECT 1 FROM race_entries e4 WHERE e4.race_id=r.race_id "
        "AND e4.boat_number=4 AND e4.national_top_1_percent>=5) "
        "AND r.race_number IN (7,8,9,10,11,12)"
    )
    eval_condition("K1 + boat4 国1>=5 + 7-12R", extra, "4-5-2", BASE_K1, all_results)
    # 9-4. 風向追加 (wd=6 / wd!=6)
    eval_condition(
        "K1 4-5-2 + wd=6",
        "pv.wind_direction_number=6",
        "4-5-2", BASE_K1, all_results,
    )
    eval_condition(
        "K1 4-5-2 + wd!=6",
        "(pv.wind_direction_number IS NULL OR pv.wind_direction_number != 6)",
        "4-5-2", BASE_K1, all_results,
    )

    # =========================================================================
    # 10. 最強候補: K2 + 各種重ね
    # =========================================================================
    section("10. K2 5-1-2 系の最終調整候補")
    # 10-1. K2 + 1号艇 国1>=7 + 5号艇 国1>=5
    extra = (
        "EXISTS (SELECT 1 FROM race_entries e1b WHERE e1b.race_id=r.race_id "
        "AND e1b.boat_number=1 AND e1b.national_top_1_percent>=7) "
        "AND EXISTS (SELECT 1 FROM race_entries e5 WHERE e5.race_id=r.race_id "
        "AND e5.boat_number=5 AND e5.national_top_1_percent>=5)"
    )
    eval_condition("K2 + boat1 国1>=7 + boat5 国1>=5", extra, "5-1-2", BASE_K2, all_results)
    # 10-2. K2 + 1号艇 motor>=40
    extra = (
        "EXISTS (SELECT 1 FROM race_entries e1b WHERE e1b.race_id=r.race_id "
        "AND e1b.boat_number=1 AND e1b.assigned_motor_top_2_percent>=40)"
    )
    eval_condition("K2 + boat1 motor>=40", extra, "5-1-2", BASE_K2, all_results)
    # 10-3. K2 + 1号艇 motor>=40 + 5号艇 国1>=5
    extra = (
        "EXISTS (SELECT 1 FROM race_entries e1b WHERE e1b.race_id=r.race_id "
        "AND e1b.boat_number=1 AND e1b.assigned_motor_top_2_percent>=40) "
        "AND EXISTS (SELECT 1 FROM race_entries e5 WHERE e5.race_id=r.race_id "
        "AND e5.boat_number=5 AND e5.national_top_1_percent>=5)"
    )
    eval_condition("K2 + boat1 motor>=40 + boat5 国1>=5", extra, "5-1-2", BASE_K2, all_results)
    # 10-4. K2 + 1号艇 局1>=6
    extra = (
        "EXISTS (SELECT 1 FROM race_entries e1b WHERE e1b.race_id=r.race_id "
        "AND e1b.boat_number=1 AND e1b.local_top_1_percent>=6)"
    )
    eval_condition("K2 + boat1 局1>=6", extra, "5-1-2", BASE_K2, all_results)
    # 10-5. K2 + 1号艇 局1>=6 + 5号艇 国1>=5
    extra = (
        "EXISTS (SELECT 1 FROM race_entries e1b WHERE e1b.race_id=r.race_id "
        "AND e1b.boat_number=1 AND e1b.local_top_1_percent>=6) "
        "AND EXISTS (SELECT 1 FROM race_entries e5 WHERE e5.race_id=r.race_id "
        "AND e5.boat_number=5 AND e5.national_top_1_percent>=5)"
    )
    eval_condition("K2 + boat1 局1>=6 + boat5 国1>=5", extra, "5-1-2", BASE_K2, all_results)

    # =========================================================================
    # 11. 最終: K1 5-1-2 でも同様の上昇候補
    # =========================================================================
    section("11. K1 5-1-2 最終候補組合せ")
    # 11-1. K1 + boat5 国1>=5 + boat1 国1>=7
    extra = (
        "EXISTS (SELECT 1 FROM race_entries e5 WHERE e5.race_id=r.race_id "
        "AND e5.boat_number=5 AND e5.national_top_1_percent>=5) "
        "AND EXISTS (SELECT 1 FROM race_entries e1b WHERE e1b.race_id=r.race_id "
        "AND e1b.boat_number=1 AND e1b.national_top_1_percent>=7)"
    )
    eval_condition("K1 + boat5 国1>=5 + boat1 国1>=7", extra, "5-1-2", BASE_K1, all_results)
    # 11-2. K1 + boat5 国1>=5 + 7-12R
    extra = (
        "EXISTS (SELECT 1 FROM race_entries e5 WHERE e5.race_id=r.race_id "
        "AND e5.boat_number=5 AND e5.national_top_1_percent>=5) "
        "AND r.race_number IN (7,8,9,10,11,12)"
    )
    eval_condition("K1 + boat5 国1>=5 + 7-12R", extra, "5-1-2", BASE_K1, all_results)
    # 11-3. K1 + boat5 国1>=5 + boat2 国1<5
    extra = (
        "EXISTS (SELECT 1 FROM race_entries e5 WHERE e5.race_id=r.race_id "
        "AND e5.boat_number=5 AND e5.national_top_1_percent>=5) "
        "AND EXISTS (SELECT 1 FROM race_entries e2 WHERE e2.race_id=r.race_id "
        "AND e2.boat_number=2 AND e2.national_top_1_percent<5)"
    )
    eval_condition("K1 + boat5 国1>=5 + boat2 国1<5", extra, "5-1-2", BASE_K1, all_results)

    # =========================================================================
    # 結果まとめ
    # =========================================================================
    print("\n=== 上位候補ランキング (test 的中率順, te_n>=20) ===")
    qualified = [r for r in all_results if r["te_n"] >= 20]
    by_hit = sorted(qualified, key=lambda x: (-x["te_hit"], -x["te_roi"]))[:20]
    print(f"{'rank':<4} {'label':<55} {'te_hit':>7} {'te_roi':>8} {'tr_hit':>7} {'tr_roi':>8} {'te_n':>5}")
    for i, r in enumerate(by_hit, 1):
        print(
            f"{i:<4} {r['label']:<55} "
            f"{r['te_hit']:>6.2f}% {r['te_roi']:>7.1f}% "
            f"{r['tr_hit']:>6.2f}% {r['tr_roi']:>7.1f}% {r['te_n']:>5}"
        )

    print("\n=== 上位候補 (test ROI順, te_n>=20 かつ tr_roi>=120) ===")
    qualified2 = [r for r in qualified if r["tr_roi"] >= 120]
    by_roi = sorted(qualified2, key=lambda x: (-x["te_roi"], -x["te_hit"]))[:15]
    print(f"{'rank':<4} {'label':<55} {'te_roi':>8} {'te_hit':>7} {'tr_roi':>8} {'tr_hit':>7} {'te_n':>5}")
    for i, r in enumerate(by_roi, 1):
        print(
            f"{i:<4} {r['label']:<55} "
            f"{r['te_roi']:>7.1f}% {r['te_hit']:>6.2f}% "
            f"{r['tr_roi']:>7.1f}% {r['tr_hit']:>6.2f}% {r['te_n']:>5}"
        )

    # Trade-off summary: 的中率 vs ROI vs n
    print("\n=== 推奨条件まとめ (元の K1/K2 と比較) ===")
    bases = {
        "K1 5-1-2": next(r for r in all_results if r["label"] == "BASE K1 5-1-2"),
        "K1 4-5-2": next(r for r in all_results if r["label"] == "BASE K1 4-5-2"),
        "K2 5-1-2": next(r for r in all_results if r["label"] == "BASE K2 5-1-2 (wd!=6)"),
    }
    for k, b in bases.items():
        print(
            f"  [BASE] {k:<14} te {b['te_h']:>2}/{b['te_n']:>3}={b['te_hit']:>5.2f}% "
            f"ROI={b['te_roi']:>6.1f}% | tr {b['tr_h']:>2}/{b['tr_n']:>4}={b['tr_hit']:>5.2f}% "
            f"ROI={b['tr_roi']:>6.1f}%"
        )

    conn.close()
    print("\n=== 探索完了 ===")


if __name__ == "__main__":
    main()
