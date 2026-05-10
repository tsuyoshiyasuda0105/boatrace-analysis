"""Per-winner カスケード評価 (v0.1 unified との比較)"""
import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from src.features.builder import build_training_frame
from src.models.train import split_time_ratio
from src.evaluation.evaluate_with_payouts import load_artifact, predict_with_probs, load_payouts
from src.models.cascade import predict_trifecta_joint, load_cascade
from src.models.cascade_per_winner import predict_trifecta_per_winner, load_per_winner_cascade
from src.evaluation.evaluate_cascade import (
    predict_trifecta_plackett, topk_hit_rate, fixed_top1_roi, topk_split_roi,
)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="v0.2")
    p.add_argument("--unified", default="cascade-v0.1")
    p.add_argument("--per-winner", default="pw-v0.1")
    p.add_argument("--date-from", required=True)
    p.add_argument("--date-to", required=True)
    p.add_argument("--split-ratio", type=float, default=0.8)
    p.add_argument("--max-val-races", type=int, default=1500)
    args = p.parse_args()

    print(f"[load] artifacts")
    artifact = load_artifact(args.base)
    unified = load_cascade(args.unified)
    pw = load_per_winner_cascade(args.per_winner)

    print(f"[val] {args.date_from} .. {args.date_to}")
    df = build_training_frame(date_from=args.date_from, date_to=args.date_to)
    _, df_val = split_time_ratio(df, args.split_ratio)
    if args.max_val_races and df_val["race_id"].nunique() > args.max_val_races:
        keep = df_val.drop_duplicates("race_id").head(args.max_val_races)["race_id"]
        df_val = df_val[df_val["race_id"].isin(keep)]
    print(f"      {df_val['race_id'].nunique():,} races")

    print("[predict] stage1")
    df_pred = predict_with_probs(artifact, df_val)

    print("[predict] Plackett-Luce / unified cascade / per-winner cascade")
    pl_pred = predict_trifecta_plackett(df_pred)

    cs_pred = predict_trifecta_joint(
        df_pred, unified["stage2_model"], unified["stage2_features"],
        unified["stage3_model"], unified["stage3_features"],
        pattern_2nd=unified.get("pattern_2nd"), pattern_3rd=unified.get("pattern_3rd"),
    )
    pw_pred = predict_trifecta_per_winner(
        df_pred, pw["s2"], pw["s3"],
        fallback_s2_model=unified["stage2_model"], fallback_s2_features=unified["stage2_features"],
        fallback_s3_model=unified["stage3_model"], fallback_s3_features=unified["stage3_features"],
    )

    df_from = date.fromisoformat(args.date_from)
    df_to = date.fromisoformat(args.date_to)
    tri_p = load_payouts(config.DB_PATH, df_from, df_to, "trifecta")

    print("\n" + "=" * 60)
    print(" 三連単 Top-K 的中率")
    print("=" * 60)
    for name, pred in [("PL", pl_pred), ("Unified", cs_pred), ("PerWinner", pw_pred)]:
        h = topk_hit_rate(pred, tri_p)
        print(f"  [{name:9s}] top1={h['top_1_hit_rate']:.4f}  top3={h['top_3_hit_rate']:.4f}  top10={h['top_10_hit_rate']:.4f}")

    print("\n" + "=" * 60)
    print(" Top-1 fixed bet ROI")
    print("=" * 60)
    for name, pred in [("PL", pl_pred), ("Unified", cs_pred), ("PerWinner", pw_pred)]:
        r = fixed_top1_roi(pred, tri_p)
        print(f"  [{name:9s}] n={r['n_bets']} hits={r['n_hits']} hit_rate={r['hit_rate']:.4f} ROI={r['roi']:+.4f}")

    print("\n" + "=" * 60)
    print(" Top-3 split bet ROI (3組合せに各100円)")
    print("=" * 60)
    for name, pred in [("PL", pl_pred), ("Unified", cs_pred), ("PerWinner", pw_pred)]:
        r = topk_split_roi(pred, tri_p, k=3)
        print(f"  [{name:9s}] n={r['n_races']} hits={r['n_hits']} hit_rate={r['hit_rate']:.4f} ROI={r['roi']:+.4f}")


if __name__ == "__main__":
    main()
