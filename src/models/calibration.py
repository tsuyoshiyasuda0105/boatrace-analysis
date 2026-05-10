"""
確率較正 (Probability Calibration)

LightGBM Ranker の生スコア → softmax の「確率らしきもの」は
真の頻度と乖離する。EV 計算が前提なので Isotonic Regression で較正する。

入力 DataFrame は train.predict_probs() の出力を想定:
  - race_id, boat_number, prob_first_uncalibrated, finishing_position

3つの較正器を作る:
  - first  : 1着確率
  - top_2  : 2着以内確率
  - top_3  : 3着以内確率

実装ノート:
  - Ranker 単体では top_2/top_3 確率が直接出ないため、
    raw_score の累積和 / softmax 和で近似する
  - Isotonic は単調を保つので EV 計算と相性が良い
  - 較正器の fit に val/test を使うとリークするので train 末尾で fit
"""
from __future__ import annotations

import logging
import pickle
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

import config

logger = logging.getLogger(__name__)


def _softmax_top_k(group: pd.DataFrame, k: int) -> pd.Series:
    """
    各艇について「自分が上位k位以内に入る確率」を softmax の累積で近似。
    Plackett-Luce ベースの近似。
    """
    s = group["raw_score"].to_numpy(dtype=float)
    s = s - s.max()
    p = np.exp(s)
    p = p / p.sum()
    # P(top-k) ≒ 1 - (1 - p)^k  (粗い近似だが単調性は維持)
    out = 1.0 - np.power(1.0 - p, k)
    return pd.Series(out, index=group.index)


def add_top_k_uncalibrated(df: pd.DataFrame) -> pd.DataFrame:
    """raw_score 列から prob_top_2_uncalibrated / prob_top_3_uncalibrated を生成"""
    df = df.copy()
    if "raw_score" not in df.columns:
        raise ValueError("df must have 'raw_score' column (call predict_probs first)")
    df["prob_top_2_uncalibrated"] = 0.0
    df["prob_top_3_uncalibrated"] = 0.0
    for race_id, group in df.groupby("race_id", sort=False):
        top2 = _softmax_top_k(group, 2)
        top3 = _softmax_top_k(group, 3)
        df.loc[group.index, "prob_top_2_uncalibrated"] = top2.values
        df.loc[group.index, "prob_top_3_uncalibrated"] = top3.values
    return df


def fit_calibrators(df: pd.DataFrame) -> dict:
    """
    df 必須列: prob_first_uncalibrated, finishing_position, raw_score, race_id, boat_number

    Returns:
      {"first": IsotonicRegression, "top_2": ..., "top_3": ...}
    """
    try:
        from sklearn.isotonic import IsotonicRegression
    except ImportError:
        raise RuntimeError("scikit-learn が必要です: pip install scikit-learn")

    df = add_top_k_uncalibrated(df)
    df = df.dropna(subset=["finishing_position"]).copy()
    df["finishing_position"] = df["finishing_position"].astype(int)

    targets = {
        "first": ("prob_first_uncalibrated", df["finishing_position"] == 1),
        "top_2": ("prob_top_2_uncalibrated", df["finishing_position"] <= 2),
        "top_3": ("prob_top_3_uncalibrated", df["finishing_position"] <= 3),
    }

    calibrators: dict = {}
    for name, (col, y_bool) in targets.items():
        x = df[col].to_numpy(dtype=float)
        y = y_bool.astype(int).to_numpy()
        ir = IsotonicRegression(out_of_bounds="clip", y_min=1e-4, y_max=1 - 1e-4)
        ir.fit(x, y)
        calibrators[name] = ir
        logger.info(
            "calibrator %s: n=%d positives=%d mean_uncal=%.3f mean_actual=%.3f",
            name, len(x), int(y.sum()), x.mean(), y.mean(),
        )
    return calibrators


def apply_calibrators(df: pd.DataFrame, calibrators: dict) -> pd.DataFrame:
    """較正器を当てて prob_first / prob_top_2 / prob_top_3 列を追加"""
    df = add_top_k_uncalibrated(df)
    if "first" in calibrators:
        df["prob_first"] = calibrators["first"].predict(df["prob_first_uncalibrated"].to_numpy())
    if "top_2" in calibrators:
        df["prob_top_2"] = calibrators["top_2"].predict(df["prob_top_2_uncalibrated"].to_numpy())
    if "top_3" in calibrators:
        df["prob_top_3"] = calibrators["top_3"].predict(df["prob_top_3_uncalibrated"].to_numpy())
    return df


def save_calibrators(calibrators: dict, version: str) -> Path:
    config.MODEL_DIR.mkdir(parents=True, exist_ok=True)
    out = config.MODEL_DIR / f"calibrators_{version}.pkl"
    with open(out, "wb") as f:
        pickle.dump(calibrators, f)
    return out


def load_calibrators(version: str) -> Optional[dict]:
    path = config.MODEL_DIR / f"calibrators_{version}.pkl"
    if not path.exists():
        return None
    with open(path, "rb") as f:
        return pickle.load(f)
