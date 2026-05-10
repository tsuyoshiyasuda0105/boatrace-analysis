"""
PerWinner Top-1 + 実オッズで EV 条件付けベット

戦略:
  1. 各レースで PerWinner Top-1 組合せを取得
  2. その組合せの実オッズを参照
  3. predicted_prob × odds >= threshold なら買い、そうでなければスキップ
"""
import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

import config
from src.features.builder import build_training_frame
from src.evaluation.evaluate_with_payouts import load_artifact, predict_with_probs
from src.models.cascade import load_cascade
from src.models.cascade_per_winner import load_per_winner_cascade, predict_trifecta_per_winner
from src.evaluation.value_bet_trifecta import load_full_odds, load_payouts_trifecta


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="v0.2")
    p.add_argument("--cascade", default="cascade-v0.1")
    p.add_argument("--per-winner", default="pw-v0.1")
    p.add_argument("--date-from", required=True)
    p.add_argument("--date-to", required=True)
    args = p.parse_args()

    artifact = load_artifact(args.base)
    cascade = load_cascade(args.cascade)
    pw = load_per_winner_cascade(args.per_winner)

    df = build_training_frame(date_from=args.date_from, date_to=args.date_to)
    df_from = date.fromisoformat(args.date_from)
    df_to = date.fromisoformat(args.date_to)
    odds_df = load_full_odds(config.DB_PATH, df_from, df_to)
    payouts = load_payouts_trifecta(config.DB_PATH, df_from, df_to)

    if odds_df.empty:
        print("no odds data")
        return

    races_with_odds = set(odds_df["race_id"].unique())
    df = df[df["race_id"].isin(races_with_odds)]
    print(f"target races (with odds): {df['race_id'].nunique():,}")

    df_pred = predict_with_probs(artifact, df)
    pw_pred = predict_trifecta_per_winner(
        df_pred, pw["s2"], pw["s3"],
        fallback_s2_model=cascade["stage2_model"], fallback_s2_features=cascade["stage2_features"],
        fallback_s3_model=cascade["stage3_model"], fallback_s3_features=cascade["stage3_features"],
    )

    odds_lookup = {(r["race_id"], r["combination"]): float(r["odds"])
                   for _, r in odds_df.iterrows()}

    # 各レースの top-1 + その組合せのオッズ
    rows = []
    for race_id, combos in pw_pred.items():
        if not combos or race_id not in payouts:
            continue
        top1_combo, top1_prob = max(combos.items(), key=lambda x: x[1])
        odds = odds_lookup.get((race_id, top1_combo))
        actual_combo, actual_payout = payouts[race_id]
        hit = 1 if top1_combo == actual_combo else 0
        rows.append({
            "race_id": race_id,
            "top1_combo": top1_combo,
            "top1_prob": top1_prob,
            "market_odds": odds,
            "ev": (top1_prob * odds - 1) if odds else None,
            "actual_combo": actual_combo,
            "actual_payout": actual_payout,
            "hit": hit,
        })
    df_r = pd.DataFrame(rows)
    print(f"  total: {len(df_r):,} races analyzed")
    df_r_with_odds = df_r.dropna(subset=["market_odds"]).copy()
    print(f"  with odds: {len(df_r_with_odds):,}")

    print("\n" + "=" * 60)
    print(" PerWinner Top-1 + EV しきい値別フィルタ")
    print("=" * 60)
    for thr in [-1.0, -0.5, -0.3, -0.1, 0.0, 0.1, 0.2]:
        sub = df_r_with_odds[df_r_with_odds["ev"] >= thr]
        if len(sub) == 0:
            print(f"  EV >= {thr:+.2f}: NO BETS")
            continue
        n = len(sub)
        n_hits = int(sub["hit"].sum())
        total_stake = 100 * n
        total_payout = (sub["hit"] * sub["actual_payout"]).sum()
        roi = (total_payout - total_stake) / total_stake
        avg_odds = sub["market_odds"].mean()
        avg_prob = sub["top1_prob"].mean()
        print(f"  EV >= {thr:+.2f}: n={n:4,} hits={n_hits:3} hit_rate={n_hits/n:.4f} ROI={roi:+.4f}  avg_odds={avg_odds:.1f} avg_prob={avg_prob:.4f}")


if __name__ == "__main__":
    main()
