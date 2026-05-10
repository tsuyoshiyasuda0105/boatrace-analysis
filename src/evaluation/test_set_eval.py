"""
リーク無し Test set 一発評価

前提: --version で指定するモデルは test 期間より前のデータでのみ学習されていること
      (例: v0.6-test = 2022-04-01 .. 2026-03-31 学習 → 2026-04-01 以降を test)

split を一切行わず、指定期間そのものを評価対象にする (venue_exclusion との違い)。

usage:
    python -m src.evaluation.test_set_eval --version v0.6-test \\
        --date-from 2026-04-01 --date-to 2026-05-09 --n-iter 2000
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd

import config
from src.features.builder import build_training_frame
from src.evaluation.evaluate_with_payouts import load_artifact, predict_with_probs, load_payouts
from src.evaluation.bootstrap_ci import bootstrap_roi


EXCLUDE_VENUES = {2: "戸田", 21: "芦屋", 10: "三国", 7: "蒲郡"}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--version", required=True)
    p.add_argument("--date-from", required=True)
    p.add_argument("--date-to", required=True)
    p.add_argument("--n-iter", type=int, default=2000)
    args = p.parse_args()

    print(f"=== Test set 一発評価 (no split, leak-free) ===")
    print(f"  model:  {args.version}")
    print(f"  period: {args.date_from} .. {args.date_to}\n")

    artifact = load_artifact(args.version)
    df_test = build_training_frame(date_from=args.date_from, date_to=args.date_to)
    print(f"  test races: {df_test['race_id'].nunique():,}")

    df_pred = predict_with_probs(artifact, df_test)

    idx = df_pred.groupby("race_id")["prob_first"].idxmax()
    top1 = df_pred.loc[idx, ["race_id", "stadium_number", "boat_number", "prob_first"]]
    top1 = top1.rename(columns={"boat_number": "top1_boat", "prob_first": "top1_prob"})

    df_from = date.fromisoformat(args.date_from)
    df_to = date.fromisoformat(args.date_to)
    win_p = load_payouts(config.DB_PATH, df_from, df_to, "win")
    win_map = dict(zip(zip(win_p["race_id"], win_p["combination"]), win_p["payout"]))

    rows = []
    for _, r in top1.iterrows():
        rid = r["race_id"]
        b = int(r["top1_boat"])
        ap = float(win_map.get((rid, str(b)), 0))
        rows.append({
            "race_id": rid,
            "stadium_number": int(r["stadium_number"]),
            "top1_boat": b,
            "top1_prob": float(r["top1_prob"]),
            "actual_payout": ap,
            "hit": 1 if ap > 0 else 0,
        })
    df_r = pd.DataFrame(rows)

    print(f"\n=== 戦略別 Bootstrap CI 95% (n_iter={args.n_iter}) ===\n")

    strategies = []
    # A. 全レース買い
    strategies.append(("A. 全レース", df_r))
    # B. 4会場除外
    strategies.append(("B. 4会場除外", df_r[~df_r["stadium_number"].isin(EXCLUDE_VENUES.keys())]))
    # C. 1号艇 70%+ × 4会場除外 (Sweet Spot)
    strategies.append(("C. Sweet Spot (1号艇70%+ × 4会場除外)",
                       df_r[(df_r["top1_prob"] >= 0.70)
                            & (df_r["top1_boat"] == 1)
                            & (~df_r["stadium_number"].isin(EXCLUDE_VENUES.keys()))]))
    # D. 1号艇 65%+ × 4会場除外 (緩めSweet)
    strategies.append(("D. 1号艇65%+ × 4会場除外",
                       df_r[(df_r["top1_prob"] >= 0.65)
                            & (df_r["top1_boat"] == 1)
                            & (~df_r["stadium_number"].isin(EXCLUDE_VENUES.keys()))]))
    # E. 1号艇 75%+ × 4会場除外 (強めSweet)
    strategies.append(("E. 1号艇75%+ × 4会場除外",
                       df_r[(df_r["top1_prob"] >= 0.75)
                            & (df_r["top1_boat"] == 1)
                            & (~df_r["stadium_number"].isin(EXCLUDE_VENUES.keys()))]))

    print(f"{'strategy':<42}{'n':>6}{'hit':>8}{'ROI':>10}{'CI_lo':>9}{'CI_hi':>9}{'P>0':>7}")
    print("-" * 91)
    for label, sub in strategies:
        if len(sub) == 0:
            print(f"{label:<42}{0:>6}    n=0")
            continue
        ci = bootstrap_roi(sub, n_iter=args.n_iter)
        hit = sub["hit"].mean()
        print(f"{label:<42}{ci['n']:>6}{hit:>8.3f}{ci['roi_mean']:>+10.4f}"
              f"{ci['roi_lo']:>+9.3f}{ci['roi_hi']:>+9.3f}{ci['p_positive']:>7.2f}")

    # 月別の Sweet Spot を見る
    sweet = df_r[(df_r["top1_prob"] >= 0.70)
                 & (df_r["top1_boat"] == 1)
                 & (~df_r["stadium_number"].isin(EXCLUDE_VENUES.keys()))].copy()
    if len(sweet) > 0:
        sweet["yyyymm"] = sweet["race_id"].str.slice(0, 6)
        print(f"\n=== Sweet Spot 月別 ===")
        for ym, g in sweet.groupby("yyyymm"):
            n = len(g)
            hits = g["hit"].sum()
            payout = g["actual_payout"].sum()
            roi = (payout - 100 * n) / (100 * n)
            print(f"  {ym}: n={n:>4}  hit={hits/n:.3f}  ROI={roi:+.4f}")


if __name__ == "__main__":
    main()
