"""
LightGBM による着順予測モデル

設計:
  - LGBMRanker でレース内6艇のランキングを学習
  - 1着確率は softmax 風変換 + 別途 LGBMClassifier で較正
  - Walk-Forward で時系列検証

使い方:
    python -m src.models.train --train-from 2020-01-01 --train-to 2024-12-31 --val-from 2025-01-01 --val-to 2025-06-30
"""
from __future__ import annotations

import argparse
import logging
import pickle
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

import config
from src.features.builder import build_training_frame
from src.models.calibration import fit_calibrators, save_calibrators

logger = logging.getLogger(__name__)


# 学習に使う特徴量列 (TODO: phase ごとにメタデータ化)
FEATURE_COLS = [
    "stadium_number", "race_number", "race_grade_number",
    "boat_number", "class_number", "age", "weight",
    "flying_count", "late_count", "avg_start_timing",
    "national_top_1_percent", "national_top_2_percent", "national_top_3_percent",
    "local_top_1_percent", "local_top_2_percent", "local_top_3_percent",
    "assigned_motor_top_2_percent", "assigned_motor_top_3_percent",
    "assigned_boat_top_2_percent", "assigned_boat_top_3_percent",
    "weather_number", "wind_speed", "wind_direction_number",
    "wave_height", "temperature", "water_temperature",
    "exhibition_time", "start_timing_exhibition",
    "weight_adjustment", "tilt_adjustment",
    "is_night", "altitude_high",
    "recent_10_first_rate", "recent_10_top2_rate", "recent_10_top3_rate",
    "recent_10_avg_st",
    "national_top_2_percent_rank_in_race",
    "assigned_motor_top_2_percent_rank_in_race",
    "exhibition_time_rank_in_race",
    "is_course_changed", "is_inner_course",
    # [追加] 会場×選手 (特徴1a)
    "stadium_recent_20_first_rate", "stadium_recent_20_top2_rate",
    # [追加] コース×選手 (特徴1b)
    "course_recent_30_first_rate", "course_recent_30_top2_rate",
    # [追加] モーター長期 (特徴6)
    "motor_long_50_first_rate", "motor_long_50_top2_rate", "motor_long_50_top3_rate",
    "motor_top2_diff_vs_official",
    # [追加] 天候×選手 (特徴7)
    "wind_strong_first_rate", "wave_high_first_rate", "wind_strong_first_rate_diff",
    # [v0.7] 長窓フォーム + キャリア比 (選手 alpha 捕捉)
    "recent_30_first_rate", "recent_30_top2_rate", "recent_50_first_rate",
    "recent_30_first_rate_vs_national", "recent_50_first_rate_vs_national",
    # [v0.8] 選手×会場/コース 長期 (100走) スペシャリスト指標
    "stadium_lt_100_first_rate", "stadium_lt_100_top2_rate",
    "course_lt_100_first_rate", "course_lt_100_top2_rate",
]


# ============================================================
# 学習
# ============================================================

def prepare_xy(df: pd.DataFrame):
    """LightGBM Ranker 用に X, y, group を整形"""
    df = df.copy()

    # カテゴリ列
    for c in ["water", "in_strength", "tide_effect"]:
        if c in df.columns:
            df[c] = df[c].astype("category")

    # ランキングのターゲット: 着順を逆順でスコア化 (1着=5, 2着=4 ... 6着=0)
    df["rank_score"] = (7 - df["finishing_position"].astype(int)).clip(lower=0)

    # group: race_id ごとの 6艇
    df = df.sort_values(["race_id", "boat_number"])
    groups = df.groupby("race_id").size().tolist()

    feature_cols = [c for c in FEATURE_COLS if c in df.columns]
    X = df[feature_cols]
    y = df["rank_score"]

    return X, y, groups, feature_cols, df


def train_ranker(
    df_train: pd.DataFrame,
    df_val: Optional[pd.DataFrame] = None,
):
    """LGBMRanker を学習"""
    try:
        import lightgbm as lgb
    except ImportError:
        raise RuntimeError(
            "LightGBM が必要です: pip install lightgbm scikit-learn"
        )

    X_train, y_train, g_train, feature_cols, _ = prepare_xy(df_train)

    fit_kwargs = {}
    if df_val is not None and len(df_val) > 0:
        X_val, y_val, g_val, _, _ = prepare_xy(df_val)
        # 列を揃える
        X_val = X_val[feature_cols]
        fit_kwargs["eval_set"] = [(X_val, y_val)]
        fit_kwargs["eval_group"] = [g_val]
        fit_kwargs["callbacks"] = [lgb.early_stopping(50)]

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
    )
    model.fit(X_train, y_train, group=g_train, **fit_kwargs)
    return model, feature_cols


def predict_probs(model, df: pd.DataFrame, feature_cols: list[str]) -> pd.DataFrame:
    """
    レース毎の生スコアを softmax で 1着確率に変換。
    NOTE: ここで出る「確率」は較正されていないので、別途 isotonic regression 等で較正することを強く推奨。
    """
    df = df.sort_values(["race_id", "boat_number"]).copy()
    X = df[feature_cols]
    df["raw_score"] = model.predict(X)

    def _softmax(s):
        s = s - s.max()
        e = np.exp(s)
        return e / e.sum()

    df["prob_first_uncalibrated"] = (
        df.groupby("race_id")["raw_score"].transform(_softmax)
    )
    return df


# ============================================================
# 保存
# ============================================================

def save_artifact(
    model,
    feature_cols: list[str],
    version: str,
    calibrators: Optional[dict] = None,
) -> Path:
    config.MODEL_DIR.mkdir(parents=True, exist_ok=True)
    out = config.MODEL_DIR / f"ranker_{version}.pkl"
    payload = {
        "model": model,
        "feature_cols": feature_cols,
        "version": version,
        "calibrators": calibrators,
    }
    with open(out, "wb") as f:
        pickle.dump(payload, f)
    return out


def split_time_ratio(df: pd.DataFrame, ratio: float = 0.8):
    """
    レース日付で時系列 split。
    train: 古い ratio %、val: 新しい (1-ratio) %。
    """
    if df.empty:
        return df, df
    df = df.copy()
    df["race_date"] = pd.to_datetime(df["race_date"])
    cutoff = df["race_date"].quantile(ratio, interpolation="nearest")
    df_train = df[df["race_date"] < cutoff]
    df_val = df[df["race_date"] >= cutoff]
    return df_train, df_val


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--split-mode", choices=["fixed", "time-ratio"], default="fixed",
                        help="fixed: 期間を直接指定 / time-ratio: 期間を比率分割")
    # fixed 用
    parser.add_argument("--train-from")
    parser.add_argument("--train-to")
    parser.add_argument("--val-from", default=None)
    parser.add_argument("--val-to", default=None)
    # time-ratio 用
    parser.add_argument("--date-from")
    parser.add_argument("--date-to")
    parser.add_argument("--split-ratio", type=float, default=0.8)
    parser.add_argument("--version", default=config.DEFAULT_MODEL_VERSION)
    parser.add_argument("--no-calibration", action="store_true",
                        help="確率較正をスキップ")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    print("[1/4] 学習データ読み込み...")
    if args.split_mode == "time-ratio":
        if not args.date_from or not args.date_to:
            parser.error("--split-mode time-ratio では --date-from / --date-to が必要")
        df_all = build_training_frame(date_from=args.date_from, date_to=args.date_to)
        df_train, df_val = split_time_ratio(df_all, args.split_ratio)
        print(f"      期間: {args.date_from} .. {args.date_to} (split={args.split_ratio})")
    else:
        if not args.train_from or not args.train_to:
            parser.error("--split-mode fixed では --train-from / --train-to が必要")
        df_train = build_training_frame(date_from=args.train_from, date_to=args.train_to)
        df_val = None
        if args.val_from:
            df_val = build_training_frame(date_from=args.val_from, date_to=args.val_to)

    print(f"      train: {len(df_train):,} 行 / {df_train['race_id'].nunique():,} レース")
    if df_val is not None and not df_val.empty:
        print(f"      val  : {len(df_val):,} 行 / {df_val['race_id'].nunique():,} レース")

    print("[2/4] LGBMRanker 学習...")
    model, feature_cols = train_ranker(df_train, df_val)

    calibrators = None
    if not args.no_calibration:
        print("[3/4] 確率較正 (Isotonic Regression) ...")
        # train 末尾10% で較正器を fit (val を使うとリーク)
        df_train_tail, _ = split_time_ratio(df_train, 0.9)
        df_tail_for_calib = df_train[df_train["race_date"] >= df_train_tail["race_date"].max()] \
            if not df_train_tail.empty else df_train
        df_tail_with_probs = predict_probs(model, df_tail_for_calib, feature_cols)
        try:
            calibrators = fit_calibrators(df_tail_with_probs)
            save_calibrators(calibrators, args.version)
        except Exception as e:
            print(f"      [WARN] 較正に失敗: {e}")
            calibrators = None
    else:
        print("[3/4] 較正スキップ")

    print("[4/4] 保存...")
    out = save_artifact(model, feature_cols, args.version, calibrators=calibrators)
    print(f"      saved: {out}")


if __name__ == "__main__":
    main()
