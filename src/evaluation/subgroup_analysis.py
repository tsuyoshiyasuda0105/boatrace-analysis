"""
サブグループ別 ROI 分析

「全体は赤字でも特定ゾーンは +EV」を見つけるための網羅集計。

軸:
  - 会場 (24)
  - グレード (SG/G1/G2/G3/一般)
  - 1号艇 強度 (prob_first 帯)
  - レース時間帯 (午前/午後/ナイター)
  - 天候・風 (強風 / 通常)
  - 進入崩れ有無
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
from src.models.train import split_time_ratio
from src.evaluation.evaluate_with_payouts import load_artifact, predict_with_probs, load_payouts


def _stadium_name_map():
    import json
    with open(config.MASTER_DIR / "stadiums.json", encoding="utf-8") as f:
        data = json.load(f)
    return {int(k): v["name"] for k, v in data.items() if isinstance(v, dict) and "name" in v}


def annotate_predictions(df_pred: pd.DataFrame) -> pd.DataFrame:
    """各レース行に top1 予測艇とその確率を追加"""
    df = df_pred.copy()
    top1 = df.loc[df.groupby("race_id")["prob_first"].idxmax(), ["race_id", "boat_number", "prob_first"]]
    top1 = top1.rename(columns={"boat_number": "top1_boat", "prob_first": "top1_prob"})
    return df.merge(top1, on="race_id", how="left")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--version", default="v0.2")
    p.add_argument("--date-from", required=True)
    p.add_argument("--date-to", required=True)
    p.add_argument("--split-ratio", type=float, default=0.85)
    args = p.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")

    print(f"[load] artifact v{args.version}")
    artifact = load_artifact(args.version)

    print(f"[data] {args.date_from} .. {args.date_to}, val={1-args.split_ratio:.0%}")
    df_all = build_training_frame(date_from=args.date_from, date_to=args.date_to)
    _, df_val = split_time_ratio(df_all, args.split_ratio)
    print(f"  val races: {df_val['race_id'].nunique():,}")

    df_pred = predict_with_probs(artifact, df_val)
    df_pred = annotate_predictions(df_pred)

    # 単勝払戻
    df_from = date.fromisoformat(args.date_from)
    df_to = date.fromisoformat(args.date_to)
    win_p = load_payouts(config.DB_PATH, df_from, df_to, "win")
    win_map = dict(zip(zip(win_p["race_id"], win_p["combination"]), win_p["payout"]))

    # レース単位サマリ (top1 予測 + payout)
    rows = []
    for race_id, grp in df_pred.groupby("race_id"):
        first_row = grp.iloc[0]
        top1 = int(first_row["top1_boat"])
        prob = float(first_row["top1_prob"])
        actual_payout = float(win_map.get((race_id, str(top1)), 0))
        hit = 1 if actual_payout > 0 else 0
        rows.append({
            "race_id": race_id,
            "race_date": first_row["race_date"],
            "stadium_number": int(first_row["stadium_number"]),
            "race_grade_number": first_row.get("race_grade_number"),
            "race_number": int(first_row["race_number"]),
            "wind_speed": first_row.get("wind_speed"),
            "wave_height": first_row.get("wave_height"),
            "is_night": first_row.get("is_night"),
            "top1_boat": top1,
            "top1_prob": prob,
            "actual_payout": actual_payout,
            "hit": hit,
        })
    df_r = pd.DataFrame(rows)
    print(f"\n  total races: {len(df_r):,}, overall hit_rate: {df_r['hit'].mean():.4f}")
    flat_roi = (df_r['hit'] * df_r['actual_payout']).sum() / (100 * len(df_r)) - 1
    print(f"  overall fixed-top1 ROI: {flat_roi:+.4f}")

    stadium_names = _stadium_name_map()

    def report(label: str, agg: pd.DataFrame, sort_col="roi"):
        print(f"\n{'=' * 60}")
        print(f" {label}")
        print(f"{'=' * 60}")
        print(agg.sort_values(sort_col, ascending=False).to_string(index=False, float_format="%.4f"))

    # 1. 会場別
    g = df_r.groupby("stadium_number").agg(
        n=("hit", "size"),
        hit_rate=("hit", "mean"),
        avg_payout=("actual_payout", lambda x: (x[x > 0].mean() if (x > 0).any() else 0)),
        total_payout=("actual_payout", "sum"),
    ).reset_index()
    g["roi"] = (g["total_payout"] - 100 * g["n"]) / (100 * g["n"])
    g["stadium"] = g["stadium_number"].map(stadium_names)
    g = g[g["n"] >= 50][["stadium_number", "stadium", "n", "hit_rate", "avg_payout", "roi"]]
    report("会場別 単勝 fixed-top1 ROI (n>=50)", g)

    # 2. グレード別
    g_map = {1: "SG", 2: "G1", 3: "G2", 4: "G3", 5: "一般"}
    df_r["grade"] = df_r["race_grade_number"].map(g_map).fillna("不明")
    g = df_r.groupby("grade").agg(
        n=("hit", "size"),
        hit_rate=("hit", "mean"),
        total_payout=("actual_payout", "sum"),
    ).reset_index()
    g["roi"] = (g["total_payout"] - 100 * g["n"]) / (100 * g["n"])
    report("グレード別 ROI", g[g["n"] >= 30][["grade", "n", "hit_rate", "roi"]])

    # 3. 1号艇強度 (top1_boat==1 のときの top1_prob で分類)
    sub = df_r[df_r["top1_boat"] == 1].copy()
    bins = [0, 0.30, 0.50, 0.70, 1.0]
    labels = ["<30%", "30-50%", "50-70%", "70%+"]
    sub["bin"] = pd.cut(sub["top1_prob"], bins=bins, labels=labels, include_lowest=True)
    g = sub.groupby("bin", observed=True).agg(
        n=("hit", "size"),
        hit_rate=("hit", "mean"),
        total_payout=("actual_payout", "sum"),
    ).reset_index()
    g["roi"] = (g["total_payout"] - 100 * g["n"]) / (100 * g["n"])
    report("1号艇予測確率帯 ROI (1号艇予測時のみ)", g[g["n"] >= 30][["bin", "n", "hit_rate", "roi"]], sort_col="bin")

    # 4. 強風時
    df_r["wind_band"] = pd.cut(df_r["wind_speed"].fillna(0), bins=[-0.1, 2, 4, 6, 30], labels=["~2", "3-4", "5-6", "7+"])
    g = df_r.groupby("wind_band", observed=True).agg(
        n=("hit", "size"),
        hit_rate=("hit", "mean"),
        total_payout=("actual_payout", "sum"),
    ).reset_index()
    g["roi"] = (g["total_payout"] - 100 * g["n"]) / (100 * g["n"])
    report("風速帯別 ROI (m/s)", g[g["n"] >= 30][["wind_band", "n", "hit_rate", "roi"]], sort_col="wind_band")

    # 5. ナイター vs デイ
    g = df_r.groupby("is_night").agg(
        n=("hit", "size"),
        hit_rate=("hit", "mean"),
        total_payout=("actual_payout", "sum"),
    ).reset_index()
    g["roi"] = (g["total_payout"] - 100 * g["n"]) / (100 * g["n"])
    g["session"] = g["is_night"].map({0: "デイ", 1: "ナイター"})
    report("デイ/ナイター ROI", g[["session", "n", "hit_rate", "roi"]])

    # 6. 1号艇強度×会場 (ベスト10)
    sub2 = df_r[df_r["top1_boat"] == 1].copy()
    sub2["bin"] = pd.cut(sub2["top1_prob"], bins=bins, labels=labels, include_lowest=True)
    g = sub2.groupby(["stadium_number", "bin"], observed=True).agg(
        n=("hit", "size"),
        hit_rate=("hit", "mean"),
        total_payout=("actual_payout", "sum"),
    ).reset_index()
    g["roi"] = (g["total_payout"] - 100 * g["n"]) / (100 * g["n"])
    g["stadium"] = g["stadium_number"].map(stadium_names)
    g = g[g["n"] >= 30].sort_values("roi", ascending=False).head(10)
    report("会場×1号艇強度 ROI Top-10", g[["stadium", "bin", "n", "hit_rate", "roi"]])


if __name__ == "__main__":
    main()
