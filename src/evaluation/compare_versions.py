"""
複数モデルバージョンを共通 val 期間で横断比較

各 version について:
  - PerWinner Top-1 ROI
  - Bootstrap CI
  - 戸田/芦屋等を切ったROI

usage:
    python -m src.evaluation.compare_versions \\
        --versions v0.2,v0.5 \\
        --date-from 2025-05-08 --date-to 2026-05-08 --split-ratio 0.85
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
from src.models.train import split_time_ratio
from src.evaluation.evaluate_with_payouts import load_artifact, predict_with_probs, load_payouts
from src.evaluation.bootstrap_ci import bootstrap_roi


LOSING_VENUES = {2: "戸田", 7: "蒲郡", 10: "三国", 21: "芦屋"}


def evaluate_version(version: str, df_val: pd.DataFrame, win_map: dict, n_iter: int = 1000) -> dict:
    """1 version の評価結果を dict で返す"""
    artifact = load_artifact(version)
    df_pred = predict_with_probs(artifact, df_val)
    idx = df_pred.groupby("race_id")["prob_first"].idxmax()
    top1 = df_pred.loc[idx, ["race_id", "stadium_number", "boat_number", "prob_first"]]
    top1 = top1.rename(columns={"boat_number": "top1_boat", "prob_first": "top1_prob"})

    rows = []
    for _, r in top1.iterrows():
        rid = r["race_id"]
        b = int(r["top1_boat"])
        ap = float(win_map.get((rid, str(b)), 0))
        rows.append({
            "race_id": rid,
            "stadium_number": int(r["stadium_number"]),
            "top1_prob": float(r["top1_prob"]),
            "actual_payout": ap,
            "hit": 1 if ap > 0 else 0,
        })
    df_r = pd.DataFrame(rows)

    # 戦略 A: 全レース
    a = bootstrap_roi(df_r, n_iter=n_iter)
    # 戦略 B: 4会場除外
    df_b = df_r[~df_r["stadium_number"].isin(LOSING_VENUES.keys())]
    b = bootstrap_roi(df_b, n_iter=n_iter) if len(df_b) > 0 else None
    # 戦略 D: 1号艇 70%+ × 4会場除外
    df_d = df_r[(df_r["top1_prob"] >= 0.70) & (~df_r["stadium_number"].isin(LOSING_VENUES.keys()))]
    d = bootstrap_roi(df_d, n_iter=n_iter) if len(df_d) > 0 else None

    return {
        "version": version,
        "top1_hit_rate": df_r["hit"].mean(),
        "n_total": len(df_r),
        "all_roi": a["roi_mean"], "all_ci": (a["roi_lo"], a["roi_hi"]),
        "no_losing_roi": b["roi_mean"] if b else None,
        "no_losing_ci": (b["roi_lo"], b["roi_hi"]) if b else None,
        "sweet_roi": d["roi_mean"] if d else None,
        "sweet_ci": (d["roi_lo"], d["roi_hi"]) if d else None,
        "sweet_n": d["n"] if d else 0,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--versions", required=True, help="v0.2,v0.5 等カンマ区切り")
    p.add_argument("--date-from", required=True)
    p.add_argument("--date-to", required=True)
    p.add_argument("--split-ratio", type=float, default=0.85)
    p.add_argument("--n-iter", type=int, default=1000)
    args = p.parse_args()

    versions = [v.strip() for v in args.versions.split(",")]

    print(f"[load val] {args.date_from} .. {args.date_to} (split={args.split_ratio})")
    df_all = build_training_frame(date_from=args.date_from, date_to=args.date_to)
    _, df_val = split_time_ratio(df_all, args.split_ratio)
    print(f"  val races: {df_val['race_id'].nunique():,}")

    df_from = date.fromisoformat(args.date_from)
    df_to = date.fromisoformat(args.date_to)
    win_p = load_payouts(config.DB_PATH, df_from, df_to, "win")
    win_map = dict(zip(zip(win_p["race_id"], win_p["combination"]), win_p["payout"]))

    results = []
    for v in versions:
        try:
            print(f"\n[eval] {v}")
            r = evaluate_version(v, df_val, win_map, n_iter=args.n_iter)
            results.append(r)
        except FileNotFoundError as e:
            print(f"  [SKIP] {e}")

    print("\n" + "=" * 76)
    print(" バージョン横断比較 (Bootstrap 95%CI)")
    print("=" * 76)
    print(f"{'version':<10}{'hit_rate':>10}{'all_ROI':>10}{'all_CI':>22}{'sweet_ROI':>12}{'sweet_CI':>22}{'sweet_n':>10}")
    for r in results:
        all_ci = f"[{r['all_ci'][0]:+.3f},{r['all_ci'][1]:+.3f}]"
        sweet = f"{r['sweet_roi']:+.4f}" if r['sweet_roi'] is not None else "—"
        sweet_ci = f"[{r['sweet_ci'][0]:+.3f},{r['sweet_ci'][1]:+.3f}]" if r['sweet_ci'] else "—"
        print(f"{r['version']:<10}{r['top1_hit_rate']:>10.4f}{r['all_roi']:>10.4f}{all_ci:>22}{sweet:>12}{sweet_ci:>22}{r['sweet_n']:>10}")


if __name__ == "__main__":
    main()
