"""
カスケードモデルの評価

比較する2つの三連単確率モデル:
  A. Plackett-Luce 単純近似 (現状の find_value_bets_trifecta)
  B. 条件付きカスケード (stage1 + stage2 + stage3)

評価指標:
  - 三連単 top-1 的中率: 各レースで予測最有力組合せが当たったか
  - 三連単 top-3 的中率: 予測上位3組合せの中に正解があるか
  - 三連単 top-10 的中率
  - log-loss / Brier (確率較正の良さ)
  - ROI: top-1 組合せに 100円固定ベット
  - ROI: top-N 組合せに分散ベット
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd

import config
from src.db.connection import connect as db_connect
from src.features.builder import build_training_frame
from src.models.train import predict_probs, split_time_ratio
from src.models.calibration import apply_calibrators
from src.evaluation.evaluate_with_payouts import (
    load_artifact, predict_with_probs, load_payouts,
)
from src.evaluation.value_bet import trifecta_combination_prob
from src.models.cascade import predict_trifecta_joint, load_cascade


# ============================================================
# Plackett-Luce ベースライン (既存)
# ============================================================

def predict_trifecta_plackett(df_pred: pd.DataFrame) -> dict[str, dict[str, float]]:
    out = {}
    for race_id, race_df in df_pred.groupby("race_id"):
        boat_probs = {
            int(r["boat_number"]): {
                "prob_first": float(r["prob_first"]),
                "prob_top_2": float(r["prob_top_2"]),
                "prob_top_3": float(r["prob_top_3"]),
            }
            for _, r in race_df.iterrows()
        }
        combos = trifecta_combination_prob(boat_probs)
        out[race_id] = combos
    return out


# ============================================================
# 評価
# ============================================================

def topk_hit_rate(predicted: dict[str, dict[str, float]],
                  payouts: pd.DataFrame, k_list=(1, 3, 10)) -> dict:
    """
    payouts: trifecta payouts DataFrame (race_id, combination, payout)
    予測組合せ確率の上位k に正解 combination が含まれる率
    """
    pay_map = dict(zip(payouts["race_id"], payouts["combination"]))
    hits = {k: 0 for k in k_list}
    n = 0
    for race_id, combos in predicted.items():
        if race_id not in pay_map:
            continue
        actual = pay_map[race_id]
        n += 1
        sorted_combos = sorted(combos.items(), key=lambda x: -x[1])
        for k in k_list:
            top_k = [c for c, _ in sorted_combos[:k]]
            if actual in top_k:
                hits[k] += 1
    return {f"top_{k}_hit_rate": hits[k] / n if n else 0 for k in k_list} | {"n_races": n}


def fixed_top1_roi(predicted: dict[str, dict[str, float]],
                   payouts: pd.DataFrame, bet_amount: float = 100.0) -> dict:
    """各レースで予測 top-1 トリフェクタに 100円ベット"""
    pay_map = dict(zip(payouts["race_id"], zip(payouts["combination"], payouts["payout"])))
    n_bets = 0
    n_hits = 0
    total_payout = 0.0
    for race_id, combos in predicted.items():
        if not combos or race_id not in pay_map:
            continue
        n_bets += 1
        top1 = max(combos, key=combos.get)
        actual_combo, actual_payout = pay_map[race_id]
        if top1 == actual_combo:
            n_hits += 1
            total_payout += float(actual_payout)
    total_stake = bet_amount * n_bets
    return {
        "n_bets": n_bets,
        "n_hits": n_hits,
        "hit_rate": n_hits / n_bets if n_bets else 0,
        "total_stake": total_stake,
        "total_payout": total_payout,
        "roi": (total_payout - total_stake) / total_stake if total_stake else 0,
    }


def topk_split_roi(predicted: dict[str, dict[str, float]],
                   payouts: pd.DataFrame, k: int = 3,
                   bet_amount: float = 100.0) -> dict:
    """各レースで予測 top-k 組合せにそれぞれ100円ずつベット (合計 k*100)"""
    pay_map = dict(zip(payouts["race_id"], zip(payouts["combination"], payouts["payout"])))
    n_races = 0
    n_hits = 0
    total_payout = 0.0
    for race_id, combos in predicted.items():
        if not combos or race_id not in pay_map:
            continue
        n_races += 1
        sorted_combos = sorted(combos.items(), key=lambda x: -x[1])
        top_k = [c for c, _ in sorted_combos[:k]]
        actual_combo, actual_payout = pay_map[race_id]
        if actual_combo in top_k:
            n_hits += 1
            total_payout += float(actual_payout)
    total_stake = bet_amount * k * n_races
    return {
        "k": k,
        "n_races": n_races,
        "n_hits": n_hits,
        "hit_rate": n_hits / n_races if n_races else 0,
        "total_stake": total_stake,
        "total_payout": total_payout,
        "roi": (total_payout - total_stake) / total_stake if total_stake else 0,
    }


def value_bet_roi(predicted: dict[str, dict[str, float]],
                  payouts: pd.DataFrame, ev_threshold: float = 0.0,
                  bet_amount: float = 100.0) -> dict:
    """
    実払戻だけ分かる制約のもと、予測確率と実現オッズの比較で EV+ ベットを抽出。
    NOTE: 外れ目のオッズが分からないので、これは「当たった場合のみ評価可」。
    つまり実運用の代理ではなく、参考値。
    """
    pay_map = dict(zip(payouts["race_id"], zip(payouts["combination"], payouts["payout"])))
    n_bets = 0
    total_stake = 0.0
    total_payout = 0.0
    for race_id, combos in predicted.items():
        if race_id not in pay_map:
            continue
        actual_combo, actual_payout = pay_map[race_id]
        # 実現オッズ = payout / 100
        odds = float(actual_payout) / bet_amount
        prob = combos.get(actual_combo, 0.0)
        ev = prob * odds - 1.0
        if ev >= ev_threshold:
            n_bets += 1
            total_stake += bet_amount
            total_payout += float(actual_payout)
    return {
        "n_bets": n_bets,
        "total_stake": total_stake,
        "total_payout": total_payout,
        "roi": (total_payout - total_stake) / total_stake if total_stake else 0,
        "warning": "外れ目オッズ無し → 上方バイアス、参考値のみ",
    }


# ============================================================
# main
# ============================================================

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="v0.2", help="Stage1 (ranker_v*.pkl) version")
    p.add_argument("--cascade", default="cascade-v0.1")
    p.add_argument("--date-from", required=True)
    p.add_argument("--date-to", required=True)
    p.add_argument("--split-ratio", type=float, default=0.8,
                   help="この比率分の前半を学習扱い、後半 (val) を評価対象")
    p.add_argument("--max-val-races", type=int, default=None,
                   help="val 期間内の最初の N レースだけ評価 (時短)")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    print(f"[1/4] artifact ロード base={args.base} cascade={args.cascade}")
    artifact = load_artifact(args.base)
    cascade = load_cascade(args.cascade)
    if cascade is None:
        print(f"  cascade artifact not found: {args.cascade}")
        return

    print(f"[2/4] val 期間構築 (split_ratio={args.split_ratio})")
    df_all = build_training_frame(date_from=args.date_from, date_to=args.date_to)
    _, df_val = split_time_ratio(df_all, args.split_ratio)
    print(f"      val: {len(df_val):,} 行 / {df_val['race_id'].nunique():,} レース")
    if args.max_val_races is not None and df_val["race_id"].nunique() > args.max_val_races:
        keep_ids = df_val.drop_duplicates("race_id").head(args.max_val_races)["race_id"]
        df_val = df_val[df_val["race_id"].isin(keep_ids)]
        print(f"      → {df_val['race_id'].nunique():,} races に制限")

    print("[3/4] 予測 (Stage 1)")
    df_val_pred = predict_with_probs(artifact, df_val)

    print("[4/4] joint 予測")
    print("  Plackett-Luce ベースライン...")
    pl_pred = predict_trifecta_plackett(df_val_pred)

    print("  カスケード (Stage1+2+3)...")
    cascade_pred = predict_trifecta_joint(
        df_val_pred[["race_id", "boat_number", "prob_first"] + [
            c for c in df_val_pred.columns
            if c not in ("race_id", "boat_number", "prob_first")
        ]],
        cascade["stage2_model"], cascade["stage2_features"],
        cascade["stage3_model"], cascade["stage3_features"],
        pattern_2nd=cascade.get("pattern_2nd"),
        pattern_3rd=cascade.get("pattern_3rd"),
    )

    # 評価
    df_from = date.fromisoformat(args.date_from)
    df_to = date.fromisoformat(args.date_to)
    tri_payouts = load_payouts(config.DB_PATH, df_from, df_to, "trifecta")

    print("\n" + "=" * 60)
    print(" 三連単的中率 (Top-K に正解組合せが含まれる率)")
    print("=" * 60)
    print("\n  [A] Plackett-Luce")
    pl_hits = topk_hit_rate(pl_pred, tri_payouts)
    for k, v in pl_hits.items():
        print(f"    {k}: {v:.4f}" if isinstance(v, float) else f"    {k}: {v}")
    print("\n  [B] カスケード (Stage1+2+3)")
    cs_hits = topk_hit_rate(cascade_pred, tri_payouts)
    for k, v in cs_hits.items():
        print(f"    {k}: {v:.4f}" if isinstance(v, float) else f"    {k}: {v}")

    print("\n" + "=" * 60)
    print(" 三連単 fixed Top-1 ベット ROI")
    print("=" * 60)
    print("\n  [A] Plackett-Luce")
    pl_roi = fixed_top1_roi(pl_pred, tri_payouts)
    for k, v in pl_roi.items():
        print(f"    {k}: {v}")
    print("\n  [B] カスケード")
    cs_roi = fixed_top1_roi(cascade_pred, tri_payouts)
    for k, v in cs_roi.items():
        print(f"    {k}: {v}")

    print("\n" + "=" * 60)
    print(" 三連単 Top-3 分散ベット ROI (3組合せに各100円)")
    print("=" * 60)
    print("\n  [A] Plackett-Luce")
    pl_t3 = topk_split_roi(pl_pred, tri_payouts, k=3)
    for k, v in pl_t3.items():
        print(f"    {k}: {v}")
    print("\n  [B] カスケード")
    cs_t3 = topk_split_roi(cascade_pred, tri_payouts, k=3)
    for k, v in cs_t3.items():
        print(f"    {k}: {v}")


if __name__ == "__main__":
    main()
