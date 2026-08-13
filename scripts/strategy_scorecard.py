# -*- coding: utf-8 -*-
"""採用手法の月次スコアカード集計。

roi_race_history (フォワード清算台帳) を読み取り専用で集計し、
手法別のフォワード成績・ブートストラップCI・昇格/降格判定・
同一レース重複エントリー状況を Markdown レポートに出力する。

判定ルール (2026-08-13 リッキーさん合意):
  - 昇格候補: フォワード N >= 30 かつ ROI >= 130%
  - 降格候補: フォワード N >= 30 かつ ROI < 70%
  - それ以外: 検証中 (100円固定を継続)
  - 単月・単週の成績では動かさない (N 基準のみ)

使い方:
  .venv/Scripts/python.exe scripts/strategy_scorecard.py
  .venv/Scripts/python.exe scripts/strategy_scorecard.py --out reports/strategy_scorecard_YYYYMMDD.md
"""
from __future__ import annotations

import argparse
import random
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.db.connection import connect  # noqa: E402

PROMOTE_MIN_N = 30
PROMOTE_MIN_ROI = 130.0
DEMOTE_MIN_N = 30
DEMOTE_MAX_ROI = 70.0
BOOTSTRAP_ITER = 4000

# adopted_strategies.md / 採用時検証値 (参考表示用、フォワードとの縮み比較)
BACKTEST_ROI = {
    "tri134_acc2_ex3_tri": 296.5,
    "omura_132_weak2_ex3_tri": 264.0,
    "wakamatsu_13_weak2_strong3_exa": 250.4,
    "heiwajima_13_acc2_late_exa": 196.3,
    "tamagawa_13_weak_sashi2_exa": 191.9,
    "marugame_123_weak4_t5_tri": 226.0,
    "marugame_123_late_weak4_t5_tri": 242.1,
    "edogawa_132_weak4_t5_tri": 303.5,
    "karatsu_123_weak4_t5_tri": 242.2,
    "suminoe_124_weak3_t5_tri": 297.7,
    "nov_wall_break_31_41_exa": 236.1,
    "marugame_wall_hold_123_tri": 195.8,
    "miyajima_wall_break_31_41_exa": 182.2,
    "july_wall_hold_12_exa": 169.6,
    "shimonoseki_late_wall_hold_12_exa": 166.4,
    "hamanako_wall_hold_12_exa": 162.1,
    "miyajima_wall_hold_123_132_tri": 155.7,
    "g23_wall_hold_12_exa": 153.2,
    "tamagawa_late_wall_hold_123_132_tri": 150.2,
    "edogawa_a_accident4_12_exa": 284.7,
    "shimonoseki_a_accident4_13_exa": 201.2,
    "toda_a_accident2_13_exa": 299.4,
    "toda_dent2_makuri4_41": 159.7,
    "edogawa_late_dent2_makuri3_31": 151.0,
    "biwako_dent2_makuri3_31": 166.8,
    "amagasaki_dent3_makuri4_41": 168.8,
    "fukuoka_ex12_b_exa": 153.3,
    "fukuoka_tri124_c": 320.0,
    "fukuoka_123_late_foot_tri": 237.3,
    "a1_ace_motor_123_corr_tri": 145.7,
    "omura_124_original_t5_tri": 294.4,
    "kiryu_win4_ace_kimarite_late": 329.8,
    "amagasaki_win3_ace_kimarite_late": 422.3,
    "amagasaki_win3_ace_kimarite_m40": 397.9,
    "amagasaki_win3_ace_kimarite_no_rain": 401.6,
    "amagasaki_win3_ace_kimarite_late_no_rain": 371.4,
    "amagasaki_win3_ace_kimarite_all": 345.5,
    "naruto_win4_ace_kimarite_all": 484.9,
    "naruto_win4_ace_kimarite_no_rain": 434.2,
    "naruto_win3_ace_kimarite_late_no_rain": 285.6,
    "ashiya_win4_ace_kimarite_no_rain": 363.3,
    "shimonoseki_123_tri": 141.6,
    "suminoe_123_tri": 242.7,
}

# 同一レースに重複しやすい変種グループ (代表1本へ統合検討の対象)
VARIANT_GROUPS = {
    "尼崎win3系": [
        "amagasaki_win3_ace_kimarite_late",
        "amagasaki_win3_ace_kimarite_m40",
        "amagasaki_win3_ace_kimarite_no_rain",
        "amagasaki_win3_ace_kimarite_late_no_rain",
        "amagasaki_win3_ace_kimarite_all",
    ],
    "鳴門win4系": [
        "naruto_win4_ace_kimarite_all",
        "naruto_win4_ace_kimarite_no_rain",
    ],
    "びわこ4号艇単勝系": [
        "biwako_coursefit_boat4_gap10_general_win",
        "biwako_coursefit_boat4_gap5_general_win",
        "biwako_coursefit_boat4_rank1_general_win",
        "biwako_coursefit_boat4_gap10_all_win",
    ],
}


def bootstrap_ci(rows, n_iter=BOOTSTRAP_ITER, seed=42):
    """rows = [(stake, payout), ...] → (roi_lo, roi_hi, p_ge_100)"""
    if not rows:
        return (0.0, 0.0, 0.0)
    rng = random.Random(seed)
    rois = []
    for _ in range(n_iter):
        sample = [rng.choice(rows) for _ in rows]
        stake = sum(r[0] for r in sample)
        pay = sum(r[1] for r in sample)
        rois.append(100.0 * pay / stake if stake else 0.0)
    rois.sort()
    lo = rois[int(n_iter * 0.025)]
    hi = rois[int(n_iter * 0.975)]
    p = sum(1 for r in rois if r >= 100.0) / n_iter
    return (lo, hi, p)


def judge(n, roi):
    if n >= PROMOTE_MIN_N and roi >= PROMOTE_MIN_ROI:
        return "🔼 昇格候補"
    if n >= DEMOTE_MIN_N and roi < DEMOTE_MAX_ROI:
        return "🔽 降格候補"
    return "⏳ 検証中"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None, help="出力 Markdown パス")
    args = ap.parse_args()

    today = date.today().isoformat()
    out_path = Path(args.out) if args.out else (
        Path(__file__).resolve().parents[1]
        / "reports"
        / f"strategy_scorecard_{today.replace('-', '')}.md"
    )

    con = connect()
    cur = con.cursor()
    cur.execute(
        "SELECT strategy_key, race_id, race_date, stake_amount, payout_amount, is_hit "
        "FROM roi_race_history WHERE is_settled=1"
    )
    rows = cur.fetchall()
    con.close()

    per_strategy = defaultdict(list)
    race_strategies = defaultdict(set)
    dates = []
    for key, race_id, rdate, stake, payout, is_hit in rows:
        per_strategy[key].append((stake or 0, payout or 0, 1 if is_hit else 0))
        if race_id:
            race_strategies[race_id].add(key)
        if rdate:
            dates.append(rdate)

    total_stake = sum(s for v in per_strategy.values() for s, _, _ in v)
    total_pay = sum(p for v in per_strategy.values() for _, p, _ in v)
    total_hits = sum(h for v in per_strategy.values() for _, _, h in v)
    total_n = len(rows)
    total_roi = 100.0 * total_pay / total_stake if total_stake else 0.0
    all_bets = [(s, p) for v in per_strategy.values() for s, p, _ in v]
    lo, hi, p100 = bootstrap_ci(all_bets)

    lines = []
    lines.append(f"# 採用手法スコアカード ({today})")
    lines.append("")
    lines.append(f"- 集計対象: `roi_race_history` 清算済み {total_n} 件"
                 f" ({min(dates)} 〜 {max(dates)})" if dates else "- 集計対象: 0件")
    lines.append(f"- ポートフォリオ全体: 投入 {total_stake:,}円 / 回収 {total_pay:,}円 / "
                 f"収支 {total_pay - total_stake:+,}円 / ROI **{total_roi:.1f}%** / "
                 f"的中率 {100.0 * total_hits / total_n:.1f}%")
    lines.append(f"- Bootstrap 95%CI [{lo:.1f}%, {hi:.1f}%] / P(ROI>=100%) = {p100 * 100:.0f}%")
    lines.append("")
    lines.append(f"判定ルール: 昇格 = N>={PROMOTE_MIN_N} かつ ROI>={PROMOTE_MIN_ROI:.0f}% / "
                 f"降格 = N>={DEMOTE_MIN_N} かつ ROI<{DEMOTE_MAX_ROI:.0f}% / 他は検証中")
    lines.append("")
    lines.append("## 手法別フォワード成績")
    lines.append("")
    lines.append("| 手法 | N | 的中 | ROI | 収支 | 95%CI | 採用時ROI | 縮み | 判定 |")
    lines.append("|---|---:|---:|---:|---:|---|---:|---:|---|")

    ranked = sorted(
        per_strategy.items(),
        key=lambda kv: sum(p for _, p, _ in kv[1]) - sum(s for s, _, _ in kv[1]),
        reverse=True,
    )
    for key, bets in ranked:
        n = len(bets)
        stake = sum(s for s, _, _ in bets)
        pay = sum(p for _, p, _ in bets)
        hits = sum(h for _, _, h in bets)
        roi = 100.0 * pay / stake if stake else 0.0
        s_lo, s_hi, _ = bootstrap_ci([(s, p) for s, p, _ in bets])
        bt = BACKTEST_ROI.get(key)
        bt_s = f"{bt:.0f}%" if bt else "—"
        shrink = f"{roi / bt * 100:.0f}%" if bt and bt > 0 else "—"
        lines.append(
            f"| `{key}` | {n} | {hits} | {roi:.1f}% | {pay - stake:+,}円 | "
            f"[{s_lo:.0f}, {s_hi:.0f}] | {bt_s} | {shrink} | {judge(n, roi)} |"
        )

    dup_races = {rid: ks for rid, ks in race_strategies.items() if len(ks) > 1}
    lines.append("")
    lines.append("## 同一レース重複エントリー")
    lines.append("")
    lines.append(f"- 複数手法が同じレースに賭けたケース: **{len(dup_races)} レース**")
    for rid, ks in sorted(dup_races.items())[:20]:
        lines.append(f"  - `{rid}`: {', '.join(sorted(ks))}")
    lines.append("")
    lines.append("### 変種グループ合算 (統合検討用)")
    lines.append("")
    lines.append("| グループ | 本数 | N合計 | ROI | 収支 |")
    lines.append("|---|---:|---:|---:|---:|")
    for gname, keys in VARIANT_GROUPS.items():
        bets = [b for k in keys for b in per_strategy.get(k, [])]
        if not bets:
            continue
        stake = sum(s for s, _, _ in bets)
        pay = sum(p for _, p, _ in bets)
        roi = 100.0 * pay / stake if stake else 0.0
        lines.append(f"| {gname} | {len(keys)} | {len(bets)} | {roi:.1f}% | {pay - stake:+,}円 |")

    lines.append("")
    lines.append("## 運用メモ")
    lines.append("")
    lines.append("- 判定は N 基準のみ。単月・単週のマイナスでは手法を動かさない。")
    lines.append("- 変種グループは代表1本のみ実弾、他は記録のみとする統合を検討。")
    lines.append("- 新規採用は「バックテストROI 250%以上または N>=30」を満たさない場合、記録のみで開始。")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"レポート出力: {out_path}")
    print()
    print("\n".join(lines[:8]))


if __name__ == "__main__":
    main()
