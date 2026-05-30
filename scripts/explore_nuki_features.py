"""L4 universe で「1号艇が抜きで勝つ race」の事前特徴 (race 開始前に分かる指標) を
多軸で系統的に探索する。

L4 universe 定義 (本検証):
  - 1号艇 class_number = 1 (A1)
  - r.stadium_number NOT IN (2,4,7,8,10,19,21,24) (B 除外会場)
  - 全選手が男性 (no female)
  - race_previews.weather_number != 3 (雨除外)
  - race_results.kimarite IS NOT NULL (= 2022-05-08 〜 2025-06-30 の期間のみ)

母数: 26,242 races
1号艇 1着 (うち): 18,596 races
1号艇 1着 × 抜き: 732 races
→ ベース「抜き hit 率」 = 732 / 26,242 = 2.79% (L4 universe ベース)
→ ベース「1-2-3 hit 率」(L4 universe 全体): 2,580 / 26,242 = 9.83%
→ ベース「平均配当」 = 836円
→ ベース ROI (全 L4 1-2-3 100円買い) = 9.83% × 836 / 100 = 82.2%

注: ユーザー文書記載の「4.7%」 は 732/18,596=3.94% の typo と判断。
    本検証では L4 universe 母数 (26,242) を分母とした「抜き hit 率」を主指標、
    1-2-3 hit 率と ROI は L4 universe 全体で 100円買った場合の値で揃える。

探索軸:
  A. 1号艇 事前指標
  B. 2号艇 事前指標
  C. 3号艇 事前指標
  D. 4-6 号艇 (motor / 国1着率) — 1号艇 不調を引き出すか
  E. race-level (race_number / grade / 風速 / 風向 / 開催場)
  F. 組合せ (top 2-3 axes クロス)

検証手法:
  - 各サブセットで:
      * n (race 数)
      * 抜き hit 率 (vs L4 universe base 2.79%)
      * 1-2-3 hit 率 (vs L4 universe base 9.83%)
      * 平均配当
      * ROI (= 1-2-3 hit 率 × 平均配当 / 100)
  - 時系列スプリット (train < 2025-01-01, test >= 2025-01-01) で robust 判定

完了基準:
  - 「抜き hit 率が L4 base (2.79%) より +50% 以上 (= 4.2%+)」になる事前条件を最低 3 件発見
"""
from __future__ import annotations

import os
import sys
import sqlite3
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ローカル SQLite 強制 (Supabase は 2025-06-30 までしか kimarite ない可能性も同じ)
os.environ.pop("DATABASE_URL", None)

DB_PATH = str(Path(__file__).resolve().parents[1] / "data" / "boatrace.db")
SPLIT_DATE = "2025-01-01"
EXCLUDE_B = (2, 4, 7, 8, 10, 19, 21, 24)
NUKI_KIMARITE = "抜き"

STADIUM_NAMES = {
    1: "桐生", 2: "戸田", 3: "江戸川", 4: "平和島", 5: "多摩川", 6: "浜名湖",
    7: "蒲郡", 8: "常滑", 9: "津", 10: "三国", 11: "びわこ", 12: "住之江",
    13: "尼崎", 14: "鳴門", 15: "丸亀", 16: "児島", 17: "宮島", 18: "徳山",
    19: "下関", 20: "若松", 21: "芦屋", 22: "福岡", 23: "唐津", 24: "大村",
}

GRADE_NAMES = {1: "SG", 2: "G1", 3: "G2", 4: "G3", 5: "一般戦"}

# ============================================================
# L4 universe 1 row / race のデータを 1 回だけ集める
# ============================================================
BASE_SQL = f"""
WITH gc AS (
    SELECT e.race_id,
           SUM(CASE WHEN ra.gender=2 THEN 1 ELSE 0 END) AS n_female
    FROM race_entries e
    JOIN racers ra ON e.racer_number = ra.racer_number
    GROUP BY e.race_id
)
SELECT
    r.race_id,
    r.race_date,
    r.stadium_number,
    r.race_number,
    r.race_grade_number,
    -- 1号艇 entry
    e1.racer_number          AS r1_number,
    e1.class_number          AS r1_class,
    e1.age                   AS r1_age,
    e1.weight                AS r1_weight,
    e1.flying_count          AS r1_flying,
    e1.late_count            AS r1_late,
    e1.avg_start_timing      AS r1_avg_st,
    e1.national_top_1_percent AS r1_natl1,
    e1.national_top_2_percent AS r1_natl2,
    e1.national_top_3_percent AS r1_natl3,
    e1.local_top_1_percent   AS r1_local1,
    e1.local_top_2_percent   AS r1_local2,
    e1.local_top_3_percent   AS r1_local3,
    e1.assigned_motor_top_2_percent AS r1_mot2,
    e1.assigned_motor_top_3_percent AS r1_mot3,
    e1.assigned_boat_top_2_percent  AS r1_boat2,
    -- 2-6号艇 (代表指標 - JOIN avoid scan-bloat)
    e2.national_top_1_percent AS r2_natl1,
    e2.national_top_2_percent AS r2_natl2,
    e2.assigned_motor_top_2_percent AS r2_mot2,
    e2.class_number          AS r2_class,
    e2.avg_start_timing      AS r2_avg_st,
    e3.national_top_1_percent AS r3_natl1,
    e3.national_top_2_percent AS r3_natl2,
    e3.assigned_motor_top_2_percent AS r3_mot2,
    e3.class_number          AS r3_class,
    e4.national_top_1_percent AS r4_natl1,
    e4.assigned_motor_top_2_percent AS r4_mot2,
    e4.class_number          AS r4_class,
    e5.national_top_1_percent AS r5_natl1,
    e5.assigned_motor_top_2_percent AS r5_mot2,
    e6.national_top_1_percent AS r6_natl1,
    e6.assigned_motor_top_2_percent AS r6_mot2,
    -- race_preview (boat 1 row only)
    pv.weather_number        AS weather,
    pv.wind_speed            AS wind_sp,
    pv.wind_direction_number AS wind_dir,
    pv.wave_height           AS wave,
    pv.temperature           AS temp,
    pv.water_temperature     AS water_temp,
    -- 結果
    rr1.boat_number          AS winner_boat,
    rr1.kimarite             AS kimarite,
    -- 3連単 1-2-3 配当
    p123.payout              AS pay_123,
    -- 単勝 1 配当
    pw1.payout               AS pay_win1
  FROM races r
  JOIN race_entries e1 ON e1.race_id=r.race_id AND e1.boat_number=1
  LEFT JOIN race_entries e2 ON e2.race_id=r.race_id AND e2.boat_number=2
  LEFT JOIN race_entries e3 ON e3.race_id=r.race_id AND e3.boat_number=3
  LEFT JOIN race_entries e4 ON e4.race_id=r.race_id AND e4.boat_number=4
  LEFT JOIN race_entries e5 ON e5.race_id=r.race_id AND e5.boat_number=5
  LEFT JOIN race_entries e6 ON e6.race_id=r.race_id AND e6.boat_number=6
  LEFT JOIN race_previews pv ON pv.race_id=r.race_id AND pv.boat_number=1
  JOIN race_results rr1 ON rr1.race_id=r.race_id AND rr1.finishing_position=1
  JOIN gc ON gc.race_id=r.race_id
  LEFT JOIN race_payouts p123 ON p123.race_id=r.race_id
                              AND p123.bet_type='trifecta' AND p123.combination='1-2-3'
  LEFT JOIN race_payouts pw1 ON pw1.race_id=r.race_id
                             AND pw1.bet_type='win' AND pw1.combination='1'
 WHERE e1.class_number = 1
   AND r.stadium_number NOT IN {EXCLUDE_B}
   AND gc.n_female = 0
   AND (pv.weather_number IS NULL OR pv.weather_number != 3)
   AND rr1.kimarite IS NOT NULL
"""

# field index 名簿 (sqlite row_factory 不使用 → tuple index)
FIELDS = [
    "race_id","race_date","stadium","race_number","grade",
    "r1_number","r1_class","r1_age","r1_weight","r1_flying","r1_late","r1_avg_st",
    "r1_natl1","r1_natl2","r1_natl3","r1_local1","r1_local2","r1_local3",
    "r1_mot2","r1_mot3","r1_boat2",
    "r2_natl1","r2_natl2","r2_mot2","r2_class","r2_avg_st",
    "r3_natl1","r3_natl2","r3_mot2","r3_class",
    "r4_natl1","r4_mot2","r4_class",
    "r5_natl1","r5_mot2",
    "r6_natl1","r6_mot2",
    "weather","wind_sp","wind_dir","wave","temp","water_temp",
    "winner_boat","kimarite","pay_123","pay_win1",
]
IDX = {f: i for i, f in enumerate(FIELDS)}


def load_data():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(BASE_SQL).fetchall()
    conn.close()
    return rows


# ============================================================
# 統計集計 helper
# ============================================================
def stats(rows, label: str = ""):
    """rows のリストから core stats を返す。
    Returns: dict with n, nuki, nuki_rate, hit123, hit123_rate, avg_pay_123, roi
    """
    n = len(rows)
    if n == 0:
        return {"label": label, "n": 0, "nuki": 0, "nuki_rate": 0.0,
                "hit123": 0, "hit123_rate": 0.0, "avg_pay": 0.0, "roi": 0.0,
                "win1": 0, "win1_rate": 0.0}
    nuki = sum(1 for r in rows
               if r[IDX["winner_boat"]] == 1 and r[IDX["kimarite"]] == NUKI_KIMARITE)
    hit123 = sum(1 for r in rows if r[IDX["pay_123"]] is not None and r[IDX["pay_123"]] > 0)
    sum_pay = sum((r[IDX["pay_123"]] or 0) for r in rows)
    win1 = sum(1 for r in rows if r[IDX["winner_boat"]] == 1)
    return {
        "label": label,
        "n": n,
        "nuki": nuki,
        "nuki_rate": 100.0 * nuki / n,
        "hit123": hit123,
        "hit123_rate": 100.0 * hit123 / n,
        "avg_pay": sum_pay / hit123 if hit123 else 0.0,
        "roi": 100.0 * sum_pay / (100 * n),
        "win1": win1,
        "win1_rate": 100.0 * win1 / n,
    }


def split_train_test(rows):
    tr = [r for r in rows if r[IDX["race_date"]] < SPLIT_DATE]
    te = [r for r in rows if r[IDX["race_date"]] >= SPLIT_DATE]
    return tr, te


def stats_split(rows, label: str = ""):
    tr, te = split_train_test(rows)
    return {
        "label": label,
        "all": stats(rows, "all"),
        "train": stats(tr, f"train(<{SPLIT_DATE})"),
        "test": stats(te, f"test(>={SPLIT_DATE})"),
    }


# ============================================================
# 検証軸定義
# ============================================================
def filter_by(rows, predicate):
    return [r for r in rows if predicate(r)]


def _has(r, key):
    return r[IDX[key]] is not None


def axis_bins(rows, field: str, bins: list[tuple[float | None, float | None, str]]):
    """連続値 field を bin に分けて stats 返す。
    bins = [(lo, hi, label), ...] (lo, hi: None で open)
    """
    out = []
    for lo, hi, label in bins:
        def pred(r, lo=lo, hi=hi):
            v = r[IDX[field]]
            if v is None:
                return False
            if lo is not None and v < lo:
                return False
            if hi is not None and v >= hi:
                return False
            return True
        sub = filter_by(rows, pred)
        s = stats(sub, f"{field}: {label}")
        out.append(s)
    return out


def print_axis_table(title: str, base: dict, results: list[dict]):
    """軸別 stats を表示。robust criteria 付き。"""
    print()
    print("=" * 110)
    print(f"  {title}")
    print("=" * 110)
    print(f"  {'subset':<46} {'n':>6} {'nuki%':>7} {'+x%':>7} "
          f"{'h123%':>7} {'avg¥':>6} {'ROI%':>7} {'w1%':>6}")
    print("-" * 110)
    base_nuki = base["nuki_rate"]
    base_roi = base["roi"]
    base_h = base["hit123_rate"]
    for s in results:
        if s["n"] < 30:
            tag = " (n<30)"
        else:
            nuki_lift = s["nuki_rate"] / base_nuki - 1 if base_nuki else 0
            roi_lift = s["roi"] - base_roi
            tags = []
            if nuki_lift >= 0.5 and s["n"] >= 100:
                tags.append("★抜き+50%")
            if s["roi"] >= 130 and s["n"] >= 100:
                tags.append("★ROI130+")
            tag = " " + "/".join(tags) if tags else ""
        nuki_lift_pct = (s["nuki_rate"] / base_nuki - 1) * 100 if base_nuki else 0.0
        print(f"  {s['label']:<46} {s['n']:>6} {s['nuki_rate']:>6.2f}% "
              f"{nuki_lift_pct:>+6.1f}% {s['hit123_rate']:>6.2f}% "
              f"{s['avg_pay']:>5.0f} {s['roi']:>6.2f}% {s['win1_rate']:>5.1f}%{tag}")


def print_split_block(label: str, sp: dict):
    """train/test 分割結果を表示"""
    print(f"  {label}")
    for blk in ["all", "train", "test"]:
        s = sp[blk]
        if s["n"] == 0:
            print(f"    {blk:<20} n=0")
            continue
        print(f"    {blk:<20} n={s['n']:>5} nuki={s['nuki_rate']:>5.2f}%  "
              f"h123={s['hit123_rate']:>5.2f}%  avg¥={s['avg_pay']:>5.0f}  ROI={s['roi']:>6.2f}%")


# ============================================================
# main
# ============================================================
def main():
    print("=" * 110)
    print("  L4 universe 抜き race 事前特徴 多軸探索")
    print("=" * 110)
    print(f"  DB: {DB_PATH}")
    print(f"  検証期間: 2022-05-08 〜 2025-06-30 (kimarite データ有期間)")
    print(f"  時系列 split: train < {SPLIT_DATE}, test >= {SPLIT_DATE}")
    print()

    print("Loading L4 universe rows...")
    rows = load_data()
    print(f"  Loaded {len(rows)} L4 races")

    base = stats(rows, "L4 universe (BASE)")
    base_sp = stats_split(rows, "L4 universe")
    print()
    print(f"  [BASE] n={base['n']}  nuki={base['nuki_rate']:.2f}%  "
          f"hit123={base['hit123_rate']:.2f}%  avg¥={base['avg_pay']:.0f}  "
          f"ROI={base['roi']:.2f}%  win1={base['win1_rate']:.1f}%")
    print(f"  TRAIN: n={base_sp['train']['n']}  nuki={base_sp['train']['nuki_rate']:.2f}%  "
          f"hit123={base_sp['train']['hit123_rate']:.2f}%  ROI={base_sp['train']['roi']:.2f}%")
    print(f"  TEST : n={base_sp['test']['n']}  nuki={base_sp['test']['nuki_rate']:.2f}%  "
          f"hit123={base_sp['test']['hit123_rate']:.2f}%  ROI={base_sp['test']['roi']:.2f}%")

    # robust 候補を集める
    robust_candidates: list[dict] = []  # 各候補: {label, all_stats, train_stats, test_stats, predicate}

    target_nuki = base["nuki_rate"] * 1.5  # +50% lift

    # ------------------------------------------------------------
    # A. 1号艇 事前指標 (continuous → bins)
    # ------------------------------------------------------------
    print()
    print("#" * 110)
    print("# A. 1号艇 事前指標")
    print("#" * 110)

    # A-1. motor 2連率
    res = axis_bins(rows, "r1_mot2", [
        (None, 30, "<30 (極弱)"),
        (30, 35, "[30-35) 弱"),
        (35, 40, "[35-40) やや弱"),
        (40, 45, "[40-45) 標準"),
        (45, 50, "[45-50) やや強"),
        (50, None, ">=50 強"),
    ])
    print_axis_table("A-1. 1号艇 motor 2連率", base, res)
    for s in res:
        if s["n"] >= 100 and s["nuki_rate"] >= target_nuki:
            robust_candidates.append({"label": s["label"]})

    # A-2. 1号艇 国 1着率
    res = axis_bins(rows, "r1_natl1", [
        (None, 4, "<4"),
        (4, 5.5, "[4-5.5)"),
        (5.5, 7, "[5.5-7)"),
        (7, 8.5, "[7-8.5)"),
        (8.5, 10, "[8.5-10)"),
        (10, None, ">=10 (トップ)"),
    ])
    print_axis_table("A-2. 1号艇 国 1着率", base, res)

    # A-3. 1号艇 国 2連率
    res = axis_bins(rows, "r1_natl2", [
        (None, 30, "<30"),
        (30, 40, "[30-40)"),
        (40, 50, "[40-50)"),
        (50, 60, "[50-60)"),
        (60, None, ">=60"),
    ])
    print_axis_table("A-3. 1号艇 国 2連率", base, res)

    # A-4. 1号艇 当地 1着率
    res = axis_bins(rows, "r1_local1", [
        (None, 3, "<3"),
        (3, 5, "[3-5)"),
        (5, 7, "[5-7)"),
        (7, 9, "[7-9)"),
        (9, None, ">=9"),
    ])
    print_axis_table("A-4. 1号艇 当地 1着率", base, res)

    # A-5. 1号艇 avg ST
    res = axis_bins(rows, "r1_avg_st", [
        (None, 0.14, "<0.14 (鋭い)"),
        (0.14, 0.16, "[0.14-0.16)"),
        (0.16, 0.18, "[0.16-0.18)"),
        (0.18, 0.20, "[0.18-0.20)"),
        (0.20, None, ">=0.20 (遅い)"),
    ])
    print_axis_table("A-5. 1号艇 平均 ST", base, res)

    # A-6. 1号艇 年齢
    res = axis_bins(rows, "r1_age", [
        (None, 30, "<30"),
        (30, 35, "[30-35)"),
        (35, 40, "[35-40)"),
        (40, 45, "[40-45)"),
        (45, 50, "[45-50)"),
        (50, None, ">=50 (ベテラン)"),
    ])
    print_axis_table("A-6. 1号艇 年齢", base, res)

    # A-7. 1号艇 体重
    res = axis_bins(rows, "r1_weight", [
        (None, 51, "<51"),
        (51, 53, "[51-53)"),
        (53, 55, "[53-55)"),
        (55, None, ">=55 (重い)"),
    ])
    print_axis_table("A-7. 1号艇 体重", base, res)

    # A-8. 1号艇 F (フライング) count
    res = []
    for fc, lbl in [(0, "F=0"), (1, "F=1"), (2, "F=2")]:
        sub = filter_by(rows, lambda r, fc=fc: r[IDX["r1_flying"]] == fc)
        res.append(stats(sub, f"r1_flying: {lbl}"))
    sub = filter_by(rows, lambda r: r[IDX["r1_flying"]] is not None and r[IDX["r1_flying"]] >= 3)
    res.append(stats(sub, "r1_flying: F>=3"))
    print_axis_table("A-8. 1号艇 フライング count", base, res)

    # A-9. 1号艇 motor 弱 × 国 1着率 強 (機序仮説: 立ち遅れ → 巻き返し)
    print()
    print("=" * 110)
    print("  A-9. 機序仮説: 1号艇 motor 弱 × 国 1着率 強 (=立ち遅れ → 巻き返し)")
    print("=" * 110)
    res = []
    for mlo, mhi, mlbl in [(None, 35, "mot<35"), (None, 40, "mot<40"), (None, 45, "mot<45")]:
        for nlo, nhi, nlbl in [(7, None, "natl1>=7"), (8, None, "natl1>=8"), (9, None, "natl1>=9")]:
            def pred(r, mlo=mlo, mhi=mhi, nlo=nlo, nhi=nhi):
                m = r[IDX["r1_mot2"]]
                n = r[IDX["r1_natl1"]]
                if m is None or n is None: return False
                if mlo is not None and m < mlo: return False
                if mhi is not None and m >= mhi: return False
                if nlo is not None and n < nlo: return False
                if nhi is not None and n >= nhi: return False
                return True
            sub = filter_by(rows, pred)
            res.append(stats(sub, f"{mlbl} & {nlbl}"))
    print_axis_table("(combo)", base, res)

    # ------------------------------------------------------------
    # B. 2号艇 事前指標 (2号艇が強い → 一旦先頭 → 1号艇抜き?)
    # ------------------------------------------------------------
    print()
    print("#" * 110)
    print("# B. 2号艇 事前指標 (2号艇が強い → 一旦先頭 → 1号艇抜き?)")
    print("#" * 110)

    # B-1. 2号艇 国 2連率
    res = axis_bins(rows, "r2_natl2", [
        (None, 30, "<30"),
        (30, 40, "[30-40)"),
        (40, 50, "[40-50)"),
        (50, 60, "[50-60)"),
        (60, None, ">=60"),
    ])
    print_axis_table("B-1. 2号艇 国 2連率", base, res)

    # B-2. 2号艇 国 1着率
    res = axis_bins(rows, "r2_natl1", [
        (None, 4, "<4"),
        (4, 6, "[4-6)"),
        (6, 8, "[6-8)"),
        (8, None, ">=8"),
    ])
    print_axis_table("B-2. 2号艇 国 1着率", base, res)

    # B-3. 2号艇 motor 2連率
    res = axis_bins(rows, "r2_mot2", [
        (None, 30, "<30"),
        (30, 40, "[30-40)"),
        (40, 50, "[40-50)"),
        (50, None, ">=50"),
    ])
    print_axis_table("B-3. 2号艇 motor 2連率", base, res)

    # B-4. 2号艇クラス
    res = []
    for cls, lbl in [(1, "A1"), (2, "A2"), (3, "B1"), (4, "B2")]:
        sub = filter_by(rows, lambda r, cls=cls: r[IDX["r2_class"]] == cls)
        res.append(stats(sub, f"r2_class: {lbl}"))
    print_axis_table("B-4. 2号艇 クラス", base, res)

    # B-5. 2号艇 avg ST
    res = axis_bins(rows, "r2_avg_st", [
        (None, 0.14, "<0.14"),
        (0.14, 0.16, "[0.14-0.16)"),
        (0.16, 0.18, "[0.16-0.18)"),
        (0.18, None, ">=0.18"),
    ])
    print_axis_table("B-5. 2号艇 平均 ST", base, res)

    # ------------------------------------------------------------
    # C. 3号艇 事前指標
    # ------------------------------------------------------------
    print()
    print("#" * 110)
    print("# C. 3号艇 事前指標")
    print("#" * 110)

    # C-1. 3号艇 国 2連率
    res = axis_bins(rows, "r3_natl2", [
        (None, 30, "<30"),
        (30, 40, "[30-40)"),
        (40, 50, "[40-50)"),
        (50, None, ">=50"),
    ])
    print_axis_table("C-1. 3号艇 国 2連率", base, res)

    # C-2. 3号艇 motor 2連率
    res = axis_bins(rows, "r3_mot2", [
        (None, 30, "<30"),
        (30, 40, "[30-40)"),
        (40, 50, "[40-50)"),
        (50, None, ">=50"),
    ])
    print_axis_table("C-2. 3号艇 motor 2連率", base, res)

    # C-3. 3号艇クラス
    res = []
    for cls, lbl in [(1, "A1"), (2, "A2"), (3, "B1"), (4, "B2")]:
        sub = filter_by(rows, lambda r, cls=cls: r[IDX["r3_class"]] == cls)
        res.append(stats(sub, f"r3_class: {lbl}"))
    print_axis_table("C-3. 3号艇 クラス", base, res)

    # ------------------------------------------------------------
    # D. 4-6 号艇 (motor / 国1着率)
    # ------------------------------------------------------------
    print()
    print("#" * 110)
    print("# D. 4-6 号艇 (1号艇の不調を引き出すか)")
    print("#" * 110)

    # D-1. 4号艇 motor 2連率
    res = axis_bins(rows, "r4_mot2", [
        (None, 30, "<30"),
        (30, 40, "[30-40)"),
        (40, 50, "[40-50)"),
        (50, None, ">=50"),
    ])
    print_axis_table("D-1. 4号艇 motor 2連率", base, res)

    # D-2. 4号艇 国 1着率
    res = axis_bins(rows, "r4_natl1", [
        (None, 4, "<4"),
        (4, 6, "[4-6)"),
        (6, 8, "[6-8)"),
        (8, None, ">=8"),
    ])
    print_axis_table("D-2. 4号艇 国 1着率", base, res)

    # D-3. 4号艇クラス
    res = []
    for cls, lbl in [(1, "A1"), (2, "A2"), (3, "B1"), (4, "B2")]:
        sub = filter_by(rows, lambda r, cls=cls: r[IDX["r4_class"]] == cls)
        res.append(stats(sub, f"r4_class: {lbl}"))
    print_axis_table("D-3. 4号艇 クラス", base, res)

    # D-4. avg(r4,r5,r6) motor 2連率 (外艇全体が弱い → 1号艇優位 → 抜き低い?)
    res = []
    for lo, hi, lbl in [(None, 30, "<30"), (30, 40, "[30-40)"), (40, 50, "[40-50)"), (50, None, ">=50")]:
        def pred(r, lo=lo, hi=hi):
            ms = [r[IDX[f"r{i}_mot2"]] for i in (4, 5, 6)]
            ms = [m for m in ms if m is not None]
            if len(ms) < 3: return False
            avg = sum(ms) / 3
            if lo is not None and avg < lo: return False
            if hi is not None and avg >= hi: return False
            return True
        sub = filter_by(rows, pred)
        res.append(stats(sub, f"avg(r456_mot2): {lbl}"))
    print_axis_table("D-4. 外艇(4-6) 平均 motor 2連率", base, res)

    # ------------------------------------------------------------
    # E. race-level (race_number / grade / 風 / 開催場)
    # ------------------------------------------------------------
    print()
    print("#" * 110)
    print("# E. race-level (race_number / grade / 風 / 開催場)")
    print("#" * 110)

    # E-1. race_number
    res = []
    for rn in range(1, 13):
        sub = filter_by(rows, lambda r, rn=rn: r[IDX["race_number"]] == rn)
        res.append(stats(sub, f"R{rn}"))
    print_axis_table("E-1. race_number 別", base, res)

    # E-2. grade
    res = []
    for g, lbl in GRADE_NAMES.items():
        sub = filter_by(rows, lambda r, g=g: r[IDX["grade"]] == g)
        res.append(stats(sub, f"grade={g} ({lbl})"))
    print_axis_table("E-2. グレード別", base, res)

    # E-3. 風速
    res = axis_bins(rows, "wind_sp", [
        (None, 1, "<1 (無風)"),
        (1, 3, "[1-3) 弱風"),
        (3, 5, "[3-5) 中風"),
        (5, 7, "[5-7) 強風"),
        (7, None, ">=7 強強風"),
    ])
    print_axis_table("E-3. 風速別", base, res)

    # E-4. 風向 (1-17)
    res = []
    for wd in range(1, 18):
        sub = filter_by(rows, lambda r, wd=wd: r[IDX["wind_dir"]] == wd)
        if sub:
            res.append(stats(sub, f"wind_dir={wd}"))
    print_axis_table("E-4. 風向別", base, res)

    # E-5. 開催場
    res = []
    for st in sorted(STADIUM_NAMES.keys()):
        if st in EXCLUDE_B: continue
        sub = filter_by(rows, lambda r, st=st: r[IDX["stadium"]] == st)
        res.append(stats(sub, f"{st} {STADIUM_NAMES[st]}"))
    # nuki_rate desc
    res.sort(key=lambda s: -s["nuki_rate"])
    print_axis_table("E-5. 開催場別 (nuki率 降順)", base, res)

    # E-6. 天候 (1=晴, 2=曇, 4=雪)
    res = []
    for w, lbl in [(1, "晴"), (2, "曇"), (4, "雪")]:
        sub = filter_by(rows, lambda r, w=w: r[IDX["weather"]] == w)
        res.append(stats(sub, f"weather={w} ({lbl})"))
    print_axis_table("E-6. 天候別", base, res)

    # ------------------------------------------------------------
    # F. 組合せ探索 (上で nuki_rate が高かった軸の交差)
    # ------------------------------------------------------------
    print()
    print("#" * 110)
    print("# F. 組合せ探索 (個別軸で抜き率高めの条件をクロス)")
    print("#" * 110)

    # F-1. 2号艇 強い × 3号艇 強い (両方先頭周辺 → 1号艇 抜き)
    res = []
    for r2_n2, r2lbl in [(40, ">=40"), (45, ">=45"), (50, ">=50")]:
        for r3_n2, r3lbl in [(35, ">=35"), (40, ">=40"), (45, ">=45")]:
            def pred(r, r2_n2=r2_n2, r3_n2=r3_n2):
                a = r[IDX["r2_natl2"]]
                b = r[IDX["r3_natl2"]]
                if a is None or b is None: return False
                return a >= r2_n2 and b >= r3_n2
            sub = filter_by(rows, pred)
            res.append(stats(sub, f"r2_natl2{r2lbl} & r3_natl2{r3lbl}"))
    print_axis_table("F-1. 2号艇強 × 3号艇強", base, res)

    # F-2. 1号艇 motor 弱 × 外艇 motor 弱 (低調戦)
    res = []
    for r1_lo, r1_hi, r1lbl in [(None, 35, "<35"), (None, 40, "<40")]:
        for o_lo, o_hi, olbl in [(None, 30, "<30"), (None, 35, "<35"), (None, 40, "<40")]:
            def pred(r, r1_lo=r1_lo, r1_hi=r1_hi, o_lo=o_lo, o_hi=o_hi):
                m1 = r[IDX["r1_mot2"]]
                if m1 is None: return False
                if r1_lo is not None and m1 < r1_lo: return False
                if r1_hi is not None and m1 >= r1_hi: return False
                ms = [r[IDX[f"r{i}_mot2"]] for i in (4, 5, 6)]
                ms = [m for m in ms if m is not None]
                if len(ms) < 3: return False
                avg = sum(ms) / 3
                if o_lo is not None and avg < o_lo: return False
                if o_hi is not None and avg >= o_hi: return False
                return True
            sub = filter_by(rows, pred)
            res.append(stats(sub, f"r1_mot2{r1lbl} & avg(r456_mot2){olbl}"))
    print_axis_table("F-2. 1号艇 mot 弱 × 外艇 mot 弱 (低調戦)", base, res)

    # F-3. 1号艇 ST 早 × 2号艇 ST 早 (ST 競り合い)
    res = []
    for r1_lo, r1_hi, r1lbl in [(None, 0.14, "<0.14"), (None, 0.16, "<0.16")]:
        for r2_lo, r2_hi, r2lbl in [(None, 0.14, "<0.14"), (None, 0.16, "<0.16")]:
            def pred(r, r1_lo=r1_lo, r1_hi=r1_hi, r2_lo=r2_lo, r2_hi=r2_hi):
                a = r[IDX["r1_avg_st"]]
                b = r[IDX["r2_avg_st"]]
                if a is None or b is None: return False
                if r1_lo is not None and a < r1_lo: return False
                if r1_hi is not None and a >= r1_hi: return False
                if r2_lo is not None and b < r2_lo: return False
                if r2_hi is not None and b >= r2_hi: return False
                return True
            sub = filter_by(rows, pred)
            res.append(stats(sub, f"r1_ST{r1lbl} & r2_ST{r2lbl}"))
    print_axis_table("F-3. 1号艇 ST 早 × 2号艇 ST 早", base, res)

    # F-4. 開催場 × 風速 — 抜き高い会場の風速依存
    print()
    print("=" * 110)
    print("  F-4. 開催場 × 風速 cross")
    print("=" * 110)
    print(f"  {'subset':<46} {'n':>6} {'nuki%':>7} {'h123%':>7} {'avg¥':>6} {'ROI%':>7}")
    print("-" * 110)
    # 抜き率高めの会場 (上から 6 つ) について風速 bin
    stadium_nuki = []
    for st in sorted(STADIUM_NAMES.keys()):
        if st in EXCLUDE_B: continue
        sub = filter_by(rows, lambda r, st=st: r[IDX["stadium"]] == st)
        s = stats(sub)
        stadium_nuki.append((st, s["nuki_rate"], s["n"]))
    stadium_nuki.sort(key=lambda x: -x[1])
    top_stadia = [st for st, _, n in stadium_nuki[:6] if n >= 500]
    cross_res = []
    for st in top_stadia:
        for lo, hi, lbl in [(None, 3, "風<3"), (3, 6, "風3-6"), (6, None, "風>=6")]:
            def pred(r, st=st, lo=lo, hi=hi):
                if r[IDX["stadium"]] != st: return False
                ws = r[IDX["wind_sp"]]
                if ws is None: return False
                if lo is not None and ws < lo: return False
                if hi is not None and ws >= hi: return False
                return True
            sub = filter_by(rows, pred)
            cross_res.append(stats(sub, f"{STADIUM_NAMES[st]} × {lbl}"))
    print_axis_table("F-4. 抜き率高め会場 × 風速", base, cross_res)

    # F-5. 開催場 × 1号艇 motor 弱
    res = []
    for st in top_stadia:
        for lo, hi, lbl in [(None, 35, "mot<35"), (None, 40, "mot<40")]:
            def pred(r, st=st, hi=hi):
                if r[IDX["stadium"]] != st: return False
                m = r[IDX["r1_mot2"]]
                if m is None: return False
                if m >= hi: return False
                return True
            sub = filter_by(rows, pred)
            res.append(stats(sub, f"{STADIUM_NAMES[st]} × {lbl}"))
    print_axis_table("F-5. 抜き率高め会場 × 1号艇 mot 弱", base, res)

    # ------------------------------------------------------------
    # G. 最終 robust 候補抽出 + 時系列 split
    # ------------------------------------------------------------
    print()
    print("#" * 110)
    print("# G. 最終 robust 候補 — 時系列 split で再検証")
    print("#" * 110)

    # 候補条件リスト (上の探索から手動でピックアップしうるもの全部):
    # 評価関数: 単一 predicate で表現可能なものを列挙
    candidates = [
        ("1号艇 motor<35", lambda r: r[IDX["r1_mot2"]] is not None and r[IDX["r1_mot2"]] < 35),
        ("1号艇 motor<40", lambda r: r[IDX["r1_mot2"]] is not None and r[IDX["r1_mot2"]] < 40),
        ("1号艇 motor<30", lambda r: r[IDX["r1_mot2"]] is not None and r[IDX["r1_mot2"]] < 30),
        ("1号艇 natl1>=8", lambda r: r[IDX["r1_natl1"]] is not None and r[IDX["r1_natl1"]] >= 8),
        ("1号艇 natl1>=9", lambda r: r[IDX["r1_natl1"]] is not None and r[IDX["r1_natl1"]] >= 9),
        ("1号艇 mot<40 & natl1>=8",
         lambda r: r[IDX["r1_mot2"]] is not None and r[IDX["r1_natl1"]] is not None
                   and r[IDX["r1_mot2"]] < 40 and r[IDX["r1_natl1"]] >= 8),
        ("1号艇 mot<35 & natl1>=8",
         lambda r: r[IDX["r1_mot2"]] is not None and r[IDX["r1_natl1"]] is not None
                   and r[IDX["r1_mot2"]] < 35 and r[IDX["r1_natl1"]] >= 8),
        ("1号艇 mot<40 & natl1>=7",
         lambda r: r[IDX["r1_mot2"]] is not None and r[IDX["r1_natl1"]] is not None
                   and r[IDX["r1_mot2"]] < 40 and r[IDX["r1_natl1"]] >= 7),
        ("1号艇 age>=45", lambda r: r[IDX["r1_age"]] is not None and r[IDX["r1_age"]] >= 45),
        ("1号艇 age>=50", lambda r: r[IDX["r1_age"]] is not None and r[IDX["r1_age"]] >= 50),
        ("1号艇 F>=1", lambda r: r[IDX["r1_flying"]] is not None and r[IDX["r1_flying"]] >= 1),
        ("1号艇 F>=2", lambda r: r[IDX["r1_flying"]] is not None and r[IDX["r1_flying"]] >= 2),
        ("2号艇 natl2>=50", lambda r: r[IDX["r2_natl2"]] is not None and r[IDX["r2_natl2"]] >= 50),
        ("2号艇 natl2>=55", lambda r: r[IDX["r2_natl2"]] is not None and r[IDX["r2_natl2"]] >= 55),
        ("2号艇 natl1>=7", lambda r: r[IDX["r2_natl1"]] is not None and r[IDX["r2_natl1"]] >= 7),
        ("2号艇 class=A1", lambda r: r[IDX["r2_class"]] == 1),
        ("3号艇 natl2>=40", lambda r: r[IDX["r3_natl2"]] is not None and r[IDX["r3_natl2"]] >= 40),
        ("3号艇 class=A1", lambda r: r[IDX["r3_class"]] == 1),
        ("外艇avg(r456_mot2)<35",
         lambda r: all(r[IDX[f"r{i}_mot2"]] is not None for i in (4,5,6))
                   and sum(r[IDX[f"r{i}_mot2"]] for i in (4,5,6)) / 3 < 35),
        ("外艇avg(r456_mot2)<40",
         lambda r: all(r[IDX[f"r{i}_mot2"]] is not None for i in (4,5,6))
                   and sum(r[IDX[f"r{i}_mot2"]] for i in (4,5,6)) / 3 < 40),
        ("風速 >=6",
         lambda r: r[IDX["wind_sp"]] is not None and r[IDX["wind_sp"]] >= 6),
        ("風速 >=7",
         lambda r: r[IDX["wind_sp"]] is not None and r[IDX["wind_sp"]] >= 7),
        ("grade=SG", lambda r: r[IDX["grade"]] == 1),
        ("grade=G1", lambda r: r[IDX["grade"]] == 2),
        ("grade=G3", lambda r: r[IDX["grade"]] == 4),
        ("grade=一般戦", lambda r: r[IDX["grade"]] == 5),
        # 2号艇 強 × 3号艇 強
        ("2号艇 natl2>=50 & 3号艇 natl2>=40",
         lambda r: r[IDX["r2_natl2"]] is not None and r[IDX["r3_natl2"]] is not None
                   and r[IDX["r2_natl2"]] >= 50 and r[IDX["r3_natl2"]] >= 40),
        ("2号艇 natl2>=55 & 3号艇 natl2>=45",
         lambda r: r[IDX["r2_natl2"]] is not None and r[IDX["r3_natl2"]] is not None
                   and r[IDX["r2_natl2"]] >= 55 and r[IDX["r3_natl2"]] >= 45),
        # 1号艇 mot 弱 × 2号艇 強
        ("1号艇 mot<40 & 2号艇 natl2>=50",
         lambda r: r[IDX["r1_mot2"]] is not None and r[IDX["r2_natl2"]] is not None
                   and r[IDX["r1_mot2"]] < 40 and r[IDX["r2_natl2"]] >= 50),
        ("1号艇 mot<40 & 2号艇 class=A1",
         lambda r: r[IDX["r1_mot2"]] is not None
                   and r[IDX["r1_mot2"]] < 40 and r[IDX["r2_class"]] == 1),
        ("1号艇 mot<35 & 2号艇 class=A1",
         lambda r: r[IDX["r1_mot2"]] is not None
                   and r[IDX["r1_mot2"]] < 35 and r[IDX["r2_class"]] == 1),
        # 1号艇 mot 弱 × 外艇 weak (低調戦; 抜き低い予想)
        ("1号艇 mot<35 & 外艇avg(r456_mot2)<35",
         lambda r: r[IDX["r1_mot2"]] is not None and r[IDX["r1_mot2"]] < 35
                   and all(r[IDX[f"r{i}_mot2"]] is not None for i in (4,5,6))
                   and sum(r[IDX[f"r{i}_mot2"]] for i in (4,5,6)) / 3 < 35),
        # 開催場 (探索で抜き率高めだった会場 単独)
        ("江戸川 (sta=3)", lambda r: r[IDX["stadium"]] == 3),
        ("鳴門 (sta=14)", lambda r: r[IDX["stadium"]] == 14),
        ("浜名湖 (sta=6)", lambda r: r[IDX["stadium"]] == 6),
        ("若松 (sta=20)", lambda r: r[IDX["stadium"]] == 20),
        # 開催場 × 風速 (探索で nuki +50% だった subset)
        ("江戸川 × 風>=6",
         lambda r: r[IDX["stadium"]] == 3 and r[IDX["wind_sp"]] is not None and r[IDX["wind_sp"]] >= 6),
        ("江戸川 × 風>=3",
         lambda r: r[IDX["stadium"]] == 3 and r[IDX["wind_sp"]] is not None and r[IDX["wind_sp"]] >= 3),
        ("鳴門 × 風3-6",
         lambda r: r[IDX["stadium"]] == 14 and r[IDX["wind_sp"]] is not None
                   and 3 <= r[IDX["wind_sp"]] < 6),
        ("浜名湖 × 風3-6",
         lambda r: r[IDX["stadium"]] == 6 and r[IDX["wind_sp"]] is not None
                   and 3 <= r[IDX["wind_sp"]] < 6),
        ("若松 × 風3-6",
         lambda r: r[IDX["stadium"]] == 20 and r[IDX["wind_sp"]] is not None
                   and 3 <= r[IDX["wind_sp"]] < 6),
        # 開催場 × 1号艇 mot 弱
        ("江戸川 × 1号艇 mot<35",
         lambda r: r[IDX["stadium"]] == 3 and r[IDX["r1_mot2"]] is not None
                   and r[IDX["r1_mot2"]] < 35),
        ("江戸川 × 1号艇 mot<40",
         lambda r: r[IDX["stadium"]] == 3 and r[IDX["r1_mot2"]] is not None
                   and r[IDX["r1_mot2"]] < 40),
        # 軽量級
        ("1号艇 体重<51", lambda r: r[IDX["r1_weight"]] is not None and r[IDX["r1_weight"]] < 51),
        # R7 (探索で +43% lift)
        ("R7", lambda r: r[IDX["race_number"]] == 7),
        ("R1", lambda r: r[IDX["race_number"]] == 1),
        # 江戸川 × R7
        ("江戸川 × R7",
         lambda r: r[IDX["stadium"]] == 3 and r[IDX["race_number"]] == 7),
        # 江戸川 × R12 (江戸川 R12 はよく荒れる仮説)
        ("江戸川 × R12",
         lambda r: r[IDX["stadium"]] == 3 and r[IDX["race_number"]] == 12),
        # 1号艇 当地1着率 >=9 (探索で n=118 のみだが +112% lift)
        ("1号艇 local1>=9",
         lambda r: r[IDX["r1_local1"]] is not None and r[IDX["r1_local1"]] >= 9),
        # 江戸川 × 風>=6 (clean test on test period)
        ("江戸川 × 風速>=5",
         lambda r: r[IDX["stadium"]] == 3 and r[IDX["wind_sp"]] is not None
                   and r[IDX["wind_sp"]] >= 5),
    ]

    final_results = []
    print(f"  {'cond':<48} {'n':>5} {'nuki%':>6} {'+%':>5} {'h123%':>6} {'avg¥':>6} "
          f"{'ROI%':>6} | {'tr_n':>5} {'tr_nk%':>6} {'tr_ROI%':>7} | "
          f"{'te_n':>5} {'te_nk%':>6} {'te_ROI%':>7}")
    print("-" * 145)
    for label, pred in candidates:
        sub = filter_by(rows, pred)
        s = stats(sub, label)
        if s["n"] < 50:
            continue
        tr, te = split_train_test(sub)
        st = stats(tr)
        st2 = stats(te)
        nuki_lift_pct = (s["nuki_rate"] / base["nuki_rate"] - 1) * 100 if base["nuki_rate"] else 0
        # robust 判定:
        # all-period: nuki_rate >= base * 1.5 AND n >= 100
        # train/test 両方で nuki_rate >= base * 1.2 AND n >= 30
        is_robust_all = (s["nuki_rate"] >= base["nuki_rate"] * 1.5) and s["n"] >= 100
        is_robust_split = (
            st["nuki_rate"] >= base["nuki_rate"] * 1.2 and st["n"] >= 30
            and st2["nuki_rate"] >= base["nuki_rate"] * 1.2 and st2["n"] >= 30
        )
        if is_robust_all:
            mark = "★★" if is_robust_split else "★"
        else:
            mark = ""
        final_results.append({
            "label": label, "all": s, "train": st, "test": st2,
            "nuki_lift_pct": nuki_lift_pct,
            "robust_all": is_robust_all, "robust_split": is_robust_split, "mark": mark,
        })
        print(f"  {label:<48} {s['n']:>5} {s['nuki_rate']:>5.2f}% "
              f"{nuki_lift_pct:>+4.0f}% {s['hit123_rate']:>5.2f}% "
              f"{s['avg_pay']:>5.0f} {s['roi']:>5.1f}% | "
              f"{st['n']:>5} {st['nuki_rate']:>5.2f}% {st['roi']:>6.1f}% | "
              f"{st2['n']:>5} {st2['nuki_rate']:>5.2f}% {st2['roi']:>6.1f}% {mark}")

    # 最終 summary
    print()
    print("=" * 110)
    print("  最終 robust 候補 (★ = 全期間で nuki_rate +50% lift かつ n>=100;  ★★ = train/test 両方 +20% lift)")
    print("=" * 110)
    starred = [c for c in final_results if c["robust_all"]]
    if not starred:
        print("  → ★ 該当なし。「抜き hit 率 +50% lift」 条件は L4 universe では検出されず。")
    else:
        for c in starred:
            print(f"  {c['mark']} {c['label']}")
            print(f"    全期間: n={c['all']['n']}  nuki={c['all']['nuki_rate']:.2f}% "
                  f"(+{c['nuki_lift_pct']:.0f}%)  hit123={c['all']['hit123_rate']:.2f}%  "
                  f"avg¥={c['all']['avg_pay']:.0f}  ROI={c['all']['roi']:.2f}%")
            print(f"    train : n={c['train']['n']}  nuki={c['train']['nuki_rate']:.2f}%  "
                  f"ROI={c['train']['roi']:.2f}%")
            print(f"    test  : n={c['test']['n']}  nuki={c['test']['nuki_rate']:.2f}%  "
                  f"ROI={c['test']['roi']:.2f}%")

    print()
    print("=" * 110)
    print("  Done.")
    print("=" * 110)


if __name__ == "__main__":
    main()
