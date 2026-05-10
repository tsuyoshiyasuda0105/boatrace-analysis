"""
Walk-Forward Backtest (簡易版)

固定の v0.6 ranker (4年学習済) を使い、時系列で月次評価。
モデル再学習はしない (重いので)、Sweet Spot 戦略の月次安定性を見る。
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd

import config
from src.features.builder import build_training_frame
from src.evaluation.evaluate_with_payouts import load_artifact, predict_with_probs, load_payouts


LOSING_VENUES = {2, 7, 10, 21}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--version", default="v0.6")
    p.add_argument("--start", default="2025-06-01")
    p.add_argument("--end", default="2026-05-09")
    p.add_argument("--window-days", type=int, default=30)
    args = p.parse_args()

    artifact = load_artifact(args.version)
    df_full = build_training_frame(date_from=args.start, date_to=args.end)

    win_p = load_payouts(config.DB_PATH, date.fromisoformat(args.start), date.fromisoformat(args.end), "win")
    win_map = dict(zip(zip(win_p["race_id"], win_p["combination"]), win_p["payout"]))

    print(f"=== Walk-Forward Sweet Spot 戦略 (v{args.version}, window={args.window_days}d) ===\n")

    df_full["race_date"] = pd.to_datetime(df_full["race_date"])
    cur = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    rows = []
    while cur <= end:
        nxt = cur + timedelta(days=args.window_days)
        sub = df_full[(df_full["race_date"] >= pd.Timestamp(cur)) & (df_full["race_date"] < pd.Timestamp(nxt))]
        if sub.empty:
            cur = nxt
            continue
        df_pred = predict_with_probs(artifact, sub)
        idx = df_pred.groupby("race_id")["prob_first"].idxmax()
        top1 = df_pred.loc[idx]
        # Sweet spot: 1号艇 70%+ × 4会場除外
        sweet = top1[
            (top1["boat_number"] == 1)
            & (top1["prob_first"] >= 0.70)
            & (~top1["stadium_number"].isin(LOSING_VENUES))
        ]
        if len(sweet) == 0:
            cur = nxt
            continue
        # ROI 計算
        hits = 0
        total_payout = 0.0
        for _, r in sweet.iterrows():
            rid = r["race_id"]
            ap = float(win_map.get((rid, "1"), 0))
            if ap > 0:
                hits += 1
                total_payout += ap
        n = len(sweet)
        roi = (total_payout - 100 * n) / (100 * n)
        rows.append({
            "window_start": cur.isoformat(),
            "window_end": (nxt - timedelta(days=1)).isoformat(),
            "n_sweet_bets": n,
            "n_hits": hits,
            "hit_rate": hits / n,
            "roi": roi,
        })
        cur = nxt

    df_r = pd.DataFrame(rows)
    print(df_r.to_string(index=False, float_format="%.4f"))

    print("\n=== 集計 ===")
    overall_n = df_r["n_sweet_bets"].sum()
    overall_payout_per_bet = ((df_r["roi"] + 1) * df_r["n_sweet_bets"]).sum() / overall_n
    overall_roi = overall_payout_per_bet - 1
    pos_windows = (df_r["roi"] > 0).sum()
    print(f"  total bets:       {overall_n:,}")
    print(f"  overall ROI:      {overall_roi:+.4f}")
    print(f"  windows with ROI>0: {pos_windows}/{len(df_r)} ({pos_windows/len(df_r)*100:.0f}%)")
    print(f"  monthly ROI std:  {df_r['roi'].std():.4f}")
    print(f"  monthly ROI mean: {df_r['roi'].mean():+.4f}")
    print(f"  worst month:      {df_r['roi'].min():+.4f}")
    print(f"  best month:       {df_r['roi'].max():+.4f}")


if __name__ == "__main__":
    main()
