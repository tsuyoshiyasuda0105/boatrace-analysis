"""
オッズ減衰率 (Discount Factor) モジュール

snapshot_label 別の odds_trifecta データから
「odds 帯域 × スナップショット間の平均減衰率」を集計。

使い方:
  - 過去レースの (T-5min, final) ペアを取得
  - 各オッズ帯 (バケット) で final/T-5min の比率の平均を取る
  - decay_factor[bucket] = mean( (final - T_5min) / T_5min )
  - 通常負の値 (オッズが下がる)

EV 計算時:
  adjusted_odds = current_odds × (1 + decay_factor[bucket(current_odds)])
  adjusted_EV   = predicted_prob × adjusted_odds - 1
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

from src.db.connection import connect as db_connect

logger = logging.getLogger(__name__)


# odds 帯域の定義 (bucket label, lower, upper)
ODDS_BUCKETS = [
    ("1-5",     1.0,    5.0),
    ("5-10",    5.0,   10.0),
    ("10-20",  10.0,   20.0),
    ("20-50",  20.0,   50.0),
    ("50-100", 50.0,  100.0),
    ("100-300", 100.0, 300.0),
    ("300-1000", 300.0, 1000.0),
    ("1000+", 1000.0, float("inf")),
]


def _bucket_for(odds: float) -> str:
    for label, lo, hi in ODDS_BUCKETS:
        if lo <= odds < hi:
            return label
    return "1000+"


def fetch_pairs(
    from_label: str = "T-5min",
    to_label: str = "final",
    db_path: Optional[str] = None,
) -> pd.DataFrame:
    """
    各 (race_id, combination) で from_label と to_label の odds を取得しペア化。
    Returns: DataFrame with columns [race_id, combination, from_odds, to_odds]
    """
    sql = """
        SELECT a.race_id, a.combination, a.odds AS from_odds, b.odds AS to_odds
          FROM odds_trifecta a
          JOIN odds_trifecta b
            ON a.race_id = b.race_id
           AND a.combination = b.combination
         WHERE a.snapshot_label = ?
           AND b.snapshot_label = ?
    """
    with db_connect(db_path) as conn:
        return pd.read_sql_query(sql, conn, params=(from_label, to_label))


def compute_decay_table(
    df: pd.DataFrame,
    min_samples: int = 30,
) -> pd.DataFrame:
    """
    odds bucket 別の平均減衰率を集計。
    Returns: DataFrame with columns [bucket, n, mean_decay, median_decay, std_decay]
    """
    if df.empty:
        return pd.DataFrame(columns=["bucket", "n", "mean_decay", "median_decay", "std_decay"])

    df = df.copy()
    df["bucket"] = df["from_odds"].apply(_bucket_for)
    df["decay"] = (df["to_odds"] - df["from_odds"]) / df["from_odds"]

    agg = df.groupby("bucket", observed=True).agg(
        n=("decay", "size"),
        mean_decay=("decay", "mean"),
        median_decay=("decay", "median"),
        std_decay=("decay", "std"),
    ).reset_index()

    # 順序を ODDS_BUCKETS で揃える
    order_map = {b[0]: i for i, b in enumerate(ODDS_BUCKETS)}
    agg["_order"] = agg["bucket"].map(order_map)
    agg = agg.sort_values("_order").drop(columns=["_order"])

    # サンプル少ないbucketは NaN にする
    agg.loc[agg["n"] < min_samples, ["mean_decay", "median_decay", "std_decay"]] = np.nan
    return agg


def adjust_odds_with_decay(
    odds: float,
    decay_table: pd.DataFrame,
) -> float:
    """
    入力 odds に bucket 別の平均 decay を適用。
    decay_table が無い bucket では入力 odds をそのまま返す (調整無し)。
    """
    bucket = _bucket_for(odds)
    row = decay_table[decay_table["bucket"] == bucket]
    if row.empty or pd.isna(row.iloc[0]["mean_decay"]):
        return odds
    decay = float(row.iloc[0]["mean_decay"])
    return odds * (1.0 + decay)


def compute_adjusted_ev(
    predicted_prob: float,
    current_odds: float,
    decay_table: Optional[pd.DataFrame] = None,
) -> float:
    """
    予測確率と現オッズから「減衰調整後 EV」を計算。
    decay_table=None なら decay 無し (= 通常 EV)。
    """
    if decay_table is None or decay_table.empty:
        return predicted_prob * current_odds - 1.0
    adj = adjust_odds_with_decay(current_odds, decay_table)
    return predicted_prob * adj - 1.0


def save_decay_table(decay_table: pd.DataFrame, db_path: Optional[str] = None) -> None:
    """decay_factor テーブルに保存 (なければ作成)"""
    with db_connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS decay_factor (
                bucket       TEXT PRIMARY KEY,
                n            INTEGER,
                mean_decay   REAL,
                median_decay REAL,
                std_decay    REAL,
                updated_at   TEXT NOT NULL
            )
        """)
        from datetime import datetime
        now = datetime.utcnow().isoformat(timespec="seconds")
        for _, r in decay_table.iterrows():
            conn.execute("""
                INSERT OR REPLACE INTO decay_factor
                    (bucket, n, mean_decay, median_decay, std_decay, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                r["bucket"], int(r["n"]) if pd.notna(r["n"]) else 0,
                float(r["mean_decay"]) if pd.notna(r["mean_decay"]) else None,
                float(r["median_decay"]) if pd.notna(r["median_decay"]) else None,
                float(r["std_decay"]) if pd.notna(r["std_decay"]) else None,
                now,
            ))
        conn.commit()


def load_decay_table(db_path: Optional[str] = None) -> pd.DataFrame:
    with db_connect(db_path) as conn:
        try:
            df = pd.read_sql_query("SELECT * FROM decay_factor", conn)
        except Exception:
            df = pd.DataFrame(columns=["bucket", "n", "mean_decay", "median_decay", "std_decay"])
    return df
