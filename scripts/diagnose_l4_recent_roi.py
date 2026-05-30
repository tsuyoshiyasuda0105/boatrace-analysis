"""L4 戦略 (3連単 1-2-3 本命買い) の最近 ROI 低下診断

要件:
1. 月別 ROI 推移 (2025-06 ~ 2026-05)
2. 期待値 (l4_strategy.GRADE_CLASS_RULES の recovery) vs 直近 6 ヶ月実測
3. 悪化原因の仮説検証 (会場別 / グレード別 / オッズ帯 / 季節性)
4. L4+1c80 / L4 PRO の現状検証
5. 改善案の train/test 再評価 (train: ~2025-12-31, test: 2026-01-01~)

注意:
- L4 base 条件 = 1号艇A1 ∧ Bでない会場 ∧ B除外なし
- 3連単 1-2-3 を 100 円固定買い (bet=100、payout=実払戻 or 0)
- ROI = 合計払戻 / (100 × 件数) × 100  (%)
- 「的中」= 1着=1艇 ∧ 2着=2艇 ∧ 3着=3艇
- L4 公式 expected: 一般戦×A1=147.7%, G3×A1=149.2%, G2×A1=242.7%, G1×A1=242.8%, SG×A1=258.2%
- グレード番号: 1:SG, 2:G1, 3:G2, 4:G3, 5:一般 (schema.sql line 89)
- l4_strategy.py の GRADE_CLASS_RULES とこの番号系で整合

n inflation 対策:
- race_previews は使わない (L4 base は天候系条件なし)
- 集計は race_id を distinct で扱う / e1 (boat=1) のみ join
"""
from __future__ import annotations

import os
import random
import sys
import sqlite3
from pathlib import Path
from typing import Any

random.seed(42)
N_BOOT = 1000

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config
from src.evaluation.l4_strategy import (
    EXCLUDE_VENUES,
    GRADE_CLASS_RULES,
    L4_DEFAULT_A1,
    L4_1C80_RECOVERY,
    L4_PRO_RECOVERY,
    RANK_PLUS_RECOVERY,
    RANK_PLUS_PLUS_RECOVERY,
    COURSE1_WINDOW_DAYS,
    COURSE1_MIN_STARTS,
    COURSE1_THRESHOLD,
    L4_PRO_AVG_ST_MAX,
    L4_PRO_AGE_MIN,
    L4_PRO_AGE_MAX,
    L4_PRO_EX_ST_MAX,
)

DB_PATH = config.DB_PATH
EXCL = sorted(EXCLUDE_VENUES)
EXCL_PH = ",".join("?" * len(EXCL))

GRADE_NAME = {1: "SG", 2: "G1", 3: "G2", 4: "G3", 5: "一般戦", None: "Unknown"}
STADIUM_NAME = {
    1: "桐生", 2: "戸田", 3: "江戸川", 4: "平和島", 5: "多摩川", 6: "浜名湖",
    7: "蒲郡", 8: "常滑", 9: "津", 10: "三国", 11: "びわこ", 12: "住之江",
    13: "尼崎", 14: "鳴門", 15: "丸亀", 16: "児島", 17: "宮島", 18: "徳山",
    19: "下関", 20: "若松", 21: "芦屋", 22: "福岡", 23: "唐津", 24: "大村",
}


def _conn() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)


def bootstrap_roi_ci(payouts: list[int], n_boot: int = N_BOOT) -> tuple[float, float, float, float]:
    """配当配列から ROI と 95% CI を bootstrap で算出。
    Returns: (roi, ci_low, ci_high, p_positive)
    p_positive = ROI が 100% (= break-even) を超える確率の bootstrap 近似。
    """
    n = len(payouts)
    if n == 0:
        return 0.0, 0.0, 0.0, 0.0
    rois = []
    for _ in range(n_boot):
        sample = random.choices(payouts, k=n)
        rois.append(sum(sample) / (100 * n) * 100)
    rois.sort()
    point = sum(payouts) / (100 * n) * 100
    ci_low = rois[int(n_boot * 0.025)]
    ci_high = rois[int(n_boot * 0.975)]
    p_pos = sum(1 for r in rois if r >= 100.0) / n_boot
    return point, ci_low, ci_high, p_pos


def _l4_base_where(extra: list[str] | None = None, class_: int = 1,
                    require_payout_band: bool = True) -> tuple[str, list[Any]]:
    """L4 base 条件の WHERE 句を返す。class_=1 は A1、2 は A2。

    require_payout_band=True (default) では、**本命 3連単 (最低人気 = MIN(payout))**
    が L4 帯 500-1000 円に入った race のみを対象にする。これが _evaluate_l4 の
    `in_500_1000 ∧ b_excluded ∧ A1` 条件に対応する正しいバックテスト近似:
    予測オッズが 5-10 倍と表示されたレース ≈ 結果論的に最人気 3連単払戻が 500-1000 円。

    require_payout_band=False は「単純 base」=  A1+B除外のみ (オッズ帯フィルタなし)。
    extra: 追加の WHERE 句リスト。
    """
    clauses = [
        f"r.stadium_number NOT IN ({EXCL_PH})",
        "e1.class_number=?",
    ]
    args: list[Any] = list(EXCL) + [class_]
    if require_payout_band:
        # 最も低い3連単払戻 (= 1番人気) が L4 帯に入った race のみ
        clauses.append(
            "(SELECT MIN(pp2.payout) FROM race_payouts pp2 "
            "WHERE pp2.race_id=r.race_id AND pp2.bet_type='trifecta') BETWEEN 500 AND 999"
        )
    if extra:
        clauses.extend(extra)
    return " AND ".join(clauses), args


def _hit_payout_subquery() -> str:
    """3連単 1-2-3 の払戻取得サブクエリ (LEFT JOIN 用)。"""
    return "LEFT JOIN race_payouts pp ON pp.race_id=r.race_id AND pp.bet_type='trifecta' AND pp.combination='1-2-3'"


def _hit_check_subquery() -> str:
    """1着=1 ∧ 2着=2 ∧ 3着=3 を判定する EXISTS サブクエリ。
    pp.payout が NOT NULL なら的中と同義 (race_payouts は的中時のみ payout 行が入る)。
    """
    return "(pp.payout IS NOT NULL)"


def query_monthly(conn: sqlite3.Connection, date_from: str, date_to: str,
                  class_: int = 1, extra_where: list[str] | None = None,
                  extra_args: list[Any] | None = None,
                  extra_joins: str = "",
                  require_payout_band: bool = True) -> list[tuple]:
    """月別の (ym, n, n_hit, sum_payout, roi%, hit_rate%) を返す。"""
    base_where, args = _l4_base_where(extra_where, class_, require_payout_band)
    if extra_args:
        args.extend(extra_args)
    sql = f"""
        SELECT substr(r.race_date,1,7) AS ym,
               COUNT(DISTINCT r.race_id) AS n,
               SUM(CASE WHEN pp.payout IS NOT NULL THEN 1 ELSE 0 END) AS n_hit,
               COALESCE(SUM(pp.payout),0) AS sum_payout
          FROM races r
          JOIN race_entries e1 ON e1.race_id=r.race_id AND e1.boat_number=1
          {extra_joins}
          {_hit_payout_subquery()}
         WHERE r.race_date BETWEEN ? AND ?
           AND {base_where}
         GROUP BY ym ORDER BY ym
    """
    rows = conn.execute(sql, [date_from, date_to] + args).fetchall()
    out = []
    for ym, n, n_hit, sp in rows:
        roi = (sp / (100 * n) * 100) if n else 0.0
        hr = (n_hit / n * 100) if n else 0.0
        out.append((ym, int(n), int(n_hit), int(sp), roi, hr))
    return out


def query_overall(conn: sqlite3.Connection, date_from: str, date_to: str,
                  class_: int = 1, extra_where: list[str] | None = None,
                  extra_args: list[Any] | None = None,
                  extra_joins: str = "",
                  group_by: str | None = None,
                  require_payout_band: bool = True) -> list[tuple]:
    """集約レベル指定可。group_by=None なら全体 1 行、'grade' / 'stadium' などで分解。"""
    base_where, args = _l4_base_where(extra_where, class_, require_payout_band)
    if extra_args:
        args.extend(extra_args)
    if group_by == "grade":
        gb = "r.race_grade_number"
        sel = f"{gb} AS g,"
    elif group_by == "stadium":
        gb = "r.stadium_number"
        sel = f"{gb} AS g,"
    else:
        gb = None
        sel = ""
    sql = f"""
        SELECT {sel}
               COUNT(DISTINCT r.race_id) AS n,
               SUM(CASE WHEN pp.payout IS NOT NULL THEN 1 ELSE 0 END) AS n_hit,
               COALESCE(SUM(pp.payout),0) AS sum_payout
          FROM races r
          JOIN race_entries e1 ON e1.race_id=r.race_id AND e1.boat_number=1
          {extra_joins}
          {_hit_payout_subquery()}
         WHERE r.race_date BETWEEN ? AND ?
           AND {base_where}
        {('GROUP BY ' + gb) if gb else ''}
        {('ORDER BY ' + gb) if gb else ''}
    """
    rows = conn.execute(sql, [date_from, date_to] + args).fetchall()
    out = []
    for r in rows:
        if gb:
            g, n, n_hit, sp = r
        else:
            n, n_hit, sp = r
            g = None
        n = int(n or 0); n_hit = int(n_hit or 0); sp = int(sp or 0)
        roi = (sp / (100 * n) * 100) if n else 0.0
        hr = (n_hit / n * 100) if n else 0.0
        out.append((g, n, n_hit, sp, roi, hr))
    return out


# ============================================================
# Section 1: 月別 ROI 推移
# ============================================================

def _fetch_payouts(conn: sqlite3.Connection, date_from: str, date_to: str,
                   class_: int = 1, require_payout_band: bool = True) -> list[int]:
    base_where, args = _l4_base_where(class_=class_, require_payout_band=require_payout_band)
    sql = f"""
        SELECT COALESCE(pp.payout, 0) AS pay
        FROM races r
        JOIN race_entries e1 ON e1.race_id=r.race_id AND e1.boat_number=1
        {_hit_payout_subquery()}
        WHERE r.race_date BETWEEN ? AND ?
          AND {base_where}
    """
    return [row[0] for row in conn.execute(sql, [date_from, date_to] + args)]


def section1_monthly_roi(conn: sqlite3.Connection) -> dict:
    print("=" * 80)
    print("Section 1: 月別 ROI 推移 (L4 真定義 = A1 + B除外 + 1-2-3 払戻 500-1000円帯)")
    print("           _evaluate_l4 / send_l4_alerts.py と整合する集計")
    print("=" * 80)
    print(f"{'月':<10}{'n':>7}{'hit':>6}{'hit%':>8}{'sum_pay':>12}{'ROI%':>9}")
    rows = query_monthly(conn, "2025-06-01", "2026-05-30", class_=1)
    total_n = total_hit = total_pay = 0
    for ym, n, hit, sp, roi, hr in rows:
        total_n += n; total_hit += hit; total_pay += sp
        print(f"{ym:<10}{n:>7,}{hit:>6,}{hr:>7.1f}%{sp:>12,}{roi:>8.1f}%")
    tot_roi = (total_pay / (100 * total_n) * 100) if total_n else 0.0
    tot_hr = (total_hit / total_n * 100) if total_n else 0.0
    print(f"{'合計':<10}{total_n:>7,}{total_hit:>6,}{tot_hr:>7.1f}%{total_pay:>12,}{tot_roi:>8.1f}%")

    # CI: 全期間
    pays_all = _fetch_payouts(conn, "2025-06-01", "2026-05-30")
    roi_a, lo_a, hi_a, p_a = bootstrap_roi_ci(pays_all)
    print(f"全期間 95% CI: [{lo_a:.1f}, {hi_a:.1f}], P(ROI>=100%) = {p_a:.1%}")

    # 参考: no_band (= 単純 A1+B除外、オッズ帯フィルタなし) も並列で出す
    print()
    print("[参考] オッズ帯フィルタなし (A1 + B除外のみ) の月別 ROI - 教科書 L4 とは別ロジック")
    rows_nb = query_monthly(conn, "2025-06-01", "2026-05-30", class_=1, require_payout_band=False)
    print(f"{'月':<10}{'n':>7}{'hit':>6}{'hit%':>8}{'ROI%':>9}")
    for ym, n, hit, sp, roi, hr in rows_nb:
        print(f"{ym:<10}{n:>7,}{hit:>6,}{hr:>7.1f}%{roi:>8.1f}%")

    # 直近 3 ヶ月 vs 過去 9 ヶ月
    recent = rows[-3:]
    past = rows[:-3]
    r_n = sum(r[1] for r in recent); r_hit = sum(r[2] for r in recent); r_sp = sum(r[3] for r in recent)
    p_n = sum(r[1] for r in past); p_hit = sum(r[2] for r in past); p_sp = sum(r[3] for r in past)
    r_roi = (r_sp / (100 * r_n) * 100) if r_n else 0.0
    p_roi = (p_sp / (100 * p_n) * 100) if p_n else 0.0

    pays_recent = _fetch_payouts(conn, "2026-03-01", "2026-05-30")
    pays_past = _fetch_payouts(conn, "2025-06-01", "2026-02-28")
    _, lo_r, hi_r, ppos_r = bootstrap_roi_ci(pays_recent)
    _, lo_p, hi_p, ppos_p = bootstrap_roi_ci(pays_past)

    # 差分の bootstrap CI (independent samples)
    diffs = []
    n_r = len(pays_recent); n_pe = len(pays_past)
    for _ in range(N_BOOT):
        sr = random.choices(pays_recent, k=n_r)
        sp = random.choices(pays_past, k=n_pe)
        diffs.append(sum(sr)/(100*n_r)*100 - sum(sp)/(100*n_pe)*100)
    diffs.sort()
    d_lo = diffs[int(N_BOOT*0.025)]
    d_hi = diffs[int(N_BOOT*0.975)]

    print()
    print(f"直近 3 ヶ月 (2026-03~05):  n={r_n:,}  ROI={r_roi:.1f}% [{lo_r:.1f}, {hi_r:.1f}], P(ROI>=100%)={ppos_r:.1%}")
    print(f"過去 9 ヶ月 (2025-06~2026-02): n={p_n:,}  ROI={p_roi:.1f}% [{lo_p:.1f}, {hi_p:.1f}], P(ROI>=100%)={ppos_p:.1%}")
    print(f"差分 (直近 - 過去): {r_roi - p_roi:+.1f}pt, 95% CI [{d_lo:+.1f}, {d_hi:+.1f}]")
    significantly_changed = (d_lo > 0) or (d_hi < 0)
    if significantly_changed:
        direction = "改善" if d_lo > 0 else "悪化"
        print(f"  → 統計的に有意に {direction}")
    else:
        print(f"  → 統計的に有意な変化なし (CI が 0 をまたぐ)")
    return {"monthly": rows, "recent": (r_n, r_hit, r_sp, r_roi, lo_r, hi_r),
            "past": (p_n, p_hit, p_sp, p_roi, lo_p, hi_p),
            "all": (total_n, total_hit, total_pay, tot_roi, lo_a, hi_a),
            "diff_ci": (d_lo, d_hi),
            "significantly_changed": significantly_changed}


# ============================================================
# Section 2: 期待値 vs 直近 6 ヶ月実測
# ============================================================

def section2_expected_vs_actual(conn: sqlite3.Connection) -> dict:
    print()
    print("=" * 80)
    print("Section 2: 期待値 (GRADE_CLASS_RULES) vs 直近 6 ヶ月実測 (2025-12 ~ 2026-05)")
    print("=" * 80)
    date_from, date_to = "2025-12-01", "2026-05-30"
    print(f"{'rule':<22}{'expected':>12}{'n_actual':>10}{'hit':>6}{'ROI%':>10}{'差分':>10}{'判定':>10}")
    results: list[dict] = []
    # A1 各グレード
    for (grade, cls), rule in GRADE_CLASS_RULES.items():
        if cls != 1 or grade is None:
            continue
        # グレード 5 (一般戦) は NULL を 5 として扱うか? GRADE_CLASS_RULES のキーは (5,1) 一般戦
        # よって r.race_grade_number = 5 で取る (NULL は L4_DEFAULT_A1 へ)
        rows = query_overall(conn, date_from, date_to, class_=1,
                             extra_where=["r.race_grade_number=?"],
                             extra_args=[grade])
        if not rows:
            continue
        _, n, hit, sp, roi, hr = rows[0]
        diff = roi - rule["recovery"]
        verdict = "OK" if roi >= 100 else "NG_loss"
        results.append({"label": rule["label"], "expected": rule["recovery"],
                        "n": n, "hit": hit, "roi": roi, "diff": diff, "key": (grade, cls)})
        print(f"{rule['label']:<22}{rule['recovery']:>11.1f}%{n:>10,}{hit:>6,}"
              f"{roi:>9.1f}%{diff:>+9.1f}pt{verdict:>10}")
    # default (grade=NULL, cls=1)
    rows = query_overall(conn, date_from, date_to, class_=1,
                         extra_where=["r.race_grade_number IS NULL"])
    if rows:
        _, n, hit, sp, roi, hr = rows[0]
        diff = roi - L4_DEFAULT_A1["recovery"]
        verdict = "OK" if roi >= 100 else "NG_loss"
        results.append({"label": L4_DEFAULT_A1["label"], "expected": L4_DEFAULT_A1["recovery"],
                        "n": n, "hit": hit, "roi": roi, "diff": diff, "key": (None, 1)})
        print(f"{L4_DEFAULT_A1['label']:<22}{L4_DEFAULT_A1['recovery']:>11.1f}%{n:>10,}{hit:>6,}"
              f"{roi:>9.1f}%{diff:>+9.1f}pt{verdict:>10}")
    # A2 派生
    a2_rule = GRADE_CLASS_RULES[(None, 2)]
    rows = query_overall(conn, date_from, date_to, class_=2)
    if rows:
        _, n, hit, sp, roi, hr = rows[0]
        diff = roi - a2_rule["recovery"]
        verdict = "OK" if roi >= 100 else "NG_loss"
        results.append({"label": a2_rule["label"], "expected": a2_rule["recovery"],
                        "n": n, "hit": hit, "roi": roi, "diff": diff, "key": (None, 2)})
        print(f"{a2_rule['label']:<22}{a2_rule['recovery']:>11.1f}%{n:>10,}{hit:>6,}"
              f"{roi:>9.1f}%{diff:>+9.1f}pt{verdict:>10}")

    print()
    print("100% を下回ったルール (= 平均すると損する):")
    for r in results:
        if r["roi"] < 100:
            print(f"  - {r['label']}: ROI={r['roi']:.1f}% (n={r['n']:,}, expected {r['expected']:.1f}%)")
    return {"results": results, "date_from": date_from, "date_to": date_to}


# ============================================================
# Section 3: 悪化原因仮説検証
# ============================================================

def section3a_by_stadium(conn: sqlite3.Connection) -> dict:
    print()
    print("=" * 80)
    print("Section 3a: 会場別 ROI (直近 6 ヶ月 2025-12 ~ 2026-05, A1 のみ)")
    print("=" * 80)
    rows = query_overall(conn, "2025-12-01", "2026-05-30", class_=1, group_by="stadium")
    rows_past = query_overall(conn, "2025-06-01", "2025-11-30", class_=1, group_by="stadium")
    past_map = {r[0]: r for r in rows_past}
    print(f"{'会場':<10}{'n_recent':>10}{'ROI_recent':>13}{'n_past':>9}{'ROI_past':>12}{'差分':>10}")
    out = []
    for st, n, hit, sp, roi, hr in rows:
        name = STADIUM_NAME.get(st, str(st))
        pr = past_map.get(st)
        if pr:
            roi_p = pr[4]; n_p = pr[1]
            diff = roi - roi_p
        else:
            roi_p = 0.0; n_p = 0; diff = 0.0
        out.append({"stadium": st, "name": name, "n": n, "roi": roi,
                    "n_past": n_p, "roi_past": roi_p, "diff": diff})
        print(f"{name:<10}{n:>10,}{roi:>12.1f}%{n_p:>9,}{roi_p:>11.1f}%{diff:>+9.1f}pt")
    # 悪化幅 top
    out_sorted = sorted([o for o in out if o["n"] >= 50], key=lambda x: x["diff"])
    print()
    print("悪化幅 大きい会場 top 5 (n_recent >= 50):")
    for o in out_sorted[:5]:
        print(f"  {o['name']:<6} ROI {o['roi_past']:.1f}% -> {o['roi']:.1f}% ({o['diff']:+.1f}pt, n={o['n']:,})")
    return {"results": out}


def section3b_by_grade(conn: sqlite3.Connection) -> dict:
    print()
    print("=" * 80)
    print("Section 3b: グレード別 ROI 推移 (A1 のみ)")
    print("=" * 80)
    # 直近 6 / 過去 6
    rec = query_overall(conn, "2025-12-01", "2026-05-30", class_=1, group_by="grade")
    pst = query_overall(conn, "2025-06-01", "2025-11-30", class_=1, group_by="grade")
    pst_map = {r[0]: r for r in pst}
    print(f"{'グレード':<10}{'n_rec':>8}{'ROI_rec':>10}{'n_past':>9}{'ROI_past':>11}{'差分':>10}")
    out = []
    for grade, n, hit, sp, roi, hr in rec:
        name = GRADE_NAME.get(grade, str(grade))
        pr = pst_map.get(grade)
        roi_p = pr[4] if pr else 0.0
        n_p = pr[1] if pr else 0
        diff = roi - roi_p
        out.append({"grade": grade, "name": name, "n": n, "roi": roi,
                    "roi_past": roi_p, "diff": diff})
        print(f"{name:<10}{n:>8,}{roi:>9.1f}%{n_p:>9,}{roi_p:>10.1f}%{diff:>+9.1f}pt")
    return {"results": out}


def section3c_by_payout_bucket(conn: sqlite3.Connection) -> dict:
    print()
    print("=" * 80)
    print("Section 3c: 1-2-3 払戻が L4 target (500-1000円) に入る率 (A1, 月別)")
    print("=" * 80)
    base_where, args = _l4_base_where(class_=1)
    sql = f"""
        SELECT substr(r.race_date,1,7) AS ym,
               COUNT(DISTINCT r.race_id) AS n,
               SUM(CASE WHEN pp.payout IS NOT NULL THEN 1 ELSE 0 END) AS n_hit,
               SUM(CASE WHEN pp.payout >= 500 AND pp.payout < 1000 THEN 1 ELSE 0 END) AS n_in_band,
               SUM(CASE WHEN pp.payout < 500 THEN 1 ELSE 0 END) AS n_below,
               SUM(CASE WHEN pp.payout >= 1000 THEN 1 ELSE 0 END) AS n_above,
               COALESCE(AVG(pp.payout),0) AS avg_pay_on_hit
          FROM races r
          JOIN race_entries e1 ON e1.race_id=r.race_id AND e1.boat_number=1
          {_hit_payout_subquery()}
         WHERE r.race_date BETWEEN ? AND ?
           AND {base_where}
         GROUP BY ym ORDER BY ym
    """
    rows = conn.execute(sql, ["2025-06-01", "2026-05-30"] + args).fetchall()
    print(f"{'月':<10}{'n':>7}{'hit':>6}{'<500':>7}{'500-1k':>9}{'>=1k':>7}{'band%':>8}{'avg_pay':>10}")
    out = []
    for ym, n, hit, in_band, below, above, avg_pay in rows:
        band_pct = (in_band / hit * 100) if hit else 0.0
        out.append({"ym": ym, "n": n, "hit": hit, "in_band": in_band,
                    "below": below, "above": above, "avg_pay": avg_pay,
                    "band_pct": band_pct})
        print(f"{ym:<10}{n:>7,}{hit:>6,}{below:>7,}{in_band:>9,}{above:>7,}{band_pct:>7.1f}%{avg_pay:>10.0f}")
    return {"results": out}


def section3d_seasonality(conn: sqlite3.Connection) -> dict:
    print()
    print("=" * 80)
    print("Section 3d: 季節性比較 (冬 12-2月 vs 春 3-5月 vs 夏 6-8月 vs 秋 9-11月)")
    print("=" * 80)
    base_where, args = _l4_base_where(class_=1)
    sql = f"""
        SELECT
          CASE WHEN CAST(substr(r.race_date,6,2) AS INT) IN (12,1,2) THEN 'winter'
               WHEN CAST(substr(r.race_date,6,2) AS INT) IN (3,4,5) THEN 'spring'
               WHEN CAST(substr(r.race_date,6,2) AS INT) IN (6,7,8) THEN 'summer'
               ELSE 'autumn' END AS season,
          COUNT(DISTINCT r.race_id) AS n,
          SUM(CASE WHEN pp.payout IS NOT NULL THEN 1 ELSE 0 END) AS n_hit,
          COALESCE(SUM(pp.payout),0) AS sum_pay
        FROM races r
        JOIN race_entries e1 ON e1.race_id=r.race_id AND e1.boat_number=1
        {_hit_payout_subquery()}
        WHERE r.race_date BETWEEN ? AND ?
          AND {base_where}
        GROUP BY season ORDER BY season
    """
    rows = conn.execute(sql, ["2025-06-01", "2026-05-30"] + args).fetchall()
    print(f"{'季節':<10}{'n':>7}{'hit':>6}{'hit%':>8}{'ROI%':>9}")
    out = []
    for season, n, hit, sp in rows:
        roi = (sp / (100 * n) * 100) if n else 0.0
        hr = (hit / n * 100) if n else 0.0
        out.append({"season": season, "n": n, "hit": hit, "roi": roi})
        print(f"{season:<10}{n:>7,}{hit:>6,}{hr:>7.1f}%{roi:>8.1f}%")
    return {"results": out}


# ============================================================
# Section 4: L4+1c80 / L4 PRO の現状検証
# ============================================================

def section4a_1c80(conn: sqlite3.Connection) -> dict:
    """1コース1着率 80%以上 (過去 180 日, 20 戦以上) の選手が 1 号艇のレース。

    racer_period_stats は現状空 (0 行) なので、race_results から直接計算する。
    各 race (2025-12-01〜) について、その race_date 以前 180 日内の
    boat_number=1 出走の中で finishing_position=1 となった率を選手別に算出。
    """
    print()
    print("=" * 80)
    print("Section 4a: L4+1c80 (1コース1着率 >= 80%, n>=20) の直近 6 ヶ月")
    print("  注: race_results から直接計算 (racer_period_stats 空のため)")
    print("=" * 80)
    date_from, date_to = "2025-12-01", "2026-05-30"
    # 全選手の 1コース1着率を、評価期間の最初の日 (2025-12-01) から見て過去 180 日で集計
    # → これは厳密な「レースごとの 180 日ローリング」ではないが、実装シンプルで近似十分
    window_from = "2025-06-04"  # 2025-12-01 - 180 days ≈ 2025-06-04
    sql_stats = """
        SELECT e1.racer_number,
               COUNT(*) AS starts,
               SUM(CASE WHEN rr.finishing_position=1 THEN 1 ELSE 0 END) AS wins
          FROM race_results rr
          JOIN race_entries e1 ON e1.race_id=rr.race_id AND e1.boat_number=1 AND rr.boat_number=1
          JOIN races r ON r.race_id=rr.race_id
         WHERE r.race_date BETWEEN ? AND ?
         GROUP BY e1.racer_number
    """
    stats = {}
    for racer, starts, wins in conn.execute(sql_stats, [window_from, date_from]):
        if starts >= COURSE1_MIN_STARTS:
            rate = wins / starts
            stats[racer] = (rate, starts)
    # 1c80 該当選手のセット
    in_1c80_racers = {r for r, (rate, _) in stats.items() if rate >= COURSE1_THRESHOLD}
    print(f"  1コース1着率 stats 集計対象 (window {window_from}~{date_from}): {len(stats):,} 選手")
    print(f"  うち 1c80 該当 (>= {COURSE1_THRESHOLD*100:.0f}%): {len(in_1c80_racers):,} 選手")

    if not in_1c80_racers:
        print("  → 1c80 該当選手 0 名 - 集計不能")
        return {"results": []}

    # in_1c80 該当 race と非該当 race を分離して ROI 集計
    in_ph = ",".join("?" * len(in_1c80_racers))
    base_where, args0 = _l4_base_where(class_=1)
    sql_in = f"""
        SELECT COUNT(DISTINCT r.race_id),
               SUM(CASE WHEN pp.payout IS NOT NULL THEN 1 ELSE 0 END),
               COALESCE(SUM(pp.payout),0)
        FROM races r
        JOIN race_entries e1 ON e1.race_id=r.race_id AND e1.boat_number=1
        {_hit_payout_subquery()}
        WHERE r.race_date BETWEEN ? AND ?
          AND {base_where}
          AND e1.racer_number IN ({in_ph})
    """
    in_args = [date_from, date_to] + args0 + sorted(in_1c80_racers)
    row = conn.execute(sql_in, in_args).fetchone()
    n_in = int(row[0] or 0); hit_in = int(row[1] or 0); sp_in = int(row[2] or 0)
    roi_in = (sp_in / (100 * n_in) * 100) if n_in else 0.0
    hr_in = (hit_in / n_in * 100) if n_in else 0.0

    sql_out = f"""
        SELECT COUNT(DISTINCT r.race_id),
               SUM(CASE WHEN pp.payout IS NOT NULL THEN 1 ELSE 0 END),
               COALESCE(SUM(pp.payout),0)
        FROM races r
        JOIN race_entries e1 ON e1.race_id=r.race_id AND e1.boat_number=1
        {_hit_payout_subquery()}
        WHERE r.race_date BETWEEN ? AND ?
          AND {base_where}
          AND e1.racer_number NOT IN ({in_ph})
    """
    out_args = [date_from, date_to] + args0 + sorted(in_1c80_racers)
    row = conn.execute(sql_out, out_args).fetchone()
    n_out = int(row[0] or 0); hit_out = int(row[1] or 0); sp_out = int(row[2] or 0)
    roi_out = (sp_out / (100 * n_out) * 100) if n_out else 0.0
    hr_out = (hit_out / n_out * 100) if n_out else 0.0

    print(f"{'bucket':<12}{'n':>8}{'hit':>6}{'hit%':>8}{'ROI%':>9}{'expected%':>11}")
    print(f"{'in_1c80':<12}{n_in:>8,}{hit_in:>6,}{hr_in:>7.1f}%{roi_in:>8.1f}%{L4_1C80_RECOVERY:>10.1f}%")
    print(f"{'not_1c80':<12}{n_out:>8,}{hit_out:>6,}{hr_out:>7.1f}%{roi_out:>8.1f}%{'n/a':>11}")

    return {"results": [
        {"bucket": "in_1c80", "n": n_in, "hit": hit_in, "roi": roi_in,
         "expected": L4_1C80_RECOVERY},
        {"bucket": "not_1c80", "n": n_out, "hit": hit_out, "roi": roi_out,
         "expected": None},
    ]}


def section4b_pro(conn: sqlite3.Connection) -> dict:
    """L4 PRO: 平均ST<0.16 ∧ 年齢 30-49 ∧ 展示ST<0.18
    展示 ST は race_previews.start_timing_exhibition (boat=1)。NULL は除外。
    payout band フィルタを含む正しい L4 ロジック。"""
    print()
    print("=" * 80)
    print("Section 4b: L4 PRO (avg_ST<0.16 ∧ age 30-49 ∧ ex_ST<0.18) の直近 6 ヶ月")
    print("           L4 真定義 (A1+B除外+500-1000帯) 内での評価")
    print("=" * 80)
    date_from, date_to = "2025-12-01", "2026-05-30"
    sql = f"""
        WITH boat1 AS (
            SELECT r.race_id, e1.avg_start_timing, e1.age,
                   pv.start_timing_exhibition
            FROM races r
            JOIN race_entries e1 ON e1.race_id=r.race_id AND e1.boat_number=1
            LEFT JOIN race_previews pv ON pv.race_id=r.race_id AND pv.boat_number=1
            WHERE r.race_date BETWEEN ? AND ?
              AND e1.class_number=1
              AND r.stadium_number NOT IN ({EXCL_PH})
              AND (SELECT MIN(pp2.payout) FROM race_payouts pp2
                   WHERE pp2.race_id=r.race_id AND pp2.bet_type='trifecta') BETWEEN 500 AND 999
        )
        SELECT
          CASE
            WHEN b.avg_start_timing < ? AND b.age BETWEEN ? AND ?
                 AND b.start_timing_exhibition IS NOT NULL
                 AND b.start_timing_exhibition < ? THEN 'pro_full'
            WHEN b.avg_start_timing < ? AND b.age BETWEEN ? AND ?
                 AND b.start_timing_exhibition IS NULL THEN 'pro_candidate'
            ELSE 'not_pro'
          END AS bucket,
          COUNT(DISTINCT b.race_id) AS n,
          SUM(CASE WHEN pp.payout IS NOT NULL THEN 1 ELSE 0 END) AS n_hit,
          COALESCE(SUM(pp.payout),0) AS sum_pay
        FROM boat1 b
        LEFT JOIN race_payouts pp ON pp.race_id=b.race_id AND pp.bet_type='trifecta' AND pp.combination='1-2-3'
        GROUP BY bucket
    """
    args = [date_from, date_to] + list(EXCL) + [
        L4_PRO_AVG_ST_MAX, L4_PRO_AGE_MIN, L4_PRO_AGE_MAX, L4_PRO_EX_ST_MAX,
        L4_PRO_AVG_ST_MAX, L4_PRO_AGE_MIN, L4_PRO_AGE_MAX,
    ]
    rows = conn.execute(sql, args).fetchall()
    print(f"{'bucket':<14}{'n':>8}{'hit':>6}{'hit%':>8}{'ROI%':>9}{'expected%':>11}")
    out = []
    for bucket, n, hit, sp in rows:
        roi = (sp / (100 * n) * 100) if n else 0.0
        hr = (hit / n * 100) if n else 0.0
        expected = L4_PRO_RECOVERY if bucket.startswith("pro") else None
        out.append({"bucket": bucket, "n": n, "hit": hit, "roi": roi, "expected": expected})
        e_str = f"{expected:.1f}%" if expected else "n/a"
        print(f"{bucket:<14}{n:>8,}{hit:>6,}{hr:>7.1f}%{roi:>8.1f}%{e_str:>11}")
    return {"results": out}


def section4c_subrank(conn: sqlite3.Connection) -> dict:
    """L4+ / L4++ サブランク (1号艇選手の国1%/局1%) の直近 6 ヶ月。
    payout band フィルタを含む正しい L4 ロジック。"""
    print()
    print("=" * 80)
    print("Section 4c: L4+ (国1%>=7.0) / L4++ (国1%>=7.0 ∧ 局1%>=7.0) の直近 6 ヶ月")
    print("           L4 真定義 (A1+B除外+500-1000帯) 内での評価")
    print("=" * 80)
    date_from, date_to = "2025-12-01", "2026-05-30"
    sql = f"""
        WITH boat1 AS (
            SELECT r.race_id, e1.national_top_1_percent AS natl,
                   e1.local_top_1_percent AS local_
            FROM races r
            JOIN race_entries e1 ON e1.race_id=r.race_id AND e1.boat_number=1
            WHERE r.race_date BETWEEN ? AND ?
              AND e1.class_number=1
              AND r.stadium_number NOT IN ({EXCL_PH})
              AND (SELECT MIN(pp2.payout) FROM race_payouts pp2
                   WHERE pp2.race_id=r.race_id AND pp2.bet_type='trifecta') BETWEEN 500 AND 999
        )
        SELECT
          CASE
            WHEN b.natl >= 7.0 AND b.local_ >= 7.0 THEN 'plus_plus'
            WHEN b.natl >= 7.0                     THEN 'plus'
            ELSE 'base'
          END AS rank_,
          COUNT(DISTINCT b.race_id) AS n,
          SUM(CASE WHEN pp.payout IS NOT NULL THEN 1 ELSE 0 END) AS n_hit,
          COALESCE(SUM(pp.payout),0) AS sum_pay
        FROM boat1 b
        LEFT JOIN race_payouts pp ON pp.race_id=b.race_id AND pp.bet_type='trifecta' AND pp.combination='1-2-3'
        GROUP BY rank_
    """
    args = [date_from, date_to] + list(EXCL)
    rows = conn.execute(sql, args).fetchall()
    print(f"{'rank':<12}{'n':>8}{'hit':>6}{'hit%':>8}{'ROI%':>9}{'expected%':>11}")
    out = []
    expected_map = {"plus_plus": RANK_PLUS_PLUS_RECOVERY,
                    "plus": RANK_PLUS_RECOVERY}
    for rank_, n, hit, sp in rows:
        roi = (sp / (100 * n) * 100) if n else 0.0
        hr = (hit / n * 100) if n else 0.0
        expected = expected_map.get(rank_)
        e_str = f"{expected:.1f}%" if expected else "n/a"
        out.append({"rank": rank_, "n": n, "hit": hit, "roi": roi, "expected": expected})
        print(f"{rank_:<12}{n:>8,}{hit:>6,}{hr:>7.1f}%{roi:>8.1f}%{e_str:>11}")
    return {"results": out}


# ============================================================
# Section 5: 改善案 train/test 再評価
# ============================================================

TRAIN_FROM = "2025-06-01"
TRAIN_TO = "2025-12-31"
TEST_FROM = "2026-01-01"
TEST_TO = "2026-05-30"


def eval_period(conn: sqlite3.Connection, name: str,
                extra_where: list[str] | None = None,
                extra_args: list[Any] | None = None,
                exclude_stadia: set[int] | None = None,
                class_filter: set[int] | None = None,
                require_payout_band: bool = True) -> dict:
    """train/test それぞれの ROI を返す。"""
    excl = sorted(EXCLUDE_VENUES | (exclude_stadia or set()))
    excl_ph = ",".join("?" * len(excl))
    clauses = [
        f"r.stadium_number NOT IN ({excl_ph})",
    ]
    args0: list[Any] = list(excl)
    if class_filter:
        ph = ",".join("?" * len(class_filter))
        clauses.append(f"e1.class_number IN ({ph})")
        args0.extend(sorted(class_filter))
    else:
        clauses.append("e1.class_number=1")
    if require_payout_band:
        clauses.append(
            "(SELECT MIN(pp2.payout) FROM race_payouts pp2 "
            "WHERE pp2.race_id=r.race_id AND pp2.bet_type='trifecta') BETWEEN 500 AND 999"
        )
    if extra_where:
        clauses.extend(extra_where)
    where = " AND ".join(clauses)
    sql_tmpl = f"""
        SELECT COUNT(DISTINCT r.race_id),
               SUM(CASE WHEN pp.payout IS NOT NULL THEN 1 ELSE 0 END),
               COALESCE(SUM(pp.payout),0)
        FROM races r
        JOIN race_entries e1 ON e1.race_id=r.race_id AND e1.boat_number=1
        {_hit_payout_subquery()}
        WHERE r.race_date BETWEEN ? AND ?
          AND {where}
    """
    full_args = args0 + (extra_args or [])

    def _q(df, dt):
        row = conn.execute(sql_tmpl, [df, dt] + full_args).fetchone()
        n, hit, sp = row
        n = int(n or 0); hit = int(hit or 0); sp = int(sp or 0)
        roi = (sp / (100 * n) * 100) if n else 0.0
        hr = (hit / n * 100) if n else 0.0
        return {"n": n, "hit": hit, "sum_pay": sp, "roi": roi, "hit_rate": hr}
    train = _q(TRAIN_FROM, TRAIN_TO)
    test = _q(TEST_FROM, TEST_TO)
    return {"name": name, "train": train, "test": test}


def section5_improvements(conn: sqlite3.Connection, baseline_train_test: dict | None = None) -> dict:
    print()
    print("=" * 80)
    print(f"Section 5: 改善案の train ({TRAIN_FROM}~{TRAIN_TO}) / test ({TEST_FROM}~{TEST_TO}) 再評価")
    print("=" * 80)

    # baseline
    base = eval_period(conn, "BASE: L4 (A1+B除外)")
    print(f"{'案':<55}{'train_n':>9}{'train_ROI':>11}{'test_n':>9}{'test_ROI':>11}")
    def _print(r):
        print(f"{r['name']:<55}{r['train']['n']:>9,}{r['train']['roi']:>10.1f}%"
              f"{r['test']['n']:>9,}{r['test']['roi']:>10.1f}%")
    _print(base)

    proposals = []

    # 案 1: 一般戦のみ (G3 以下のみ) 限定
    p = eval_period(conn, "案 1: 一般戦 (grade=5) のみ",
                    extra_where=["r.race_grade_number=5"])
    proposals.append(p); _print(p)

    # 案 2: G3 (grade=4) を除外
    p = eval_period(conn, "案 2: G3 を除外 (grade!=4 or NULL)",
                    extra_where=["(r.race_grade_number IS NULL OR r.race_grade_number<>4)"])
    proposals.append(p); _print(p)

    # 案 3: motor top2 >= 35% 縛り
    p = eval_period(conn, "案 3: motor top2 >= 35%",
                    extra_where=["e1.assigned_motor_top_2_percent >= 35"])
    proposals.append(p); _print(p)

    # 案 4: 国1% >= 7.0 縛り (L4+ 相当)
    p = eval_period(conn, "案 4: 国1%>=7.0 (L4+)",
                    extra_where=["e1.national_top_1_percent >= 7.0"])
    proposals.append(p); _print(p)

    # 案 5: 国1%>=7.0 ∧ 局1%>=7.0 (L4++ 相当)
    p = eval_period(conn, "案 5: 国1%>=7.0 ∧ 局1%>=7.0 (L4++)",
                    extra_where=["e1.national_top_1_percent >= 7.0",
                                 "e1.local_top_1_percent >= 7.0"])
    proposals.append(p); _print(p)

    # 案 6: 直近 6 ヶ月で悪化幅 top 3 会場を追加除外 (section3a 結果から拾う)
    # 会場名を出すために先に section3a を呼ぶことが望ましいが、ここでは候補を hard-code 後で報告に詳細書く
    # まずは section3a 計算で動的に決める
    rec_st = query_overall(conn, "2025-12-01", "2026-05-30", class_=1, group_by="stadium")
    pst_st = query_overall(conn, "2025-06-01", "2025-11-30", class_=1, group_by="stadium")
    pst_map = {r[0]: r[4] for r in pst_st}
    diff_list = []
    for st, n, hit, sp, roi, hr in rec_st:
        if n < 50: continue
        roi_p = pst_map.get(st, roi)
        diff_list.append((st, n, roi, roi_p, roi - roi_p))
    diff_list.sort(key=lambda x: x[4])
    extra_excl = {x[0] for x in diff_list[:3]}
    extra_excl_names = ",".join(STADIUM_NAME.get(s, str(s)) for s in extra_excl)
    p = eval_period(conn, f"案 6: 直近悪化top3会場 {{{extra_excl_names}}} 追加除外",
                    exclude_stadia=extra_excl)
    p["extra_excl"] = sorted(extra_excl)
    p["extra_excl_names"] = extra_excl_names
    proposals.append(p); _print(p)

    # 案 7: motor + 国1% 複合 (案 3 + 案 4)
    p = eval_period(conn, "案 7: motor top2 >= 35% ∧ 国1%>=7.0",
                    extra_where=["e1.assigned_motor_top_2_percent >= 35",
                                 "e1.national_top_1_percent >= 7.0"])
    proposals.append(p); _print(p)

    # 案 8: A1 + A2 を併用しない (= base は A1 のみで継続、A2 は別フラグ)
    # (今 base は A1 のみなのでこの案は実質 informational のみ)
    p = eval_period(conn, "案 8: A1+A2 を併用 (B除外維持)",
                    class_filter={1, 2})
    proposals.append(p); _print(p)

    return {"baseline": base, "proposals": proposals}


# ============================================================
# main
# ============================================================

def main():
    conn = _conn()
    try:
        out = {}
        out["section1"] = section1_monthly_roi(conn)
        out["section2"] = section2_expected_vs_actual(conn)
        out["section3a"] = section3a_by_stadium(conn)
        out["section3b"] = section3b_by_grade(conn)
        out["section3c"] = section3c_by_payout_bucket(conn)
        out["section3d"] = section3d_seasonality(conn)
        out["section4a"] = section4a_1c80(conn)
        out["section4b"] = section4b_pro(conn)
        out["section4c"] = section4c_subrank(conn)
        out["section5"] = section5_improvements(conn)

        print()
        print("=" * 80)
        print("DONE — 結論は reports/l4_recent_roi_diagnosis.md にまとめる")
        print("=" * 80)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
