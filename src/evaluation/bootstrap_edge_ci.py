"""edge 帯別の Bootstrap CI"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd

import config
from src.db.connection import connect as db_connect
from src.features.builder import build_training_frame
from src.models.train import split_time_ratio
from src.evaluation.evaluate_with_payouts import load_artifact, predict_with_probs, load_payouts
from src.evaluation.market_vs_model import compute_market_implied_first
from src.evaluation.bootstrap_ci import bootstrap_roi


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--version", default="v0.2")
    p.add_argument("--date-from", required=True)
    p.add_argument("--date-to", required=True)
    p.add_argument("--split-ratio", type=float, default=0.85)
    p.add_argument("--snapshot", default="final")
    p.add_argument("--n-iter", type=int, default=2000)
    args = p.parse_args()

    artifact = load_artifact(args.version)
    df_all = build_training_frame(date_from=args.date_from, date_to=args.date_to)
    _, df_val = split_time_ratio(df_all, args.split_ratio)
    df_pred = predict_with_probs(artifact, df_val)

    df_from = date.fromisoformat(args.date_from)
    df_to = date.fromisoformat(args.date_to)
    sql = """
        SELECT o.race_id, o.combination, o.odds
          FROM odds_trifecta o
          JOIN races r ON o.race_id = r.race_id
         WHERE r.race_date BETWEEN ? AND ?
           AND o.snapshot_label = ?
    """
    with db_connect() as conn:
        odds_df = pd.read_sql_query(sql, conn, params=(df_from.isoformat(), df_to.isoformat(), args.snapshot))
    market = compute_market_implied_first(odds_df)

    races_with_odds = set(odds_df["race_id"].unique())
    df_pred = df_pred[df_pred["race_id"].isin(races_with_odds)].copy()
    print(f"val races with odds: {df_pred['race_id'].nunique():,}")

    merged = df_pred[["race_id", "boat_number", "prob_first"]].merge(
        market, on=["race_id", "boat_number"], how="left",
    )
    merged["edge"] = (merged["prob_first"] - merged["implied_first"]) / merged["implied_first"]

    win_p = load_payouts(config.DB_PATH, df_from, df_to, "win")
    win_map = dict(zip(zip(win_p["race_id"], win_p["combination"]), win_p["payout"]))

    # 各レースで「最大 edge の艇」
    idx = merged.groupby("race_id")["edge"].idxmax()
    selected = merged.loc[idx].copy()
    selected["actual_payout"] = selected.apply(
        lambda r: float(win_map.get((r["race_id"], str(int(r["boat_number"]))), 0)), axis=1)
    selected["hit"] = (selected["actual_payout"] > 0).astype(int)

    print("\n=== edge 帯別 ROI Bootstrap CI (95%) ===")
    bins_list = [
        ("edge<0%", -1, 0),
        ("edge 0-10%", 0, 0.10),
        ("edge 10-25% ⭐", 0.10, 0.25),
        ("edge 10-20%", 0.10, 0.20),
        ("edge 12-22%", 0.12, 0.22),
        ("edge 25-50%", 0.25, 0.50),
        ("edge 50%+", 0.50, 5.0),
    ]
    rows = []
    for label, lo, hi in bins_list:
        sub = selected[(selected["edge"] >= lo) & (selected["edge"] < hi)]
        if len(sub) < 20:
            continue
        ci = bootstrap_roi(sub, n_iter=args.n_iter)
        rows.append({
            "label": label,
            "n": ci["n"],
            "ROI": ci["roi_mean"],
            "CI_lo": ci["roi_lo"],
            "CI_hi": ci["roi_hi"],
            "P(>0)": ci["p_positive"],
            "P(>5%)": ci["p_above_0_05"],
        })
    print(pd.DataFrame(rows).to_string(index=False, float_format="%.4f"))


if __name__ == "__main__":
    main()
