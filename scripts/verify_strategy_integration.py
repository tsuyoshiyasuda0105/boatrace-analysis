"""kiryu_strategy + exhibition_bonus 統合検証 (自律ループ).

過去 DB に対し、 src/evaluation/kiryu_strategy.py と
src/evaluation/exhibition_bonus.py の判定ロジックを直接呼び出し、
想定 ROI を再現できているかを検証する。

検証対象:
  A. kiryu_strategy (K1 / K2 / K1_PRIME / K2_PRIME)
     - test 期間 (2026-01-01〜2026-05-29) の race-level ROI
  B. exhibition_bonus (補助点 0 / 1 / 2)
     - 全期間 L4 候補 race のうち補助点別 ROI

想定値 (ユーザー実測):
  K1: 277.1% (n=121), K2: 237.3% (n=117)
  K1_PRIME: 689.5% (n=57), K2_PRIME: 406.9% (n=52)
  L4 全体: 164.4% (n=7234)
  展示あり L4: 166.0% (n=1430)
  補助点 0: 146.9% (n=316)
  補助点 1: 171.4% (n=1114, =1 単独 ではなく ≥1 合算)
  補助点 2: 180.8% (n=388)

実行:
  py -3 scripts/verify_strategy_integration.py

出力:
  reports/verify_strategy_integration.md   — 各ラウンドの判定 + 最終ステータス
  reports/verify_strategy_integration.log  — raw 実行ログ
"""
from __future__ import annotations

import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.evaluation import kiryu_strategy as ks
from src.evaluation import exhibition_bonus as eb
from src.verification.backtest import _conn

REPORTS_DIR = Path(__file__).resolve().parents[1] / "reports"
REPORTS_DIR.mkdir(exist_ok=True)
MD_PATH = REPORTS_DIR / "verify_strategy_integration.md"
LOG_PATH = REPORTS_DIR / "verify_strategy_integration.log"

# 検証期間 (test split)
TEST_START_DATE = "2026-01-01"
TEST_END_DATE = "2026-05-29"

# 許容差分 (ユーザー指示)
KIRYU_TOLERANCE_PCT = 0.10   # ±10% (例: 277.1 → 250〜305)
EXHIBITION_TOLERANCE_PCT = 0.15  # ±15%
COUNT_TOLERANCE_PCT = 0.20   # ±20% (n)

EXPECTED_KIRYU = {
    "K1": {"roi": ks.K1_ROI_TEST_PCT, "n": ks.K1_N_TEST},
    "K2": {"roi": ks.K2_ROI_TEST_PCT, "n": ks.K2_N_TEST},
    "K1_PRIME": {"roi": ks.K1_PRIME_ROI_TEST_PCT, "n": ks.K1_PRIME_N_TEST},
    "K2_PRIME": {"roi": ks.K2_PRIME_ROI_TEST_PCT, "n": ks.K2_PRIME_N_TEST},
}

EXPECTED_EXHIBITION = {
    "L4_all": {"roi": eb.ROI_BASELINE_L4_PCT, "n": 7234},
    "L4_with_ex": {"roi": eb.ROI_WITH_EXHIBITION_PCT, "n": 1430},
    "score_0": {"roi": eb.ROI_BY_SCORE[0], "n": 316},
    "score_1": {"roi": eb.ROI_BY_SCORE[1], "n": 1114},  # score ≥ 1 (合算)
    "score_2": {"roi": eb.ROI_BY_SCORE[2], "n": 388},
}


# ============================================================
# 共通ユーティリティ
# ============================================================
def in_tolerance(actual: float, expected: float, tol: float) -> bool:
    """actual が expected の ±tol 範囲内なら True"""
    lo = expected * (1.0 - tol)
    hi = expected * (1.0 + tol)
    return lo <= actual <= hi


def fmt_pct(v: float) -> str:
    return f"{v:.1f}%"


# ============================================================
# A. kiryu_strategy 検証
# ============================================================
def fetch_kiryu_test_races(conn) -> list[dict]:
    """test 期間 (2026-01-01〜2026-05-29) の桐生 race 全件を取得.

    1号艇 (e1), 4号艇 (e4), 5号艇 (e5) のクラス・国1・motor、
    race_previews (pv, boat=1) の weather / wind を結合。
    """
    sql = f"""
SELECT
    r.race_id,
    r.race_date,
    r.race_number,
    r.stadium_number,
    e1.class_number AS boat1_class,
    e1.assigned_motor_top_2_percent AS boat1_motor_top_2,
    e1.national_top_1_percent AS boat1_natl_1,
    e4.class_number AS boat4_class,
    e5.assigned_motor_top_2_percent AS boat5_motor_top_2,
    pv.weather_number,
    pv.wind_direction_number
FROM races r
LEFT JOIN race_entries e1 ON e1.race_id = r.race_id AND e1.boat_number = 1
LEFT JOIN race_entries e4 ON e4.race_id = r.race_id AND e4.boat_number = 4
LEFT JOIN race_entries e5 ON e5.race_id = r.race_id AND e5.boat_number = 5
LEFT JOIN race_previews pv ON pv.race_id = r.race_id AND pv.boat_number = 1
WHERE r.stadium_number = 1
  AND r.race_date >= ? AND r.race_date <= ?
ORDER BY r.race_date, r.race_number
"""
    cur = conn.cursor()
    cur.execute(sql, (TEST_START_DATE, TEST_END_DATE))
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def fetch_kiryu_payouts(conn, race_ids: list[str], combos: list[str]) -> dict[tuple[str, str], int]:
    """指定 race_id 群の 3連単 払戻を batch 取得.

    Returns: {(race_id, combination): payout}
    """
    if not race_ids or not combos:
        return {}
    rid_ph = ",".join(["?"] * len(race_ids))
    combo_ph = ",".join(["?"] * len(combos))
    sql = f"""
SELECT race_id, combination, payout
FROM race_payouts
WHERE race_id IN ({rid_ph})
  AND bet_type = 'trifecta'
  AND combination IN ({combo_ph})
"""
    cur = conn.cursor()
    cur.execute(sql, [*race_ids, *combos])
    return {(rid, combo): int(payout) for rid, combo, payout in cur.fetchall() if payout}


# 戦略 → 買い目 combo
STRATEGY_COMBOS: dict[str, list[str]] = {
    "K1": ["5-1-2", "4-5-2"],
    "K2": ["5-1-2"],
    "K1_PRIME": ["4-5-2"],
    "K2_PRIME": ["5-1-2"],
}


def verify_kiryu(log_fn) -> dict:
    """A. kiryu_strategy 検証 ループ本体."""
    log_fn("=== A. kiryu_strategy 検証 ===")
    conn = _conn()
    try:
        races = fetch_kiryu_test_races(conn)
        log_fn(f"  桐生 test race fetch: {len(races)} races")

        # 全 race_id を払戻取得
        race_ids = [r["race_id"] for r in races]
        all_combos = list({c for combos in STRATEGY_COMBOS.values() for c in combos})
        payouts_map = fetch_kiryu_payouts(conn, race_ids, all_combos)
    finally:
        conn.close()

    # 戦略別集計
    agg: dict[str, dict] = {s: {"n_races": 0, "bets": 0, "hits": 0, "pay": 0} for s in STRATEGY_COMBOS}

    for row in races:
        legacy = ks.evaluate_kiryu_race(
            stadium_number=row["stadium_number"],
            boat1_class=row["boat1_class"],
            boat1_motor_top_2_percent=row["boat1_motor_top_2"],
            boat1_national_top_1_percent=row["boat1_natl_1"],
            weather_number=row["weather_number"],
            wind_direction_number=row["wind_direction_number"],
        )
        prime = ks.evaluate_kiryu_race_prime(
            stadium_number=row["stadium_number"],
            boat1_class=row["boat1_class"],
            boat1_motor_top_2_percent=row["boat1_motor_top_2"],
            boat1_national_top_1_percent=row["boat1_natl_1"],
            weather_number=row["weather_number"],
            wind_direction_number=row["wind_direction_number"],
            boat4_class=row["boat4_class"],
            boat5_motor_top_2_percent=row["boat5_motor_top_2"],
        )

        eligible_map = {
            "K1": legacy["k1_eligible"],
            "K2": legacy["k2_eligible"],
            "K1_PRIME": prime["k1_prime_eligible"],
            "K2_PRIME": prime["k2_prime_eligible"],
        }
        for strat, eligible in eligible_map.items():
            if not eligible:
                continue
            combos = STRATEGY_COMBOS[strat]
            n_combos = len(combos)
            bets = 100 * n_combos
            pay = sum(payouts_map.get((row["race_id"], c), 0) for c in combos)
            hits = sum(1 for c in combos if (row["race_id"], c) in payouts_map)
            agg[strat]["n_races"] += 1
            agg[strat]["bets"] += bets
            agg[strat]["hits"] += hits
            agg[strat]["pay"] += pay

    # 判定
    results = {}
    for strat, a in agg.items():
        exp = EXPECTED_KIRYU[strat]
        n = a["n_races"]
        roi = (a["pay"] / max(1, a["bets"])) * 100.0
        roi_ok = in_tolerance(roi, exp["roi"], KIRYU_TOLERANCE_PCT)
        n_ok = in_tolerance(n, exp["n"], COUNT_TOLERANCE_PCT)
        verdict = "PASS" if (roi_ok and n_ok) else "FAIL"
        log_fn(
            f"  {strat}: n={n} (exp {exp['n']}, n_ok={n_ok}), "
            f"ROI={fmt_pct(roi)} (exp {fmt_pct(exp['roi'])}, roi_ok={roi_ok}) "
            f"→ {verdict}"
        )
        results[strat] = {
            "n_races": n,
            "n_expected": exp["n"],
            "n_ok": n_ok,
            "bets": a["bets"],
            "hits": a["hits"],
            "pay": a["pay"],
            "roi_pct": roi,
            "roi_expected": exp["roi"],
            "roi_ok": roi_ok,
            "verdict": verdict,
        }
    overall = "PASS" if all(r["verdict"] == "PASS" for r in results.values()) else "FAIL"
    log_fn(f"  kiryu_strategy overall: {overall}")
    return {"results": results, "overall": overall}


# ============================================================
# B. exhibition_bonus 検証
# ============================================================
def fetch_l4_candidate_races(conn) -> list[dict]:
    """L4 候補 race の全件取得 (補助点検証用 universe).

    定義 (ユーザー実測の 7234 race と一致):
      - 1号艇 class=A1 (e1.class_number=1)
      - B 除外会場でない (stadium_number NOT IN {2,4,7,8,10,19,21,24})
      - 男性のみ (女性が 1 人も含まれない)
      - 雨除外 (weather_number != 3、 NULL OK)
      - 本命 (race 内 trifecta 最低払戻) が [500, 1000) 円帯

    各 race の 1号艇〜6号艇 の exhibition_time を結合。
    1 row / race に collapse する (boat=1 結合は MIN payout の HAVING で固定済)。
    """
    sql = """
WITH l4_cand AS (
    SELECT r.race_id, r.race_date,
           MIN(pp.payout) AS fav_payout
      FROM races r
      JOIN race_entries e1 ON e1.race_id=r.race_id AND e1.boat_number=1
      LEFT JOIN race_previews pv ON pv.race_id=r.race_id AND pv.boat_number=1
      JOIN race_payouts pp ON pp.race_id=r.race_id AND pp.bet_type='trifecta'
     WHERE e1.class_number = 1
       AND r.stadium_number NOT IN (2,4,7,8,10,19,21,24)
       AND (pv.weather_number IS NULL OR pv.weather_number != 3)
       AND NOT EXISTS (
           SELECT 1 FROM race_entries e2
             JOIN racers ra ON e2.racer_number = ra.racer_number
            WHERE e2.race_id = r.race_id AND ra.gender = 2
       )
     GROUP BY r.race_id
    HAVING fav_payout >= 500 AND fav_payout < 1000
)
SELECT race_id, race_date, fav_payout FROM l4_cand
"""
    cur = conn.cursor()
    cur.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def fetch_exhibition_times(conn, race_ids: list[str]) -> dict[str, list]:
    """race_id 群の boat1-6 exhibition_time を batch 取得.

    Returns: {race_id: [t1, t2, t3, t4, t5, t6]}  (None 含む)
    """
    if not race_ids:
        return {}
    ph = ",".join(["?"] * len(race_ids))
    sql = f"""
SELECT race_id, boat_number, exhibition_time
FROM race_previews
WHERE race_id IN ({ph})
  AND boat_number BETWEEN 1 AND 6
"""
    cur = conn.cursor()
    cur.execute(sql, race_ids)
    by_race: dict[str, list] = {rid: [None] * 6 for rid in race_ids}
    for rid, bn, et in cur.fetchall():
        if rid in by_race and bn is not None and 1 <= bn <= 6:
            by_race[rid][bn - 1] = et
    return by_race


def fetch_payouts_123(conn, race_ids: list[str]) -> dict[str, int]:
    """race_id 群の 3連単 1-2-3 払戻を batch 取得."""
    if not race_ids:
        return {}
    ph = ",".join(["?"] * len(race_ids))
    sql = f"""
SELECT race_id, payout
FROM race_payouts
WHERE race_id IN ({ph})
  AND bet_type = 'trifecta'
  AND combination = '1-2-3'
"""
    cur = conn.cursor()
    cur.execute(sql, race_ids)
    return {rid: int(p) for rid, p in cur.fetchall() if p}


def chunked(seq: list, size: int):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def verify_exhibition(log_fn) -> dict:
    """B. exhibition_bonus 検証 ループ本体."""
    log_fn("=== B. exhibition_bonus 検証 ===")
    conn = _conn()
    try:
        races = fetch_l4_candidate_races(conn)
        log_fn(f"  L4 候補 race: {len(races)} races")

        rids = [r["race_id"] for r in races]
        # chunk to avoid SQLite limit
        ex_map: dict[str, list] = {}
        pay_map: dict[str, int] = {}
        for chunk in chunked(rids, 500):
            ex_map.update(fetch_exhibition_times(conn, chunk))
            pay_map.update(fetch_payouts_123(conn, chunk))
    finally:
        conn.close()

    # 集計
    overall = {"n": 0, "hits": 0, "pay": 0}
    with_ex = {"n": 0, "hits": 0, "pay": 0}
    by_score: dict[int, dict] = {s: {"n": 0, "hits": 0, "pay": 0} for s in (0, 1, 2)}
    score_ge1 = {"n": 0, "hits": 0, "pay": 0}

    for r in races:
        rid = r["race_id"]
        overall["n"] += 1
        pay = pay_map.get(rid, 0)
        if pay:
            overall["hits"] += 1
            overall["pay"] += pay
        # 補助点
        all_times = ex_map.get(rid, [None] * 6)
        b1 = all_times[0]
        b2 = all_times[1]
        b3 = all_times[2]
        result = eb.evaluate_l4_with_bonus(
            boat1_ex_time=b1,
            boat2_ex_time=b2,
            boat3_ex_time=b3,
            all_ex_times=all_times,
        )
        if result["incomplete"]:
            continue
        # exhibition データあり
        with_ex["n"] += 1
        if pay:
            with_ex["hits"] += 1
            with_ex["pay"] += pay
        # score 別
        score = result["score"]
        by_score[score]["n"] += 1
        if pay:
            by_score[score]["hits"] += 1
            by_score[score]["pay"] += pay
        if score >= 1:
            score_ge1["n"] += 1
            if pay:
                score_ge1["hits"] += 1
                score_ge1["pay"] += pay

    def calc_roi(d: dict) -> float:
        return (d["pay"] / max(1, d["n"] * 100)) * 100.0  # 1 race = 100 円 bet

    summary = {
        "L4_all": {"n": overall["n"], "hits": overall["hits"], "pay": overall["pay"], "roi": calc_roi(overall)},
        "L4_with_ex": {"n": with_ex["n"], "hits": with_ex["hits"], "pay": with_ex["pay"], "roi": calc_roi(with_ex)},
        "score_0": {"n": by_score[0]["n"], "hits": by_score[0]["hits"], "pay": by_score[0]["pay"], "roi": calc_roi(by_score[0])},
        # score=1 行のセル値は ROI_BY_SCORE[1] = 171.4 (= 合算 ≥1)
        "score_1": {"n": score_ge1["n"], "hits": score_ge1["hits"], "pay": score_ge1["pay"], "roi": calc_roi(score_ge1)},
        "score_2": {"n": by_score[2]["n"], "hits": by_score[2]["hits"], "pay": by_score[2]["pay"], "roi": calc_roi(by_score[2])},
    }

    results = {}
    for key, exp in EXPECTED_EXHIBITION.items():
        s = summary[key]
        roi_ok = in_tolerance(s["roi"], exp["roi"], EXHIBITION_TOLERANCE_PCT)
        n_ok = in_tolerance(s["n"], exp["n"], COUNT_TOLERANCE_PCT)
        verdict = "PASS" if (roi_ok and n_ok) else "FAIL"
        log_fn(
            f"  {key}: n={s['n']} (exp {exp['n']}, n_ok={n_ok}), "
            f"ROI={fmt_pct(s['roi'])} (exp {fmt_pct(exp['roi'])}, roi_ok={roi_ok}) "
            f"→ {verdict}"
        )
        results[key] = {
            "n_races": s["n"],
            "n_expected": exp["n"],
            "n_ok": n_ok,
            "hits": s["hits"],
            "pay": s["pay"],
            "roi_pct": s["roi"],
            "roi_expected": exp["roi"],
            "roi_ok": roi_ok,
            "verdict": verdict,
        }
    overall_verdict = "PASS" if all(r["verdict"] == "PASS" for r in results.values()) else "FAIL"
    log_fn(f"  exhibition_bonus overall: {overall_verdict}")
    return {"results": results, "overall": overall_verdict}


# ============================================================
# メイン (自律ループ、最大 5 ラウンド)
# ============================================================
def main() -> None:
    log_lines: list[str] = []

    def log(msg: str) -> None:
        print(msg)
        log_lines.append(msg)

    round_results: list[dict] = []
    final_status = "❌ 5 ラウンド経過、 FAIL 残存"
    MAX_ROUNDS = 5

    for rnd in range(1, MAX_ROUNDS + 1):
        log(f"\n{'=' * 60}")
        log(f"ラウンド {rnd}")
        log(f"{'=' * 60}")

        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        log(f"開始: {ts}")

        kiryu_res = verify_kiryu(log)
        exh_res = verify_exhibition(log)

        round_results.append({
            "round": rnd,
            "kiryu": kiryu_res,
            "exhibition": exh_res,
        })

        both_pass = (kiryu_res["overall"] == "PASS" and exh_res["overall"] == "PASS")
        if both_pass:
            log(f"\n--- ラウンド {rnd}: 両方 PASS → ループ終了 ---")
            final_status = "🎉 全 PASS"
            break

        # 部分 PASS の場合
        partial = (kiryu_res["overall"] == "PASS" or exh_res["overall"] == "PASS")
        log(f"\n--- ラウンド {rnd}: 部分 PASS={partial} ---")

        # 5 ラウンド目で抜けるなら部分判定
        if rnd == MAX_ROUNDS:
            if partial:
                pass_side = "exhibition_bonus" if exh_res["overall"] == "PASS" else "kiryu_strategy"
                fail_side = "kiryu_strategy" if exh_res["overall"] == "PASS" else "exhibition_bonus"
                final_status = f"⚠ 部分 PASS ({fail_side} に乖離)"

    # === レポート出力 ===
    md_lines: list[str] = []
    md_lines.append("# kiryu_strategy + exhibition_bonus 統合検証レポート")
    md_lines.append("")
    md_lines.append(f"**生成日**: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    md_lines.append(f"**検証スプリット**: 桐生は test=[{TEST_START_DATE}, {TEST_END_DATE}], 展示補助点は全期間")
    md_lines.append(f"**許容差分**: kiryu ROI ±{KIRYU_TOLERANCE_PCT*100:.0f}% / exhibition ROI ±{EXHIBITION_TOLERANCE_PCT*100:.0f}% / n ±{COUNT_TOLERANCE_PCT*100:.0f}%")
    md_lines.append("")
    md_lines.append(f"## 最終ステータス: **{final_status}**")
    md_lines.append("")

    # 各ラウンドのテーブル
    for rr in round_results:
        rnd = rr["round"]
        kr = rr["kiryu"]
        er = rr["exhibition"]
        md_lines.append(f"## ラウンド {rnd}")
        md_lines.append("")
        md_lines.append("### A. kiryu_strategy")
        md_lines.append("")
        md_lines.append("| 戦略 | 実測 n | 期待 n | n判定 | 実測 ROI | 期待 ROI | ROI判定 | 総合 |")
        md_lines.append("|---|---:|---:|:---:|---:|---:|:---:|:---:|")
        for strat in ["K1", "K2", "K1_PRIME", "K2_PRIME"]:
            r = kr["results"][strat]
            md_lines.append(
                f"| {strat} | {r['n_races']} | {r['n_expected']} | "
                f"{'✅' if r['n_ok'] else '❌'} | "
                f"{fmt_pct(r['roi_pct'])} | {fmt_pct(r['roi_expected'])} | "
                f"{'✅' if r['roi_ok'] else '❌'} | "
                f"**{r['verdict']}** |"
            )
        md_lines.append(f"\n→ overall: **{kr['overall']}**")
        md_lines.append("")

        md_lines.append("### B. exhibition_bonus")
        md_lines.append("")
        md_lines.append("| ケース | 実測 n | 期待 n | n判定 | 実測 ROI | 期待 ROI | ROI判定 | 総合 |")
        md_lines.append("|---|---:|---:|:---:|---:|---:|:---:|:---:|")
        label_map = {
            "L4_all": "L4 全体 (展示なし含む)",
            "L4_with_ex": "展示データあり L4",
            "score_0": "補助点 =0",
            "score_1": "補助点 ≥1",
            "score_2": "補助点 =2",
        }
        for key in ["L4_all", "L4_with_ex", "score_0", "score_1", "score_2"]:
            r = er["results"][key]
            md_lines.append(
                f"| {label_map[key]} | {r['n_races']} | {r['n_expected']} | "
                f"{'✅' if r['n_ok'] else '❌'} | "
                f"{fmt_pct(r['roi_pct'])} | {fmt_pct(r['roi_expected'])} | "
                f"{'✅' if r['roi_ok'] else '❌'} | "
                f"**{r['verdict']}** |"
            )
        md_lines.append(f"\n→ overall: **{er['overall']}**")
        md_lines.append("")

    md_lines.append("## 注意事項")
    md_lines.append("")
    md_lines.append("- L4 候補 universe 定義 (ユーザー実測 n=7234 race と一致): ")
    md_lines.append("  - 1号艇 class=A1 + B 除外会場 (2,4,7,8,10,19,21,24) でない")
    md_lines.append("  - 男性のみ (女性 1 人も含まれない) + 雨除外 (weather_number != 3)")
    md_lines.append("  - **本命 (MIN trifecta payout) が [500, 1000) 円帯**")
    md_lines.append("- 展示タイム = race_previews.exhibition_time (周回展示タイム、 NOT start_timing_exhibition)")
    md_lines.append("- 桐生 K2/K2_PRIME は wd=NULL も「適格」扱い (検証コードと一致)")
    md_lines.append("- 1 race = 100 円 / combo を基準. K1 は 2 combo なので 1 race=200 円.")
    md_lines.append("")

    with open(MD_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))
    log(f"\nMarkdown report: {MD_PATH}")

    with open(LOG_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines))
    log(f"Log: {LOG_PATH}")

    log(f"\n最終ステータス: {final_status}")


if __name__ == "__main__":
    main()
