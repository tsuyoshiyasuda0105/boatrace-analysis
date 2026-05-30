"""L4 universe で 1 号艇が「抜き」で勝った race の事前特徴を探索する分析スクリプト.

母集団 (L4 universe):
  - 期間: 2022-05-08 〜 2025-06-30
  - 1号艇 class = A1 (= 1)
  - stadium_number NOT IN B除外 8会場
  - 雨除外 (weather_number != 3 / NULL は許可)
  - 男性のみ (gender=2 が居る race は除外)

目的:
  決まり手 = '抜き' (= 1号艇が一旦譲って捲り返した) hit (n≈732) と
  「逃げ」(本流 hit) を分けて事前特徴量の分布差を見る。

仮説:
  1. 1号艇 motor 弱め (=立ち遅れる)
  2. 2号艇 / 3号艇 強い (= 1コースを抜き易い)
  3. 1号艇 国1/局1 強い (= 終盤で取り返す力)
  4. ベテラン (30+) × ST 早い (一旦譲っても加速で抜く)
  5. 桐生 / 浜名湖 のような流れの速い水面で起きやすい?

出力:
  - 各特徴の「抜き hit 群 vs 全 L4 群 (or 逃げ群)」の分布比較
  - AND 組合せフィルター候補 → 1-2-3 hit 率 / ROI を実測
  - 時系列スプリット (train < 2025-01-01 / test >= 2025-01-01) で robust 判定

実行:
    py -3 scripts/analyze_nuki_features.py
"""
from __future__ import annotations

import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

import config  # noqa: E402

# B 除外会場 (l4_strategy.EXCLUDE_VENUES と一致)
EXCLUDE_VENUES = (2, 4, 7, 8, 10, 19, 21, 24)
DATE_FROM = "2022-05-08"
DATE_TO = "2025-06-30"
TRAIN_SPLIT = "2025-01-01"  # train < this < test


def pull_l4_universe() -> list[dict]:
    """L4 universe の全 race + 主要特徴量 + 結果を pull する."""
    conn = sqlite3.connect(config.DB_PATH)
    cur = conn.cursor()
    q = f"""
        SELECT
            r.race_id,
            r.race_date,
            r.stadium_number,
            r.race_grade_number,
            r.race_number,
            -- boat 1
            e1.assigned_motor_top_2_percent AS m1_top2,
            e1.assigned_motor_top_3_percent AS m1_top3,
            e1.national_top_1_percent AS n1_t1,
            e1.national_top_2_percent AS n1_t2,
            e1.local_top_1_percent AS l1_t1,
            e1.local_top_2_percent AS l1_t2,
            e1.avg_start_timing AS st1,
            e1.age AS age1,
            -- boat 2
            e2.assigned_motor_top_2_percent AS m2_top2,
            e2.national_top_1_percent AS n2_t1,
            e2.national_top_2_percent AS n2_t2,
            e2.local_top_1_percent AS l2_t1,
            e2.avg_start_timing AS st2,
            e2.class_number AS cl2,
            -- boat 3
            e3.assigned_motor_top_2_percent AS m3_top2,
            e3.national_top_1_percent AS n3_t1,
            e3.national_top_2_percent AS n3_t2,
            e3.local_top_1_percent AS l3_t1,
            e3.avg_start_timing AS st3,
            e3.class_number AS cl3,
            -- result
            rr1.kimarite AS kim,
            rr1.finishing_position AS pos1,
            -- 1-2-3 payout (NULL = 不 hit)
            pp.payout AS p_123
        FROM races r
        JOIN race_entries e1 ON e1.race_id=r.race_id AND e1.boat_number=1
        JOIN race_entries e2 ON e2.race_id=r.race_id AND e2.boat_number=2
        JOIN race_entries e3 ON e3.race_id=r.race_id AND e3.boat_number=3
        LEFT JOIN race_previews pv ON pv.race_id=r.race_id AND pv.boat_number=1
        LEFT JOIN race_results rr1 ON rr1.race_id=r.race_id AND rr1.boat_number=1 AND rr1.finishing_position=1
        LEFT JOIN race_payouts pp ON pp.race_id=r.race_id AND pp.bet_type='trifecta' AND pp.combination='1-2-3'
        WHERE r.race_date BETWEEN ? AND ?
          AND e1.class_number = 1
          AND r.stadium_number NOT IN ({",".join("?" * len(EXCLUDE_VENUES))})
          AND (pv.weather_number IS NULL OR pv.weather_number != 3)
          AND NOT EXISTS (
            SELECT 1 FROM race_entries ex
            JOIN racers ra ON ex.racer_number=ra.racer_number
            WHERE ex.race_id=r.race_id AND ra.gender=2)
    """
    args = [DATE_FROM, DATE_TO, *EXCLUDE_VENUES]
    cur.execute(q, args)
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    conn.close()
    return rows


def _safe_float(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _safe_int(v):
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def summarize_feature_dist(rows: list[dict], key: str, label: str) -> None:
    """特定の特徴量 key について 抜き群 vs 逃げ群 vs 全L4 の平均/中央値を比較表示."""
    nuki = [_safe_float(r[key]) for r in rows if r["kim"] == "抜き"]
    nige = [_safe_float(r[key]) for r in rows if r["kim"] == "逃げ"]
    allv = [_safe_float(r[key]) for r in rows]
    nuki = [v for v in nuki if v is not None]
    nige = [v for v in nige if v is not None]
    allv = [v for v in allv if v is not None]
    if not nuki or not nige:
        print(f"  {label:<28} (insufficient data)")
        return
    nuki_avg = sum(nuki) / len(nuki)
    nige_avg = sum(nige) / len(nige)
    all_avg = sum(allv) / len(allv) if allv else 0
    diff = nuki_avg - nige_avg
    print(
        f"  {label:<28}  抜き avg={nuki_avg:>6.2f}  逃げ avg={nige_avg:>6.2f}  "
        f"全L4 avg={all_avg:>6.2f}  Δ(抜-逃)={diff:>+6.2f}"
    )


def hit_rate_in_bin(rows: list[dict], pred) -> tuple[int, int, int, float, float]:
    """pred(row) を満たす race の (n_races, n_hits_123, sum_payout, hit_rate%, roi%) を返す."""
    sel = [r for r in rows if pred(r)]
    n = len(sel)
    hits = [r for r in sel if r["p_123"] is not None]
    n_hits = len(hits)
    sum_pay = sum(int(r["p_123"]) for r in hits)
    hit_rate = (n_hits / n * 100) if n else 0
    roi = (sum_pay / (100 * n) * 100) if n else 0
    return n, n_hits, sum_pay, hit_rate, roi


def show_band(label, n, n_hits, sum_pay, hit_rate, roi):
    print(f"  {label:<60}  n={n:>5}  hits={n_hits:>4}  hit%={hit_rate:>5.2f}  ROI={roi:>6.1f}%")


def main():
    print("=" * 100)
    print("L4 universe 抜き hit 事前特徴 探索分析")
    print(f"期間: {DATE_FROM} 〜 {DATE_TO}  (train/test split: {TRAIN_SPLIT})")
    print("=" * 100)

    rows = pull_l4_universe()
    print(f"\nTotal L4 universe candidate races: {len(rows):,}")

    # 種別カウント
    counts = defaultdict(int)
    for r in rows:
        if r["kim"] is None:
            counts["NULL (1号艇着外 or 未取得)"] += 1
        else:
            counts[r["kim"]] += 1
    print("\nKimarite 分布 (L4 universe 内):")
    for k, n in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {k:<25} {n:>6}")

    # 1-2-3 hit 状況の baseline
    n_all, n_hits_all, sum_all, hit_all, roi_all = hit_rate_in_bin(rows, lambda r: True)
    print(f"\nL4 universe 全体 (boat1 1st 含む全体):")
    print(f"  n={n_all:,}  hits={n_hits_all}  hit%={hit_all:.2f}  ROI={roi_all:.1f}%")

    nuki_only_n, nuki_only_h, nuki_only_p, nuki_only_hit, nuki_only_roi = hit_rate_in_bin(
        rows, lambda r: r["kim"] == "抜き"
    )
    print(f"\n  抜き hit (= 事後識別):  n={nuki_only_n}  hits={nuki_only_h}  "
          f"hit%={nuki_only_hit:.2f}  ROI={nuki_only_roi:.1f}%")
    nige_n, nige_h, nige_p, nige_hit, nige_roi = hit_rate_in_bin(
        rows, lambda r: r["kim"] == "逃げ"
    )
    print(f"  逃げ hit (= 事後識別):  n={nige_n}  hits={nige_h}  "
          f"hit%={nige_hit:.2f}  ROI={nige_roi:.1f}%")

    # ============================================================
    # 1. 特徴量の分布比較 (抜き vs 逃げ vs 全L4)
    # ============================================================
    print("\n" + "=" * 100)
    print("[1] 特徴量分布比較 (抜き hit vs 逃げ hit vs 全 L4 universe)")
    print("=" * 100)
    feats = [
        ("m1_top2", "1艇 motor 2連率"),
        ("m1_top3", "1艇 motor 3連率"),
        ("n1_t1", "1艇 国1着率"),
        ("n1_t2", "1艇 国2連率"),
        ("l1_t1", "1艇 局1着率"),
        ("l1_t2", "1艇 局2連率"),
        ("st1", "1艇 avg ST"),
        ("age1", "1艇 年齢"),
        ("m2_top2", "2艇 motor 2連率"),
        ("n2_t1", "2艇 国1着率"),
        ("n2_t2", "2艇 国2連率"),
        ("l2_t1", "2艇 局1着率"),
        ("st2", "2艇 avg ST"),
        ("m3_top2", "3艇 motor 2連率"),
        ("n3_t1", "3艇 国1着率"),
        ("n3_t2", "3艇 国2連率"),
        ("st3", "3艇 avg ST"),
    ]
    for key, label in feats:
        summarize_feature_dist(rows, key, label)

    # ============================================================
    # 2. 仮説別フィルター候補で 1-2-3 hit 率/ROI 測定
    #    (フィルターは「事前情報のみ」で判定可能なもの = motor / 国局 / 年齢 / 会場)
    # ============================================================
    print("\n" + "=" * 100)
    print("[2] 単一条件で 1-2-3 hit 率/ROI 変化を測定 (= 抜き hit の母集団的 lift)")
    print("=" * 100)

    bands = [
        # 1艇 motor 弱め (= 立ち遅れる) → 抜き発生しやすい?
        ("1艇 motor 2連率 <= 30",
         lambda r: r["m1_top2"] is not None and r["m1_top2"] <= 30.0),
        ("1艇 motor 2連率 <= 33",
         lambda r: r["m1_top2"] is not None and r["m1_top2"] <= 33.0),
        ("1艇 motor 2連率 <= 35",
         lambda r: r["m1_top2"] is not None and r["m1_top2"] <= 35.0),
        ("1艇 motor 2連率 >= 40",
         lambda r: r["m1_top2"] is not None and r["m1_top2"] >= 40.0),
        # 1艇 国1着率 強い (= 終盤で取り返す)
        ("1艇 国1着率 >= 7",
         lambda r: r["n1_t1"] is not None and r["n1_t1"] >= 7.0),
        ("1艇 国1着率 >= 7.5",
         lambda r: r["n1_t1"] is not None and r["n1_t1"] >= 7.5),
        ("1艇 国1着率 >= 8",
         lambda r: r["n1_t1"] is not None and r["n1_t1"] >= 8.0),
        # 2艇 強い
        ("2艇 国2連率 >= 45",
         lambda r: r["n2_t2"] is not None and r["n2_t2"] >= 45.0),
        ("2艇 国2連率 >= 50",
         lambda r: r["n2_t2"] is not None and r["n2_t2"] >= 50.0),
        ("2艇 motor 2連率 >= 40",
         lambda r: r["m2_top2"] is not None and r["m2_top2"] >= 40.0),
        # 3艇 強い (= 3コースから 2着抜けてくる)
        ("3艇 国1着率 >= 6",
         lambda r: r["n3_t1"] is not None and r["n3_t1"] >= 6.0),
        ("3艇 国2連率 >= 40",
         lambda r: r["n3_t2"] is not None and r["n3_t2"] >= 40.0),
        # ベテラン × ST
        ("1艇 age >= 35 × ST <= 0.16",
         lambda r: (r["age1"] is not None and r["age1"] >= 35)
                   and (r["st1"] is not None and r["st1"] <= 0.16)),
        ("1艇 age >= 40 × ST <= 0.16",
         lambda r: (r["age1"] is not None and r["age1"] >= 40)
                   and (r["st1"] is not None and r["st1"] <= 0.16)),
        # 会場 (流れの速い水面?)
        ("桐生のみ", lambda r: r["stadium_number"] == 1),
        ("浜名湖のみ", lambda r: r["stadium_number"] == 6),
        ("江戸川のみ", lambda r: r["stadium_number"] == 3),
        ("尼崎のみ", lambda r: r["stadium_number"] == 13),
    ]

    for label, pred in bands:
        n, h, sp, hr, roi = hit_rate_in_bin(rows, pred)
        show_band(label, n, h, sp, hr, roi)

    # ============================================================
    # 3. 抜き 集中度 lift 分析
    #    各条件で「(抜き hit 数 / そのバンドの race 数) ÷ ベース抜き率」を計算
    #    1.5x+ の lift がある条件を抜きフィルターの候補にする
    # ============================================================
    print("\n" + "=" * 100)
    print("[3] 抜き発生率 lift 分析 (条件下の抜き発生率 / L4 universe ベース)")
    print("=" * 100)
    n_all = len(rows)
    n_nuki_all = sum(1 for r in rows if r["kim"] == "抜き")
    base_nuki_rate = n_nuki_all / n_all * 100  # 抜き発生率 (race / L4 universe)
    print(f"L4 universe ベース抜き発生率: {base_nuki_rate:.3f}% "
          f"({n_nuki_all} / {n_all})\n")

    candidates = [
        ("1艇 motor 2連率 <= 30",
         lambda r: r["m1_top2"] is not None and r["m1_top2"] <= 30.0),
        ("1艇 motor 2連率 <= 33",
         lambda r: r["m1_top2"] is not None and r["m1_top2"] <= 33.0),
        ("1艇 motor 2連率 <= 35",
         lambda r: r["m1_top2"] is not None and r["m1_top2"] <= 35.0),
        ("1艇 国1着率 >= 7",
         lambda r: r["n1_t1"] is not None and r["n1_t1"] >= 7.0),
        ("1艇 国1着率 >= 7.5",
         lambda r: r["n1_t1"] is not None and r["n1_t1"] >= 7.5),
        ("1艇 国1着率 >= 8",
         lambda r: r["n1_t1"] is not None and r["n1_t1"] >= 8.0),
        ("2艇 国2連率 >= 45",
         lambda r: r["n2_t2"] is not None and r["n2_t2"] >= 45.0),
        ("2艇 国2連率 >= 50",
         lambda r: r["n2_t2"] is not None and r["n2_t2"] >= 50.0),
        ("2艇 国1着率 >= 5.5",
         lambda r: r["n2_t1"] is not None and r["n2_t1"] >= 5.5),
        ("3艇 国2連率 >= 40",
         lambda r: r["n3_t2"] is not None and r["n3_t2"] >= 40.0),
        ("3艇 国1着率 >= 5.5",
         lambda r: r["n3_t1"] is not None and r["n3_t1"] >= 5.5),
        ("1艇 age >= 35 × ST <= 0.16",
         lambda r: r["age1"] is not None and r["age1"] >= 35
                   and r["st1"] is not None and r["st1"] <= 0.16),
        ("1艇 age >= 30 × ST <= 0.16",
         lambda r: r["age1"] is not None and r["age1"] >= 30
                   and r["st1"] is not None and r["st1"] <= 0.16),
        ("会場 桐生 (1)", lambda r: r["stadium_number"] == 1),
        ("会場 浜名湖 (6)", lambda r: r["stadium_number"] == 6),
        ("会場 江戸川 (3)", lambda r: r["stadium_number"] == 3),
        ("会場 尼崎 (13)", lambda r: r["stadium_number"] == 13),
    ]

    print(f"  {'条件':<40}  {'n_band':>6}  {'抜き数':>5}  {'抜き率%':>8}  {'lift':>6}")
    print("-" * 80)
    for label, pred in candidates:
        band_rows = [r for r in rows if pred(r)]
        n_band = len(band_rows)
        n_nuki = sum(1 for r in band_rows if r["kim"] == "抜き")
        rate = n_nuki / n_band * 100 if n_band else 0
        lift = rate / base_nuki_rate if base_nuki_rate else 0
        marker = " ★" if lift >= 1.3 and n_band >= 200 else ""
        print(f"  {label:<40}  {n_band:>6}  {n_nuki:>5}  {rate:>7.3f}%  {lift:>5.2f}x{marker}")

    # ============================================================
    # 4. AND 組合せ — 抜きフィルター候補
    # ============================================================
    print("\n" + "=" * 100)
    print("[4] AND 組合せフィルター — 抜き発生率 + 1-2-3 hit 率/ROI 両指標")
    print("=" * 100)

    combos = [
        # 仮説 A: 「1艇 motor 弱 × 1艇 国1強」
        ("motor<=33 ∧ 国1>=7",
         lambda r: r["m1_top2"] is not None and r["m1_top2"] <= 33.0
                   and r["n1_t1"] is not None and r["n1_t1"] >= 7.0),
        ("motor<=35 ∧ 国1>=7",
         lambda r: r["m1_top2"] is not None and r["m1_top2"] <= 35.0
                   and r["n1_t1"] is not None and r["n1_t1"] >= 7.0),
        # 仮説 B: motor 弱 × 2艇強 (= 1艇譲って 2艇先頭、その後抜く)
        ("motor<=33 ∧ 2艇国2連率>=45",
         lambda r: r["m1_top2"] is not None and r["m1_top2"] <= 33.0
                   and r["n2_t2"] is not None and r["n2_t2"] >= 45.0),
        ("motor<=35 ∧ 2艇国2連率>=45",
         lambda r: r["m1_top2"] is not None and r["m1_top2"] <= 35.0
                   and r["n2_t2"] is not None and r["n2_t2"] >= 45.0),
        # 仮説 C: motor 弱 × 国1 強 × 2艇 強 (3条件)
        ("motor<=35 ∧ 国1>=7 ∧ 2艇国2連率>=45",
         lambda r: r["m1_top2"] is not None and r["m1_top2"] <= 35.0
                   and r["n1_t1"] is not None and r["n1_t1"] >= 7.0
                   and r["n2_t2"] is not None and r["n2_t2"] >= 45.0),
        # 仮説 D: ベテラン × motor 弱 × 国1 強
        ("motor<=35 ∧ 国1>=7 ∧ age>=30",
         lambda r: r["m1_top2"] is not None and r["m1_top2"] <= 35.0
                   and r["n1_t1"] is not None and r["n1_t1"] >= 7.0
                   and r["age1"] is not None and r["age1"] >= 30),
        # 仮説 E: motor 弱 ∧ 国1高 ∧ 3艇強 (3艇から 3着抜けてくる)
        ("motor<=35 ∧ 国1>=7 ∧ 3艇国2連率>=40",
         lambda r: r["m1_top2"] is not None and r["m1_top2"] <= 35.0
                   and r["n1_t1"] is not None and r["n1_t1"] >= 7.0
                   and r["n3_t2"] is not None and r["n3_t2"] >= 40.0),
        # 仮説 F: motor 弱 ∧ 1艇局1高 (局所慣熟)
        ("motor<=33 ∧ 局1>=7",
         lambda r: r["m1_top2"] is not None and r["m1_top2"] <= 33.0
                   and r["l1_t1"] is not None and r["l1_t1"] >= 7.0),
        # 仮説 G: motor 弱+ベテラン ST  (= L4 PRO 型 + motor 弱)
        ("motor<=35 ∧ age>=35 ∧ ST<=0.16",
         lambda r: r["m1_top2"] is not None and r["m1_top2"] <= 35.0
                   and r["age1"] is not None and r["age1"] >= 35
                   and r["st1"] is not None and r["st1"] <= 0.16),
        # 仮説 H: motor 弱 + 国1 強 + 2艇 motor 強 + 3艇 motor 強 (前を強い艇に挟まれる)
        ("motor<=35 ∧ 国1>=7 ∧ 2艇motor>=40",
         lambda r: r["m1_top2"] is not None and r["m1_top2"] <= 35.0
                   and r["n1_t1"] is not None and r["n1_t1"] >= 7.0
                   and r["m2_top2"] is not None and r["m2_top2"] >= 40.0),
    ]

    print(f"  {'条件':<48} {'n_band':>6} {'抜き':>5} {'抜率%':>7} {'lift':>5}  {'123hit%':>8} {'ROI%':>7}")
    print("-" * 105)
    for label, pred in combos:
        band_rows = [r for r in rows if pred(r)]
        n_band = len(band_rows)
        n_nuki = sum(1 for r in band_rows if r["kim"] == "抜き")
        rate = n_nuki / n_band * 100 if n_band else 0
        lift = rate / base_nuki_rate if base_nuki_rate else 0
        n_hits = sum(1 for r in band_rows if r["p_123"] is not None)
        sum_pay = sum(int(r["p_123"]) for r in band_rows if r["p_123"] is not None)
        hit_rate = (n_hits / n_band * 100) if n_band else 0
        roi = (sum_pay / (100 * n_band) * 100) if n_band else 0
        marker = " ★" if (hit_rate >= 17 and roi >= 140 and n_band >= 200) else ""
        print(f"  {label:<48} {n_band:>6} {n_nuki:>5} {rate:>6.2f}% {lift:>4.2f}x  "
              f"{hit_rate:>7.2f}% {roi:>6.1f}%{marker}")

    # ============================================================
    # 5. Stadium-level 抜き-among-1着率 ranking
    #    (= 採用フィルターの最も強力な根拠)
    # ============================================================
    print("\n" + "=" * 100)
    print("[5] Stadium 別 抜き集中度 (L4 universe + boat1-1着 cohort)")
    print("=" * 100)
    print(f"  {'stadium':<8} {'n_won':>6} {'抜き数':>5} {'抜き率%':>8} {'lift':>5}")
    print("-" * 60)
    boat1_won_rows = [r for r in rows if r["pos1"] == 1 and r["kim"] is not None]
    n_total = len(boat1_won_rows)
    n_nuki_total = sum(1 for r in boat1_won_rows if r["kim"] == "抜き")
    base_rate = n_nuki_total / n_total * 100 if n_total else 0
    print(f"  (cohort base: n={n_total}, 抜き={n_nuki_total}, 抜き率={base_rate:.2f}%)")
    print()
    stadium_stats = []
    for s in sorted({r["stadium_number"] for r in boat1_won_rows if r["stadium_number"] is not None}):
        sub = [r for r in boat1_won_rows if r["stadium_number"] == s]
        n = len(sub)
        nk = sum(1 for r in sub if r["kim"] == "抜き")
        rate = nk / n * 100 if n else 0
        lift = rate / base_rate if base_rate else 0
        stadium_stats.append((s, n, nk, rate, lift))
    stadium_stats.sort(key=lambda x: -x[3])
    for s, n, nk, rate, lift in stadium_stats:
        marker = " ★" if lift >= 1.3 and n >= 100 else ""
        print(f"  st{s:<6} {n:>6} {nk:>5} {rate:>7.2f}% {lift:>4.2f}x{marker}")

    # ============================================================
    # 6. ★ 採用フィルター: train/test 時系列スプリット検証
    # ============================================================
    print("\n" + "=" * 100)
    print(f"[6] 採用フィルター 時系列スプリット (train < {TRAIN_SPLIT} / test >= {TRAIN_SPLIT})")
    print("=" * 100)

    candidate_filters = [
        # 探索の結果有意でなかったが記録のため残す
        ("motor<=35 ∧ 国1>=7 (探索)",
         lambda r: r["m1_top2"] is not None and r["m1_top2"] <= 35.0
                   and r["n1_t1"] is not None and r["n1_t1"] >= 7.0),
        # ★ 採用フィルター: stadium ∈ {3 江戸川, 6 浜名湖, 14 鳴門}
        ("★ stadium ∈ {3, 6, 14} (採用フィルター)",
         lambda r: r["stadium_number"] in {3, 6, 14}),
        # 単独 stadium 比較
        ("江戸川 (3) のみ",
         lambda r: r["stadium_number"] == 3),
        ("江戸川 + 鳴門 + 浜名湖 + 若松",
         lambda r: r["stadium_number"] in {3, 6, 14, 20}),
    ]

    print("Pre-race filter (L4 universe entire — bet-able view):")
    print(f"  {'条件':<48}  {'split':<6} {'n':>5} {'123hit%':>8} {'ROI%':>7} {'avg_pay':>8} {'抜率%':>6}")
    print("-" * 105)
    for label, pred in candidate_filters:
        for split, sub in [("train", [r for r in rows if r["race_date"] < TRAIN_SPLIT]),
                            ("test", [r for r in rows if r["race_date"] >= TRAIN_SPLIT])]:
            band = [r for r in sub if pred(r)]
            n = len(band)
            n_hits = sum(1 for r in band if r["p_123"] is not None)
            sum_pay = sum(int(r["p_123"]) for r in band if r["p_123"] is not None)
            n_nuki = sum(1 for r in band if r["kim"] == "抜き")
            hr = n_hits / n * 100 if n else 0
            roi = sum_pay / (100 * n) * 100 if n else 0
            nuki_rate = n_nuki / n * 100 if n else 0
            avg_pay = sum_pay / n_hits if n_hits else 0
            print(f"  {label:<48}  {split:<6} {n:>5} {hr:>7.2f}% {roi:>6.1f}% {avg_pay:>7.1f} {nuki_rate:>5.2f}%")
        print()

    print()
    print("Within boat1-1着 cohort (matches user's 13.8% / 14.5% benchmark):")
    print(f"  {'条件':<48}  {'split':<6} {'n':>5} {'123hit%':>8} {'ROI%':>7} {'avg_pay':>8} {'抜率%':>6}")
    print("-" * 105)
    for label, pred in candidate_filters:
        for split, sub in [("train", [r for r in rows if r["race_date"] < TRAIN_SPLIT and r["pos1"] == 1]),
                            ("test", [r for r in rows if r["race_date"] >= TRAIN_SPLIT and r["pos1"] == 1])]:
            band = [r for r in sub if pred(r)]
            n = len(band)
            n_hits = sum(1 for r in band if r["p_123"] is not None)
            sum_pay = sum(int(r["p_123"]) for r in band if r["p_123"] is not None)
            n_nuki = sum(1 for r in band if r["kim"] == "抜き")
            hr = n_hits / n * 100 if n else 0
            roi = sum_pay / (100 * n) * 100 if n else 0
            nuki_rate = n_nuki / n * 100 if n else 0
            avg_pay = sum_pay / n_hits if n_hits else 0
            marker = " ★" if roi >= 130 and n >= 100 else ""
            print(f"  {label:<48}  {split:<6} {n:>5} {hr:>7.2f}% {roi:>6.1f}% {avg_pay:>7.1f} {nuki_rate:>5.2f}%{marker}")
        print()


if __name__ == "__main__":
    main()
