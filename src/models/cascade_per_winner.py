"""
Per-Winner カスケード (1着レーンごとに別モデル6個)

ユーザー仮説: 1着が 1/2/3/4/5/6 のどれかで 2着・3着の分布が大きく違う。
→ 1着レーンごとに独立した stage2/stage3 モデルを学習。

データ偏り対策:
  - 1着=1 は ~55% で十分なデータ
  - 1着=6 は ~2% でデータ少 → モデル不安定リスク
  - データ少ない winner では unified モデル fallback も検討

API:
  train_per_winner_cascade(df_train, df_val) -> dict
  predict_trifecta_per_winner(df_pred, models, features) -> dict
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

from src.models.cascade import (
    _build_pair_row, _build_triple_row,
    train_classifier, _silence_native_stderr,
)

logger = logging.getLogger(__name__)


# ============================================================
# Per-winner 学習データ生成
# ============================================================

def prepare_stage2_per_winner(df: pd.DataFrame) -> dict[int, pd.DataFrame]:
    """1着レーンごとに stage2 学習データを分割"""
    df = df.dropna(subset=["finishing_position"]).copy()
    df["finishing_position"] = df["finishing_position"].astype(int)
    by_winner: dict[int, list] = {w: [] for w in range(1, 7)}

    for race_id, race_df in df.groupby("race_id", sort=False):
        winners = race_df[race_df["finishing_position"] == 1]
        if winners.empty:
            continue
        winner_row = winners.iloc[0]
        wn = int(winner_row["boat_number"])
        for _, cand in race_df.iterrows():
            if int(cand["boat_number"]) == wn:
                continue
            row = _build_pair_row(cand, winner_row, pattern_2nd=None)
            row["target"] = 1 if int(cand["finishing_position"]) == 2 else 0
            row["race_id"] = race_id
            by_winner[wn].append(row)

    return {w: pd.DataFrame(rows) for w, rows in by_winner.items()}


def prepare_stage3_per_winner(df: pd.DataFrame) -> dict[int, pd.DataFrame]:
    """1着レーンごとに stage3 学習データを分割 (2着以降は通常通り)"""
    df = df.dropna(subset=["finishing_position"]).copy()
    df["finishing_position"] = df["finishing_position"].astype(int)
    by_winner: dict[int, list] = {w: [] for w in range(1, 7)}

    for race_id, race_df in df.groupby("race_id", sort=False):
        winners = race_df[race_df["finishing_position"] == 1]
        seconds = race_df[race_df["finishing_position"] == 2]
        if winners.empty or seconds.empty:
            continue
        winner_row = winners.iloc[0]
        second_row = seconds.iloc[0]
        wn = int(winner_row["boat_number"])
        sn = int(second_row["boat_number"])
        for _, cand in race_df.iterrows():
            cn = int(cand["boat_number"])
            if cn == wn or cn == sn:
                continue
            row = _build_triple_row(cand, winner_row, second_row, pattern_2nd=None, pattern_3rd=None)
            row["target"] = 1 if int(cand["finishing_position"]) == 3 else 0
            row["race_id"] = race_id
            by_winner[wn].append(row)

    return {w: pd.DataFrame(rows) for w, rows in by_winner.items()}


# ============================================================
# 学習
# ============================================================

def train_per_winner(train_dict: dict[int, pd.DataFrame],
                     val_dict: dict[int, pd.DataFrame],
                     name_prefix: str = "stage2") -> dict:
    """6モデル学習。データ300未満の winner は学習スキップ (None)"""
    out_models: dict[int, object] = {}
    out_features: dict[int, list[str]] = {}
    for w in range(1, 7):
        train_df = train_dict.get(w)
        val_df = val_dict.get(w)
        if train_df is None or len(train_df) < 300:
            logger.warning("[%s_w%d] insufficient data (n=%d), skipping",
                           name_prefix, w, len(train_df) if train_df is not None else 0)
            out_models[w] = None
            out_features[w] = []
            continue
        model, features = train_classifier(train_df, val_df, name=f"{name_prefix}_w{w}")
        out_models[w] = model
        out_features[w] = features
    return {"models": out_models, "features": out_features}


# ============================================================
# 推論
# ============================================================

def predict_trifecta_per_winner(
    df_race_with_first_probs: pd.DataFrame,
    s2_dict: dict, s3_dict: dict,
    fallback_s2_model=None, fallback_s2_features=None,
    fallback_s3_model=None, fallback_s3_features=None,
) -> dict[str, dict[str, float]]:
    """
    Per-winner モデルで joint 三連単確率を計算。
    対応モデル無い winner は fallback (unified モデル) で代替。
    """
    from itertools import permutations
    result: dict[str, dict[str, float]] = {}

    with _silence_native_stderr():
        for race_id, race_df in df_race_with_first_probs.groupby("race_id", sort=False):
            race_df = race_df.set_index("boat_number", drop=False)
            boat_nos = sorted(race_df.index.tolist())
            if len(boat_nos) < 6:
                continue
            first_probs = {b: float(race_df.loc[b, "prob_first"]) for b in boat_nos}

            # Stage 2: P(j=2着 | 1着=w)
            second_probs: dict[tuple[int, int], float] = {}
            for w in boat_nos:
                m2 = s2_dict["models"].get(w) or fallback_s2_model
                f2 = s2_dict["features"].get(w) or fallback_s2_features
                if m2 is None:
                    # 等確率で埋める
                    for c in boat_nos:
                        if c != w:
                            second_probs[(w, c)] = 1.0 / 5.0
                    continue
                cand_rows, cand_ids = [], []
                for c in boat_nos:
                    if c == w:
                        continue
                    cand_rows.append(_build_pair_row(race_df.loc[c], race_df.loc[w]))
                    cand_ids.append(c)
                X = pd.DataFrame(cand_rows).reindex(columns=f2, fill_value=np.nan)
                raw = m2.predict_proba(X)[:, 1]
                z = raw.sum() or 1e-9
                for c, p in zip(cand_ids, raw):
                    second_probs[(w, c)] = float(p / z)

            # Stage 3: P(k=3着 | 1着=w, 2着=s)
            third_probs: dict[tuple[int, int, int], float] = {}
            for w in boat_nos:
                m3 = s3_dict["models"].get(w) or fallback_s3_model
                f3 = s3_dict["features"].get(w) or fallback_s3_features
                for s in boat_nos:
                    if s == w:
                        continue
                    if m3 is None:
                        for c in boat_nos:
                            if c != w and c != s:
                                third_probs[(w, s, c)] = 1.0 / 4.0
                        continue
                    cand_rows, cand_ids = [], []
                    for c in boat_nos:
                        if c == w or c == s:
                            continue
                        cand_rows.append(_build_triple_row(race_df.loc[c], race_df.loc[w], race_df.loc[s]))
                        cand_ids.append(c)
                    X = pd.DataFrame(cand_rows).reindex(columns=f3, fill_value=np.nan)
                    raw = m3.predict_proba(X)[:, 1]
                    z = raw.sum() or 1e-9
                    for c, p in zip(cand_ids, raw):
                        third_probs[(w, s, c)] = float(p / z)

            race_combos: dict[str, float] = {}
            for w, s, t in permutations(boat_nos, 3):
                p = first_probs[w] * second_probs[(w, s)] * third_probs[(w, s, t)]
                race_combos[f"{w}-{s}-{t}"] = p
            result[race_id] = race_combos

    return result


# ============================================================
# 保存
# ============================================================

def save_per_winner_cascade(s2_dict, s3_dict, version: str):
    import pickle
    import config
    config.MODEL_DIR.mkdir(parents=True, exist_ok=True)
    out = config.MODEL_DIR / f"cascade_pw_{version}.pkl"
    with open(out, "wb") as f:
        pickle.dump({"s2": s2_dict, "s3": s3_dict, "version": version}, f)
    return out


def load_per_winner_cascade(version: str):
    import pickle
    import config
    path = config.MODEL_DIR / f"cascade_pw_{version}.pkl"
    if not path.exists():
        return None
    with open(path, "rb") as f:
        return pickle.load(f)
