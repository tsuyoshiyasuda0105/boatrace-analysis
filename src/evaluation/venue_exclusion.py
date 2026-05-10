"""
「避けるべき会場を切る」戦略の検証

戸田・芦屋・三国・蒲郡 (CI で確実マイナスと検証済) を除外して
全体 ROI がどう改善するかを Bootstrap CI で確認。
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


# CI で確実マイナスの会場 (v0.2 検証済)
EXCLUDE_VENUES = {
    2: "戸田",
    21: "芦屋",
    10: "三国",
    7: "蒲郡",
}

# 弱マイナスだが CI 一部正側 (要検討)
QUESTIONABLE_VENUES = {
    24: "大村",
    19: "下関",
    4: "平和島",
    8: "常滑",
}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--version", default="v0.2")
    p.add_argument("--date-from", required=True)
    p.add_argument("--date-to", required=True)
    p.add_argument("--split-ratio", type=float, default=0.85)
    p.add_argument("--n-iter", type=int, default=2000)
    args = p.parse_args()

    artifact = load_artifact(args.version)
    df_all = build_training_frame(date_from=args.date_from, date_to=args.date_to)
    _, df_val = split_time_ratio(df_all, args.split_ratio)
    df_pred = predict_with_probs(artifact, df_val)

    # top1 予測
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
            "top1_prob": float(r["top1_prob"]),
            "actual_payout": ap,
            "hit": 1 if ap > 0 else 0,
        })
    df_r = pd.DataFrame(rows)

    print(f"=== 戦略別 Bootstrap CI (95%, n_iter={args.n_iter}) ===\n")

    # 戦略 A: 全レース買い
    A = df_r
    a_ci = bootstrap_roi(A, n_iter=args.n_iter)
    print(f"[A] 全レース買い (n={a_ci['n']:,})")
    print(f"    ROI {a_ci['roi_mean']:+.4f}  CI [{a_ci['roi_lo']:+.4f}, {a_ci['roi_hi']:+.4f}]  P(>0)={a_ci['p_positive']:.3f}\n")

    # 戦略 B: 確実マイナス会場 (4) を除外
    B = df_r[~df_r["stadium_number"].isin(EXCLUDE_VENUES.keys())]
    b_ci = bootstrap_roi(B, n_iter=args.n_iter)
    print(f"[B] {','.join(EXCLUDE_VENUES.values())} を除外 (n={b_ci['n']:,})")
    print(f"    ROI {b_ci['roi_mean']:+.4f}  CI [{b_ci['roi_lo']:+.4f}, {b_ci['roi_hi']:+.4f}]  P(>0)={b_ci['p_positive']:.3f}")
    diff = b_ci['roi_mean'] - a_ci['roi_mean']
    print(f"    改善幅: {diff:+.4f} ({diff*100:+.1f}pt)\n")

    # 戦略 C: 8会場を除外 (確実+弱マイナス)
    EXCLUDE2 = {**EXCLUDE_VENUES, **QUESTIONABLE_VENUES}
    C = df_r[~df_r["stadium_number"].isin(EXCLUDE2.keys())]
    c_ci = bootstrap_roi(C, n_iter=args.n_iter)
    print(f"[C] 8会場除外 ({','.join(EXCLUDE2.values())}) (n={c_ci['n']:,})")
    print(f"    ROI {c_ci['roi_mean']:+.4f}  CI [{c_ci['roi_lo']:+.4f}, {c_ci['roi_hi']:+.4f}]  P(>0)={c_ci['p_positive']:.3f}")
    diff = c_ci['roi_mean'] - a_ci['roi_mean']
    print(f"    改善幅: {diff:+.4f} ({diff*100:+.1f}pt)\n")

    # 戦略 D: 1号艇予測 70%+ かつ確実マイナス会場除外 (鉄板狙い)
    D = df_r[
        (df_r["top1_prob"] >= 0.70)
        & (~df_r["stadium_number"].isin(EXCLUDE_VENUES.keys()))
    ]
    d_ci = bootstrap_roi(D, n_iter=args.n_iter)
    print(f"[D] 1号艇 70%+ × 確実マイナス会場除外 (n={d_ci['n']:,})")
    print(f"    ROI {d_ci['roi_mean']:+.4f}  CI [{d_ci['roi_lo']:+.4f}, {d_ci['roi_hi']:+.4f}]  P(>0)={d_ci['p_positive']:.3f}\n")

    # 戦略 E: 拮抗 (30-50%) × 確実マイナス会場除外
    E = df_r[
        (df_r["top1_prob"] >= 0.30) & (df_r["top1_prob"] < 0.50)
        & (~df_r["stadium_number"].isin(EXCLUDE_VENUES.keys()))
    ]
    e_ci = bootstrap_roi(E, n_iter=args.n_iter)
    print(f"[E] 1号艇 30-50% × 確実マイナス会場除外 (n={e_ci['n']:,})")
    print(f"    ROI {e_ci['roi_mean']:+.4f}  CI [{e_ci['roi_lo']:+.4f}, {e_ci['roi_hi']:+.4f}]  P(>0)={e_ci['p_positive']:.3f}\n")


if __name__ == "__main__":
    main()
