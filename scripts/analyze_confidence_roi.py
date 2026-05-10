"""
PerWinner Top-1 の予測確率しきい値別 ROI 分析

「自信度の高いレースだけ買う」戦略で +EV になるかをチェック。
"""
import argparse
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

import config
from src.features.builder import build_training_frame
from src.models.train import split_time_ratio
from src.evaluation.evaluate_with_payouts import load_artifact, predict_with_probs, load_payouts
from src.models.cascade import load_cascade
from src.models.cascade_per_winner import load_per_winner_cascade, predict_trifecta_per_winner
from datetime import date


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="v0.2")
    p.add_argument("--cascade", default="cascade-v0.1")
    p.add_argument("--per-winner", default="pw-v0.1")
    p.add_argument("--date-from", default="2025-05-08")
    p.add_argument("--date-to", default="2026-05-08")
    p.add_argument("--max-val-races", type=int, default=3000)
    args = p.parse_args()

    artifact = load_artifact(args.base)
    cascade = load_cascade(args.cascade)
    pw = load_per_winner_cascade(args.per_winner)

    df = build_training_frame(date_from=args.date_from, date_to=args.date_to)
    _, df_val = split_time_ratio(df, 0.8)
    if df_val["race_id"].nunique() > args.max_val_races:
        keep = df_val.drop_duplicates("race_id").head(args.max_val_races)["race_id"]
        df_val = df_val[df_val["race_id"].isin(keep)]
    print(f"val races: {df_val['race_id'].nunique():,}")

    df_pred = predict_with_probs(artifact, df_val)
    pw_pred = predict_trifecta_per_winner(
        df_pred, pw["s2"], pw["s3"],
        fallback_s2_model=cascade["stage2_model"], fallback_s2_features=cascade["stage2_features"],
        fallback_s3_model=cascade["stage3_model"], fallback_s3_features=cascade["stage3_features"],
    )

    df_from = date.fromisoformat(args.date_from)
    df_to = date.fromisoformat(args.date_to)
    tri_p = load_payouts(config.DB_PATH, df_from, df_to, "trifecta")
    pay_map = dict(zip(tri_p["race_id"], zip(tri_p["combination"], tri_p["payout"])))

    # 各レースの top-1 prob と当たったか
    rows = []
    for race_id, combos in pw_pred.items():
        if race_id not in pay_map:
            continue
        actual_combo, actual_payout = pay_map[race_id]
        sorted_c = sorted(combos.items(), key=lambda x: -x[1])
        top1_combo, top1_prob = sorted_c[0]
        hit = 1 if top1_combo == actual_combo else 0
        rows.append({
            "race_id": race_id,
            "top1_combo": top1_combo,
            "top1_prob": top1_prob,
            "actual_combo": actual_combo,
            "actual_payout": float(actual_payout),
            "hit": hit,
        })
    df_r = pd.DataFrame(rows)
    print(f"  total: {len(df_r):,} races analyzed")

    print("\n" + "=" * 60)
    print(" 予測確率しきい値別 ROI (PerWinner Top-1, 100円固定ベット)")
    print("=" * 60)
    for thr in [0.0, 0.05, 0.08, 0.10, 0.12, 0.15, 0.18, 0.20, 0.25]:
        sub = df_r[df_r["top1_prob"] >= thr]
        if len(sub) == 0:
            print(f"  prob >= {thr:.2f}: NO RACES")
            continue
        n = len(sub)
        n_hits = int(sub["hit"].sum())
        hit_rate = n_hits / n
        total_stake = 100 * n
        total_payout = (sub["hit"] * sub["actual_payout"]).sum()
        roi = (total_payout - total_stake) / total_stake
        print(f"  prob >= {thr:.2f}: n={n:5,} hit_rate={hit_rate:.4f} ROI={roi:+.4f}  (avg payout={sub.loc[sub['hit']==1, 'actual_payout'].mean() if n_hits else 0:.0f})")

    # 予測確率と payout の散布 (hit のみ)
    print("\n  hit 時の payout 分布 (high prob のレースで小配当が多くないか?)")
    for low, high in [(0.0, 0.08), (0.08, 0.12), (0.12, 0.18), (0.18, 1.0)]:
        sub = df_r[(df_r["top1_prob"] >= low) & (df_r["top1_prob"] < high) & (df_r["hit"] == 1)]
        if len(sub) > 0:
            mean_p = sub["actual_payout"].mean()
            median_p = sub["actual_payout"].median()
            print(f"  prob[{low:.2f},{high:.2f}): n_hits={len(sub):4,} mean={mean_p:7.0f} median={median_p:7.0f}")


if __name__ == "__main__":
    main()
