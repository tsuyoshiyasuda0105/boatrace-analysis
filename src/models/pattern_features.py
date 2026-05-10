"""
カスケード用の経験的「条件付きパターン」ルックアップ

(stadium, winner_lane) ごとに過去の 2着分布、
(stadium, winner_lane, second_lane) ごとに 3着分布を集計。

これを Stage 2/3 の特徴量に加味することで、
「1着が2レーンの時は2着が3レーンになりやすい」等の
会場固有パターンをモデルに直接与える。

リーク防止: build_*_pattern() は学習用 df だけから集計する想定。
推論時は同じテーブルを参照する。
"""
from __future__ import annotations

from collections import Counter
from typing import Optional

import pandas as pd


# 経験的パターンが少ないセルのデフォルト (フォールバック)
DEFAULT_2ND_RATE = 1.0 / 5.0   # 5候補のうち均等
DEFAULT_3RD_RATE = 1.0 / 4.0


def build_pattern_2nd(df: pd.DataFrame) -> dict[tuple[int, int, int], float]:
    """
    {(stadium, winner_lane, candidate_lane): empirical P(2着 = candidate_lane | 1着 = winner_lane @ stadium)}
    """
    counts: Counter = Counter()    # (stadium, w, cand) → 観測 2着数
    totals: Counter = Counter()    # (stadium, w, cand) → 候補としての出現数

    df = df.dropna(subset=["finishing_position"]).copy()
    df["finishing_position"] = df["finishing_position"].astype(int)

    for race_id, race_df in df.groupby("race_id", sort=False):
        winners = race_df[race_df.finishing_position == 1]
        seconds = race_df[race_df.finishing_position == 2]
        if winners.empty or seconds.empty:
            continue
        w_row = winners.iloc[0]
        s_row = seconds.iloc[0]
        winner_lane = int(w_row["boat_number"])
        second_lane = int(s_row["boat_number"])
        stadium = int(w_row["stadium_number"])
        for cand in range(1, 7):
            if cand == winner_lane:
                continue
            key = (stadium, winner_lane, cand)
            totals[key] += 1
            if cand == second_lane:
                counts[key] += 1

    out: dict[tuple[int, int, int], float] = {}
    for k, n in totals.items():
        if n >= 5:  # 5走以上の場合のみ採用 (ノイズ抑制)
            out[k] = counts[k] / n
    return out


def build_pattern_3rd(df: pd.DataFrame) -> dict[tuple[int, int, int, int], float]:
    """
    {(stadium, winner_lane, second_lane, candidate_lane): P(3着 = cand | 1着, 2着, stadium)}
    """
    counts: Counter = Counter()
    totals: Counter = Counter()

    df = df.dropna(subset=["finishing_position"]).copy()
    df["finishing_position"] = df["finishing_position"].astype(int)

    for race_id, race_df in df.groupby("race_id", sort=False):
        winners = race_df[race_df.finishing_position == 1]
        seconds = race_df[race_df.finishing_position == 2]
        thirds = race_df[race_df.finishing_position == 3]
        if winners.empty or seconds.empty or thirds.empty:
            continue
        w = int(winners.iloc[0]["boat_number"])
        s = int(seconds.iloc[0]["boat_number"])
        t = int(thirds.iloc[0]["boat_number"])
        stadium = int(winners.iloc[0]["stadium_number"])
        for cand in range(1, 7):
            if cand == w or cand == s:
                continue
            key = (stadium, w, s, cand)
            totals[key] += 1
            if cand == t:
                counts[key] += 1

    out: dict[tuple[int, int, int, int], float] = {}
    for k, n in totals.items():
        if n >= 3:  # 3走以上 (3着は条件きついので緩め)
            out[k] = counts[k] / n
    return out


def lookup_2nd(pattern: dict, stadium: int, winner: int, candidate: int) -> float:
    return pattern.get((stadium, winner, candidate), DEFAULT_2ND_RATE)


def lookup_3rd(pattern: dict, stadium: int, winner: int, second: int, candidate: int) -> float:
    return pattern.get((stadium, winner, second, candidate), DEFAULT_3RD_RATE)
