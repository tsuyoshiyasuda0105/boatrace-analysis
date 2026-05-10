"""
時間重み付き Stage 1 Ranker 学習

sample_weight = exp(-decay_rate × (latest_date - race_date).days)
直近に重みを集中させて、データドリフトの影響を弱める。

usage:
    python scripts/train_ranker_recency.py --date-from 2022-05-08 --date-to 2026-05-09 \\
        --split-ratio 0.85 --decay-half-life-days 180 --version v0.6
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import lightgbm as lgb
import numpy as np
import pandas as pd

import config
from src.features.builder import build_training_frame
from src.models.train import (
    FEATURE_COLS, prepare_xy, predict_probs, save_artifact, split_time_ratio,
)
from src.models.calibration import fit_calibrators, save_calibrators


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--date-from", required=True)
    p.add_argument("--date-to", required=True)
    p.add_argument("--split-ratio", type=float, default=0.85)
    p.add_argument("--decay-half-life-days", type=int, default=180,
                   help="重みが半減する日数 (180日 = 6ヶ月で半減)")
    p.add_argument("--version", default="v0.6")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    print(f"[1/4] データ {args.date_from} .. {args.date_to}")
    df = build_training_frame(date_from=args.date_from, date_to=args.date_to)
    df_train, df_val = split_time_ratio(df, args.split_ratio)
    print(f"  train: {df_train['race_id'].nunique():,} races / val: {df_val['race_id'].nunique():,} races")

    # 時間重み計算 (train_max を基準点に)
    df_train = df_train.copy()
    df_train["race_date"] = pd.to_datetime(df_train["race_date"])
    train_max = df_train["race_date"].max()
    age_days = (train_max - df_train["race_date"]).dt.days
    decay_rate = np.log(2) / args.decay_half_life_days  # 半減期から減衰率
    df_train["sample_weight"] = np.exp(-decay_rate * age_days)

    # 統計
    print(f"  half-life: {args.decay_half_life_days} days, decay rate: {decay_rate:.5f}/day")
    print(f"  weight at oldest race: {df_train['sample_weight'].min():.4f}")
    print(f"  weight at latest race: {df_train['sample_weight'].max():.4f}")
    print(f"  effective sample size: {(df_train['sample_weight'].sum() ** 2) / (df_train['sample_weight'] ** 2).sum():.0f} / {len(df_train)}")

    # 学習
    print(f"\n[2/4] LGBMRanker (sample_weight 付き)")
    X_tr, y_tr, g_tr, feature_cols, df_tr_sorted = prepare_xy(df_train)
    # prepare_xy で sort されるので weight も sort 後の順序で渡す必要
    # 一旦 race_id をキーに weight をマージ
    weights = df_train.set_index(["race_id", "boat_number"])["sample_weight"]
    weight_arr = []
    for _, r in df_tr_sorted.iterrows():
        weight_arr.append(weights.get((r["race_id"], r["boat_number"]), 1.0))
    weight_arr = np.array(weight_arr)
    # group ごとの平均 weight を group sample_weight として使う (lightgbm 仕様)
    # 実装簡略化: 各レースの 6艇は同じ weight (race_date 基準なので)

    X_va, y_va, g_va, _, _ = prepare_xy(df_val)
    X_va = X_va[feature_cols]

    model = lgb.LGBMRanker(
        objective="lambdarank",
        n_estimators=2000,
        learning_rate=0.05,
        num_leaves=63,
        min_child_samples=50,
        feature_fraction=0.9,
        bagging_fraction=0.9,
        bagging_freq=5,
        random_state=42,
        verbosity=-1,
    )
    model.fit(
        X_tr, y_tr, group=g_tr,
        sample_weight=weight_arr,
        eval_set=[(X_va, y_va)],
        eval_group=[g_va],
        callbacks=[lgb.early_stopping(50, verbose=False)],
    )
    print(f"  best iter: {model.best_iteration_}, best NDCG@1: {model.best_score_['valid_0']['ndcg@1']:.5f}")

    # 較正
    print(f"\n[3/4] Isotonic 較正")
    df_calib = df_train[df_train["race_date"] >= df_train["race_date"].quantile(0.85)]
    df_calib_pred = predict_probs(model, df_calib, feature_cols)
    try:
        calibrators = fit_calibrators(df_calib_pred)
        save_calibrators(calibrators, args.version)
    except Exception as e:
        print(f"  WARN: {e}")
        calibrators = None

    print(f"\n[4/4] 保存")
    out = save_artifact(model, feature_cols, args.version, calibrators=calibrators)
    print(f"  saved: {out}")


if __name__ == "__main__":
    main()
