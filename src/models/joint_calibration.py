"""
三連単 Joint 確率の Isotonic Calibration

cascade のJoint(120組)確率は raw のままだと長い目を過大評価する。
学習データで「予測 prob → 実 hit_freq」のマッピングを fit し、
推論時に適用する。
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


def build_calibration_data(
    predicted_combos: dict[str, dict[str, float]],
    payouts: dict[str, tuple[str, float]],
) -> pd.DataFrame:
    """
    各 (race, combo) に対して predicted_prob と target (1=winning combo, 0=else) のペアを構築。
    """
    rows = []
    for race_id, combos in predicted_combos.items():
        if race_id not in payouts:
            continue
        winning_combo = payouts[race_id][0]
        for comb, prob in combos.items():
            rows.append({
                "race_id": race_id,
                "combination": comb,
                "predicted_prob": float(prob),
                "target": 1 if comb == winning_combo else 0,
            })
    return pd.DataFrame(rows)


def fit_joint_calibrator(df_calib: pd.DataFrame):
    """
    Isotonic Regression で predicted_prob → calibrated_prob を fit。
    """
    from sklearn.isotonic import IsotonicRegression
    x = df_calib["predicted_prob"].to_numpy()
    y = df_calib["target"].to_numpy()
    iso = IsotonicRegression(out_of_bounds="clip", y_min=1e-6, y_max=0.999)
    iso.fit(x, y)
    # 全体 sanity check
    bins = np.linspace(0, x.max(), 11)
    binned = pd.cut(x, bins, include_lowest=True)
    sanity = pd.DataFrame({"x": x, "y": y, "bin": binned}).groupby("bin", observed=True).agg(
        n=("y", "size"), pred_mean=("x", "mean"), actual_freq=("y", "mean")
    )
    logger.info("calibration sanity:\n%s", sanity.to_string())
    return iso, sanity


def apply_joint_calibrator(
    predicted_combos: dict[str, dict[str, float]],
    iso,
    renormalize: bool = True,
) -> dict[str, dict[str, float]]:
    """
    各レースの 120 組合せに較正を適用。renormalize=True なら sum=1 に正規化。
    """
    out: dict[str, dict[str, float]] = {}
    for race_id, combos in predicted_combos.items():
        keys = list(combos.keys())
        probs = np.array([combos[k] for k in keys])
        cal = iso.predict(probs)
        if renormalize:
            z = cal.sum() or 1e-9
            cal = cal / z
        out[race_id] = {k: float(p) for k, p in zip(keys, cal)}
    return out


def save_joint_calibrator(iso, version: str) -> Path:
    config.MODEL_DIR.mkdir(parents=True, exist_ok=True)
    out = config.MODEL_DIR / f"joint_calib_{version}.pkl"
    with open(out, "wb") as f:
        pickle.dump(iso, f)
    return out


def load_joint_calibrator(version: str):
    path = config.MODEL_DIR / f"joint_calib_{version}.pkl"
    if not path.exists():
        return None
    with open(path, "rb") as f:
        return pickle.load(f)
