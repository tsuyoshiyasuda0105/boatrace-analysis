"""
Optuna による LGBMRanker ハイパラ最適化

目的: NDCG@1 最大化 (val 期間)
試行数: 30 trials (デフォルト)

usage:
    python scripts/optuna_tune_ranker.py --date-from 2025-05-08 --date-to 2026-05-08 \\
        --split-ratio 0.8 --n-trials 30 --version v0.3
"""
from __future__ import annotations

import argparse
import logging
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

warnings.filterwarnings("ignore")

import lightgbm as lgb
import optuna
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
    p.add_argument("--split-ratio", type=float, default=0.8)
    p.add_argument("--n-trials", type=int, default=30)
    p.add_argument("--version", default="v0.3-optuna")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    print(f"[1/4] データ {args.date_from} .. {args.date_to}")
    df = build_training_frame(date_from=args.date_from, date_to=args.date_to)
    df_train, df_val = split_time_ratio(df, args.split_ratio)
    print(f"      train: {df_train['race_id'].nunique():,} races / val: {df_val['race_id'].nunique():,} races")

    X_tr, y_tr, g_tr, feature_cols, _ = prepare_xy(df_train)
    X_va, y_va, g_va, _, _ = prepare_xy(df_val)
    X_va = X_va[feature_cols]
    print(f"      features: {len(feature_cols)}")

    print(f"\n[2/4] Optuna 探索 ({args.n_trials} trials)")

    def objective(trial: optuna.Trial) -> float:
        params = {
            "objective": "lambdarank",
            "metric": "ndcg",
            "ndcg_eval_at": [1, 2, 3],
            "n_estimators": 3000,
            "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.15, log=True),
            "num_leaves": trial.suggest_int("num_leaves", 15, 255),
            "min_child_samples": trial.suggest_int("min_child_samples", 10, 200),
            "feature_fraction": trial.suggest_float("feature_fraction", 0.5, 1.0),
            "bagging_fraction": trial.suggest_float("bagging_fraction", 0.5, 1.0),
            "bagging_freq": trial.suggest_int("bagging_freq", 1, 10),
            "lambda_l1": trial.suggest_float("lambda_l1", 1e-8, 10.0, log=True),
            "lambda_l2": trial.suggest_float("lambda_l2", 1e-8, 10.0, log=True),
            "max_depth": trial.suggest_int("max_depth", 3, 12),
            "verbosity": -1,
            "random_state": 42,
        }
        model = lgb.LGBMRanker(**params)
        model.fit(
            X_tr, y_tr, group=g_tr,
            eval_set=[(X_va, y_va)],
            eval_group=[g_va],
            callbacks=[lgb.early_stopping(40, verbose=False)],
        )
        # best ndcg@1
        if model.best_score_ and "valid_0" in model.best_score_:
            ndcg1 = model.best_score_["valid_0"].get("ndcg@1", 0.0)
            return ndcg1
        return 0.0

    study = optuna.create_study(direction="maximize",
                                 sampler=optuna.samplers.TPESampler(seed=42))
    study.optimize(objective, n_trials=args.n_trials, show_progress_bar=False)

    print(f"\n      best NDCG@1: {study.best_value:.5f}")
    print(f"      best params: {study.best_params}")

    print("\n[3/4] 最適パラで再学習 + 較正")
    best_params = dict(study.best_params)
    best_params.update({
        "objective": "lambdarank",
        "n_estimators": 3000,
        "verbosity": -1,
        "random_state": 42,
    })
    model = lgb.LGBMRanker(**best_params)
    model.fit(
        X_tr, y_tr, group=g_tr,
        eval_set=[(X_va, y_va)],
        eval_group=[g_va],
        callbacks=[lgb.early_stopping(50, verbose=False)],
    )

    # 較正
    df_train_for_calib = df_train[df_train["race_date"] >= df_train["race_date"].quantile(0.8)]
    df_calib_pred = predict_probs(model, df_train_for_calib, feature_cols)
    try:
        calibrators = fit_calibrators(df_calib_pred)
        save_calibrators(calibrators, args.version)
    except Exception as e:
        print(f"      calibration warning: {e}")
        calibrators = None

    print("\n[4/4] 保存")
    out = save_artifact(model, feature_cols, args.version, calibrators=calibrators)
    print(f"      saved: {out}")
    print(f"      best NDCG@1 reached: {study.best_value:.5f}")


if __name__ == "__main__":
    main()
