"""
複数モデル version の予測アンサンブル

v0.2 と v0.5 の Stage 1 確率を加重平均。
カスケードはどちらか一方を使う (v0.2 PerWinner が最良なので)。
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


LOSING_VENUES = {2, 7, 10, 21}


def predict_ensemble(versions: list[tuple[str, float]], df_val: pd.DataFrame) -> pd.DataFrame:
    """
    versions = [(version_name, weight), ...]
    返り値: race_id, boat_number, prob_first (加重平均)
    """
    base_cols = ["race_id", "boat_number"]
    accum = None
    total_w = 0.0
    for v, w in versions:
        artifact = load_artifact(v)
        df_pred = predict_with_probs(artifact, df_val)
        sub = df_pred[base_cols + ["prob_first"]].copy()
        sub = sub.rename(columns={"prob_first": f"prob_{v}"})
        if accum is None:
            accum = sub
        else:
            accum = accum.merge(sub, on=base_cols, how="inner")
        total_w += w
    # 加重平均
    cols = [c for c in accum.columns if c.startswith("prob_")]
    weights = np.array([w for _, w in versions])
    weights = weights / weights.sum()
    accum["prob_first"] = sum(accum[c] * weights[i] for i, c in enumerate(cols))
    return accum[base_cols + ["prob_first"]]


def evaluate_ensemble(versions: list[tuple[str, float]], df_val: pd.DataFrame,
                     win_map: dict, n_iter: int = 1000) -> dict:
    df_pred = predict_ensemble(versions, df_val)

    # stadium_number を取り戻す (build_training_frame の元 df から)
    stadium_lookup = df_val.drop_duplicates("race_id").set_index("race_id")["stadium_number"].to_dict()

    idx = df_pred.groupby("race_id")["prob_first"].idxmax()
    top1 = df_pred.loc[idx]
    rows = []
    for _, r in top1.iterrows():
        rid = r["race_id"]
        b = int(r["boat_number"])
        ap = float(win_map.get((rid, str(b)), 0))
        rows.append({
            "race_id": rid,
            "stadium_number": int(stadium_lookup.get(rid, 0)),
            "top1_prob": float(r["prob_first"]),
            "actual_payout": ap,
            "hit": 1 if ap > 0 else 0,
        })
    df_r = pd.DataFrame(rows)

    a = bootstrap_roi(df_r, n_iter=n_iter)
    df_d = df_r[(df_r["top1_prob"] >= 0.70) & (~df_r["stadium_number"].isin(LOSING_VENUES))]
    d = bootstrap_roi(df_d, n_iter=n_iter) if len(df_d) > 0 else None
    return {
        "label": "+".join(f"{v}({w})" for v, w in versions),
        "hit_rate": df_r["hit"].mean(),
        "all_roi": a["roi_mean"], "all_ci": (a["roi_lo"], a["roi_hi"]),
        "sweet_roi": d["roi_mean"] if d else None,
        "sweet_ci": (d["roi_lo"], d["roi_hi"]) if d else None,
        "sweet_n": d["n"] if d else 0,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--date-from", required=True)
    p.add_argument("--date-to", required=True)
    p.add_argument("--split-ratio", type=float, default=0.85)
    p.add_argument("--n-iter", type=int, default=1000)
    args = p.parse_args()

    df_all = build_training_frame(date_from=args.date_from, date_to=args.date_to)
    _, df_val = split_time_ratio(df_all, args.split_ratio)
    print(f"val races: {df_val['race_id'].nunique():,}")

    df_from = date.fromisoformat(args.date_from)
    df_to = date.fromisoformat(args.date_to)
    win_p = load_payouts(config.DB_PATH, df_from, df_to, "win")
    win_map = dict(zip(zip(win_p["race_id"], win_p["combination"]), win_p["payout"]))

    candidates = [
        [("v0.2", 1.0)],
        [("v0.5", 1.0)],
        [("v0.2", 0.5), ("v0.5", 0.5)],
        [("v0.2", 0.7), ("v0.5", 0.3)],
        [("v0.2", 0.6), ("v0.5", 0.4)],
        [("v0.2", 0.4), ("v0.4", 0.3), ("v0.5", 0.3)],
    ]

    print("\n" + "=" * 86)
    print(f"{'label':<32}{'hit_rate':>10}{'all_ROI':>10}{'all_CI':>22}{'sweet_ROI':>12}{'sweet_n':>10}")
    print("=" * 86)
    for c in candidates:
        try:
            r = evaluate_ensemble(c, df_val, win_map, n_iter=args.n_iter)
            all_ci = f"[{r['all_ci'][0]:+.3f},{r['all_ci'][1]:+.3f}]"
            sweet = f"{r['sweet_roi']:+.4f}" if r['sweet_roi'] is not None else "—"
            print(f"{r['label']:<32}{r['hit_rate']:>10.4f}{r['all_roi']:>10.4f}{all_ci:>22}{sweet:>12}{r['sweet_n']:>10}")
        except FileNotFoundError as e:
            print(f"  [SKIP] {e}")


if __name__ == "__main__":
    main()
