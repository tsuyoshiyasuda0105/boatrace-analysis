"""
条件付きカスケードモデル

P(i,j,k) = P(i=1着) × P(j=2着 | 1着=i) × P(k=3着 | 1着=i, 2着=j)

3段階構成:
  Stage 1: 既存 LGBMRanker (prob_first 出力済 v0.x モデルを再利用)
  Stage 2: LGBMClassifier — 候補艇 j が 2着になる確率 (1着 i を条件)
  Stage 3: LGBMClassifier — 候補艇 k が 3着になる確率 (1着 i, 2着 j を条件)

学習データ生成:
  - Stage 2: 各レース × 候補艇 (winner 以外の5艇)
    target = 1 if 候補艇 finishes 2nd else 0
    features = 候補艇の特徴量 + winner の特徴量 + diff
  - Stage 3: 各レース × 候補艇 (winner と 2nd 以外の4艇)
    target = 1 if 候補艇 finishes 3rd else 0

推論:
  for race in races:
    P_first[i] = stage1.predict(race)[i]
    for w in 1..6:
      for c in {1..6} - {w}:
        P_second_raw[(w,c)] = stage2.predict(features(c, w))
      # 5候補で正規化
      P_second[(w, c)] = P_second_raw[(w, c)] / sum(P_second_raw[(w, *)])
    similarly stage 3
    P[i,j,k] = P_first[i] * P_second[(i,j)] * P_third[(i,j,k)]
"""
from __future__ import annotations

import contextlib
import logging
import os
import pickle
import sys
from itertools import permutations
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

import config

logger = logging.getLogger(__name__)


@contextlib.contextmanager
def _silence_native_stderr():
    """C 拡張 (LightGBM 等) からの stdout/stderr を OS レベルで抑制"""
    fds = (sys.stdout.fileno(), sys.stderr.fileno())
    saved = [os.dup(fd) for fd in fds]
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        for fd in fds:
            os.dup2(devnull, fd)
        yield
    finally:
        for fd, sv in zip(fds, saved):
            os.dup2(sv, fd)
            os.close(sv)
        os.close(devnull)


# ============================================================
# Stage2 / Stage3 用の対戦特徴量を作成
# ============================================================

# 候補艇 (cand) と "勝ち目" (winner / second) で複製したい列
PAIR_NUM_COLS = [
    "boat_number", "class_number", "age", "weight",
    "national_top_1_percent", "national_top_2_percent", "national_top_3_percent",
    "local_top_1_percent", "local_top_2_percent", "local_top_3_percent",
    "assigned_motor_top_2_percent", "assigned_boat_top_2_percent",
    "exhibition_time", "start_timing_exhibition",
    "weight_adjustment", "tilt_adjustment",
    "recent_10_first_rate", "recent_10_top2_rate",
    "stadium_recent_20_first_rate", "course_recent_30_first_rate",
    "motor_long_50_first_rate",
]

# レース全体に共通の (一度だけ取れば良い) 列
RACE_LEVEL_COLS = [
    "stadium_number", "race_number", "race_grade_number",
    "weather_number", "wind_speed", "wind_direction_number",
    "wave_height", "temperature", "water_temperature",
    "is_night", "altitude_high",
]


def _build_pair_row(cand_row: pd.Series, winner_row: pd.Series,
                    pattern_2nd: Optional[dict] = None) -> dict:
    out = {}
    # 候補艇
    for c in PAIR_NUM_COLS:
        out[f"cand_{c}"] = cand_row.get(c)
    # 勝ち目 (1着確定として与えられる)
    for c in PAIR_NUM_COLS:
        out[f"win_{c}"] = winner_row.get(c)
    # diff (候補 - 勝ち目)
    for c in [
        "national_top_2_percent", "local_top_2_percent",
        "assigned_motor_top_2_percent", "exhibition_time",
        "start_timing_exhibition", "recent_10_first_rate",
    ]:
        if c in cand_row.index and c in winner_row.index:
            try:
                out[f"diff_{c}"] = float(cand_row[c]) - float(winner_row[c])
            except (TypeError, ValueError):
                out[f"diff_{c}"] = None
    # 勝ち目 boat_number one-hot
    win_no = int(winner_row["boat_number"])
    for i in range(1, 7):
        out[f"winner_is_{i}"] = int(win_no == i)
    cand_no = int(cand_row["boat_number"])
    # レース共通
    for c in RACE_LEVEL_COLS:
        out[c] = cand_row.get(c)
    # 経験的パターン: P(候補が 2着 | 1着 = winner @ stadium)
    if pattern_2nd is not None:
        from src.models.pattern_features import lookup_2nd
        stadium = int(cand_row.get("stadium_number") or 0)
        out["cond_2nd_rate"] = lookup_2nd(pattern_2nd, stadium, win_no, cand_no)
    return out


def _build_triple_row(cand_row: pd.Series, winner_row: pd.Series, second_row: pd.Series,
                      pattern_2nd: Optional[dict] = None,
                      pattern_3rd: Optional[dict] = None) -> dict:
    out = _build_pair_row(cand_row, winner_row, pattern_2nd=pattern_2nd)
    # 2着の特徴量も追加
    for c in PAIR_NUM_COLS:
        out[f"sec_{c}"] = second_row.get(c)
    # 候補 vs 2着 の diff
    for c in [
        "national_top_2_percent", "local_top_2_percent",
        "assigned_motor_top_2_percent", "exhibition_time",
    ]:
        if c in cand_row.index and c in second_row.index:
            try:
                out[f"diff_sec_{c}"] = float(cand_row[c]) - float(second_row[c])
            except (TypeError, ValueError):
                out[f"diff_sec_{c}"] = None
    # 2着 one-hot
    sec_no = int(second_row["boat_number"])
    for i in range(1, 7):
        out[f"second_is_{i}"] = int(sec_no == i)
    # 経験的パターン: P(候補が 3着 | 1着 = winner, 2着 = second @ stadium)
    if pattern_3rd is not None:
        from src.models.pattern_features import lookup_3rd
        stadium = int(cand_row.get("stadium_number") or 0)
        win_no = int(winner_row["boat_number"])
        cand_no = int(cand_row["boat_number"])
        out["cond_3rd_rate"] = lookup_3rd(pattern_3rd, stadium, win_no, sec_no, cand_no)
    return out


# ============================================================
# 学習データ生成
# ============================================================

def prepare_stage2_data(df: pd.DataFrame, pattern_2nd: Optional[dict] = None) -> pd.DataFrame:
    """
    各レース × 5 (winner以外) の候補艇行を作成。
    target = 1 if 候補艇 finishes 2着 else 0
    """
    rows = []
    df = df.dropna(subset=["finishing_position"]).copy()
    df["finishing_position"] = df["finishing_position"].astype(int)

    for race_id, race_df in df.groupby("race_id", sort=False):
        winners = race_df[race_df["finishing_position"] == 1]
        if winners.empty:
            continue
        winner_row = winners.iloc[0]
        for _, cand in race_df.iterrows():
            if int(cand["boat_number"]) == int(winner_row["boat_number"]):
                continue
            row = _build_pair_row(cand, winner_row, pattern_2nd=pattern_2nd)
            row["target"] = 1 if int(cand["finishing_position"]) == 2 else 0
            row["race_id"] = race_id
            rows.append(row)
    return pd.DataFrame(rows)


def prepare_stage3_data(df: pd.DataFrame,
                        pattern_2nd: Optional[dict] = None,
                        pattern_3rd: Optional[dict] = None) -> pd.DataFrame:
    """
    各レース × 4 (winner と 2着以外) の候補艇行を作成。
    target = 1 if 候補艇 finishes 3着 else 0
    """
    rows = []
    df = df.dropna(subset=["finishing_position"]).copy()
    df["finishing_position"] = df["finishing_position"].astype(int)

    for race_id, race_df in df.groupby("race_id", sort=False):
        winners = race_df[race_df["finishing_position"] == 1]
        seconds = race_df[race_df["finishing_position"] == 2]
        if winners.empty or seconds.empty:
            continue
        winner_row = winners.iloc[0]
        second_row = seconds.iloc[0]
        for _, cand in race_df.iterrows():
            cn = int(cand["boat_number"])
            if cn == int(winner_row["boat_number"]) or cn == int(second_row["boat_number"]):
                continue
            row = _build_triple_row(cand, winner_row, second_row,
                                    pattern_2nd=pattern_2nd, pattern_3rd=pattern_3rd)
            row["target"] = 1 if int(cand["finishing_position"]) == 3 else 0
            row["race_id"] = race_id
            rows.append(row)
    return pd.DataFrame(rows)


# ============================================================
# 学習
# ============================================================

def train_classifier(df_train: pd.DataFrame, df_val: Optional[pd.DataFrame] = None,
                     name: str = "stage2"):
    import lightgbm as lgb
    feature_cols = [c for c in df_train.columns if c not in ("target", "race_id")]
    X_train = df_train[feature_cols]
    y_train = df_train["target"]

    fit_kwargs = {}
    if df_val is not None and len(df_val) > 0:
        X_val = df_val[feature_cols]
        y_val = df_val["target"]
        fit_kwargs["eval_set"] = [(X_val, y_val)]
        fit_kwargs["callbacks"] = [lgb.early_stopping(50)]

    model = lgb.LGBMClassifier(
        objective="binary",
        n_estimators=2000,
        learning_rate=0.05,
        num_leaves=63,
        min_child_samples=50,
        feature_fraction=0.9,
        bagging_fraction=0.9,
        bagging_freq=5,
        random_state=42,
    )
    model.fit(X_train, y_train, **fit_kwargs)
    logger.info("[%s] trained, n=%d, positive_rate=%.4f",
                name, len(df_train), y_train.mean())
    return model, feature_cols


# ============================================================
# 推論: joint trifecta probability
# ============================================================

def predict_trifecta_joint(
    df_race_with_first_probs: pd.DataFrame,
    stage2_model,
    stage2_features: list[str],
    stage3_model,
    stage3_features: list[str],
    pattern_2nd: Optional[dict] = None,
    pattern_3rd: Optional[dict] = None,
) -> dict[str, dict[str, float]]:
    """
    df_race_with_first_probs: 1レース×6艇、prob_first 列必須。

    Returns: {race_id: {combination: probability}}
    """
    result: dict[str, dict[str, float]] = {}

    with _silence_native_stderr():
        for race_id, race_df in df_race_with_first_probs.groupby("race_id", sort=False):
            race_df = race_df.set_index("boat_number", drop=False)
            boat_nos = sorted(race_df.index.tolist())
            if len(boat_nos) < 6:
                continue

            first_probs = {b: float(race_df.loc[b, "prob_first"]) for b in boat_nos}

            # ---- Stage 2: P(j=2着 | 1着=w) — 5候補を softmax 風に正規化
            second_probs: dict[tuple[int, int], float] = {}
            for w in boat_nos:
                cand_rows = []
                cand_ids = []
                for c in boat_nos:
                    if c == w:
                        continue
                    row = _build_pair_row(race_df.loc[c], race_df.loc[w], pattern_2nd=pattern_2nd)
                    cand_rows.append(row)
                    cand_ids.append(c)
                X = pd.DataFrame(cand_rows).reindex(columns=stage2_features, fill_value=np.nan)
                raw = stage2_model.predict_proba(X)[:, 1]
                z = raw.sum()
                if z <= 0:
                    z = 1e-9
                for c, p in zip(cand_ids, raw):
                    second_probs[(w, c)] = float(p / z)

            # ---- Stage 3: P(k=3着 | 1着=w, 2着=s)
            third_probs: dict[tuple[int, int, int], float] = {}
            for w in boat_nos:
                for s in boat_nos:
                    if s == w:
                        continue
                    cand_rows = []
                    cand_ids = []
                    for c in boat_nos:
                        if c == w or c == s:
                            continue
                        row = _build_triple_row(race_df.loc[c], race_df.loc[w], race_df.loc[s],
                                                pattern_2nd=pattern_2nd, pattern_3rd=pattern_3rd)
                        cand_rows.append(row)
                        cand_ids.append(c)
                    X = pd.DataFrame(cand_rows).reindex(columns=stage3_features, fill_value=np.nan)
                    raw = stage3_model.predict_proba(X)[:, 1]
                    z = raw.sum()
                    if z <= 0:
                        z = 1e-9
                    for c, p in zip(cand_ids, raw):
                        third_probs[(w, s, c)] = float(p / z)

            # ---- Joint
            race_combos: dict[str, float] = {}
            for w, s, t in permutations(boat_nos, 3):
                p = first_probs[w] * second_probs[(w, s)] * third_probs[(w, s, t)]
                race_combos[f"{w}-{s}-{t}"] = p
            result[race_id] = race_combos

    return result


# ============================================================
# 保存・ロード
# ============================================================

def save_cascade(stage2_model, stage2_features, stage3_model, stage3_features, version: str,
                 pattern_2nd: Optional[dict] = None, pattern_3rd: Optional[dict] = None):
    config.MODEL_DIR.mkdir(parents=True, exist_ok=True)
    out = config.MODEL_DIR / f"cascade_{version}.pkl"
    with open(out, "wb") as f:
        pickle.dump({
            "stage2_model": stage2_model,
            "stage2_features": stage2_features,
            "stage3_model": stage3_model,
            "stage3_features": stage3_features,
            "pattern_2nd": pattern_2nd,
            "pattern_3rd": pattern_3rd,
            "version": version,
        }, f)
    return out


def load_cascade(version: str) -> Optional[dict]:
    path = config.MODEL_DIR / f"cascade_{version}.pkl"
    if not path.exists():
        return None
    with open(path, "rb") as f:
        return pickle.load(f)
