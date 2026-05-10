"""
Bootstrap 信頼区間 (CI) 計算

サブグループのROIが偶然なのか本物のシグナルか検証。
ブロック・リサンプリング (1000 iter) で 95% CI を出す。
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


def _stadium_name_map():
    import json
    with open(config.MASTER_DIR / "stadiums.json", encoding="utf-8") as f:
        data = json.load(f)
    return {int(k): v["name"] for k, v in data.items() if isinstance(v, dict) and "name" in v}


def bootstrap_roi(df: pd.DataFrame, n_iter: int = 1000, ci: float = 0.95,
                  seed: int = 42, bet_amount: float = 100.0) -> dict:
    """
    df 必須列: hit (0/1), actual_payout (円)
    ROI = (sum(hit*payout) - bet_amount*n) / (bet_amount*n)

    bootstrap で n_iter 回リサンプリングして CI を計算。
    """
    if df.empty:
        return {"n": 0, "roi_mean": 0, "roi_lo": 0, "roi_hi": 0, "p_positive": 0}

    rng = np.random.default_rng(seed)
    hits = df["hit"].to_numpy()
    payouts = df["actual_payout"].to_numpy()
    n = len(df)

    rois = np.empty(n_iter)
    for i in range(n_iter):
        idx = rng.integers(0, n, size=n)
        sampled_payout = (hits[idx] * payouts[idx]).sum()
        rois[i] = (sampled_payout - bet_amount * n) / (bet_amount * n)

    alpha = (1 - ci) / 2
    lo = float(np.quantile(rois, alpha))
    hi = float(np.quantile(rois, 1 - alpha))
    return {
        "n": n,
        "roi_mean": float(rois.mean()),
        "roi_median": float(np.median(rois)),
        "roi_lo": lo,
        "roi_hi": hi,
        "p_positive": float((rois > 0).mean()),
        "p_above_0_05": float((rois > 0.05).mean()),
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--version", default="v0.2")
    p.add_argument("--date-from", required=True)
    p.add_argument("--date-to", required=True)
    p.add_argument("--split-ratio", type=float, default=0.85)
    p.add_argument("--n-iter", type=int, default=1000)
    p.add_argument("--min-n", type=int, default=20)
    args = p.parse_args()

    artifact = load_artifact(args.version)
    df_all = build_training_frame(date_from=args.date_from, date_to=args.date_to)
    _, df_val = split_time_ratio(df_all, args.split_ratio)
    df_pred = predict_with_probs(artifact, df_val)

    # top1 予測
    idx = df_pred.groupby("race_id")["prob_first"].idxmax()
    top1 = df_pred.loc[idx].rename(columns={"boat_number": "top1_boat", "prob_first": "top1_prob"})

    df_from = date.fromisoformat(args.date_from)
    df_to = date.fromisoformat(args.date_to)
    win_p = load_payouts(config.DB_PATH, df_from, df_to, "win")
    win_map = dict(zip(zip(win_p["race_id"], win_p["combination"]), win_p["payout"]))

    # レース単位 hit/payout
    rows = []
    for _, r in top1.iterrows():
        rid = r["race_id"]
        b = int(r["top1_boat"])
        ap = float(win_map.get((rid, str(b)), 0))
        rows.append({
            "race_id": rid,
            "stadium_number": int(r["stadium_number"]),
            "race_grade_number": r.get("race_grade_number"),
            "wind_speed": r.get("wind_speed"),
            "is_night": r.get("is_night"),
            "top1_boat": b,
            "top1_prob": float(r["top1_prob"]),
            "actual_payout": ap,
            "hit": 1 if ap > 0 else 0,
        })
    df_r = pd.DataFrame(rows)

    print(f"=== ALL races (n={len(df_r):,}) ===")
    overall = bootstrap_roi(df_r, n_iter=args.n_iter)
    print(f"  ROI: {overall['roi_mean']:+.4f}  95%CI [{overall['roi_lo']:+.4f}, {overall['roi_hi']:+.4f}]  P(>0)={overall['p_positive']:.3f}")

    stadium_names = _stadium_name_map()

    print("\n=== 会場別 (n>={}) ===".format(args.min_n))
    rows_out = []
    for sn, sub in df_r.groupby("stadium_number"):
        if len(sub) < args.min_n:
            continue
        ci = bootstrap_roi(sub, n_iter=args.n_iter)
        rows_out.append({
            "stadium": stadium_names.get(sn, str(sn)),
            "n": ci["n"],
            "roi": ci["roi_mean"],
            "ci_lo": ci["roi_lo"],
            "ci_hi": ci["roi_hi"],
            "P(ROI>0)": ci["p_positive"],
            "P(ROI>5%)": ci["p_above_0_05"],
        })
    out_df = pd.DataFrame(rows_out).sort_values("roi", ascending=False)
    print(out_df.to_string(index=False, float_format="%.4f"))

    print("\n=== 1号艇 30-50% (拮抗) × 会場 (n>={}) ===".format(args.min_n))
    sub_all = df_r[(df_r["top1_boat"] == 1) & (df_r["top1_prob"] >= 0.30) & (df_r["top1_prob"] < 0.50)]
    print(f"全体 n={len(sub_all)}")
    if len(sub_all) > args.min_n:
        ci = bootstrap_roi(sub_all, n_iter=args.n_iter)
        print(f"  ROI: {ci['roi_mean']:+.4f}  95%CI [{ci['roi_lo']:+.4f}, {ci['roi_hi']:+.4f}]  P(>0)={ci['p_positive']:.3f}")
    rows_out = []
    for sn, sub in sub_all.groupby("stadium_number"):
        if len(sub) < args.min_n:
            continue
        ci = bootstrap_roi(sub, n_iter=args.n_iter)
        rows_out.append({
            "stadium": stadium_names.get(sn, str(sn)),
            "n": ci["n"],
            "roi": ci["roi_mean"],
            "ci_lo": ci["roi_lo"],
            "ci_hi": ci["roi_hi"],
            "P(ROI>0)": ci["p_positive"],
        })
    if rows_out:
        out_df = pd.DataFrame(rows_out).sort_values("roi", ascending=False)
        print(out_df.to_string(index=False, float_format="%.4f"))

    print("\n=== 1号艇予測 70%+ × 会場 (n>={}) ===".format(args.min_n))
    sub_all = df_r[(df_r["top1_boat"] == 1) & (df_r["top1_prob"] >= 0.70)]
    print(f"全体 n={len(sub_all)}")
    if len(sub_all) > args.min_n:
        ci = bootstrap_roi(sub_all, n_iter=args.n_iter)
        print(f"  ROI: {ci['roi_mean']:+.4f}  95%CI [{ci['roi_lo']:+.4f}, {ci['roi_hi']:+.4f}]  P(>0)={ci['p_positive']:.3f}")


if __name__ == "__main__":
    main()
