"""
市場 implied prob vs モデル prob 比較

三連単オッズ全120組から各艇の市場推定 1着確率を逆算し、
モデル予測と乖離の大きいレースで edge を狙う。

implied_prob[艇=A] ≈ 1 / Σ_{2着,3着} odds[A→B→C] × normalize
                  → A が 1着の三連単確率を逆算 → これが市場推定 P(1着=A)

EV (model edge) = (model_prob - implied_prob) / implied_prob

各レースで「最大 edge の艇」が当たったかで ROI を見る。
"""
from __future__ import annotations

import argparse
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


def compute_market_implied_first(odds_df: pd.DataFrame) -> pd.DataFrame:
    """
    odds_df 必須列: race_id, combination ('1-2-3'), odds
    各レースの 1着艇別の market implied prob を返す。

    手法:
      - 各組合せ確率 ≈ 1 / odds (粗い近似、控除率調整なし)
      - 各 1着艇 A について: implied_first[A] = Σ over (B,C) of (1/odds[A→B→C])
      - 全艇分を合計 (~ 1/(1-控除率)) で正規化 → 概算 P(1着=A)
    """
    df = odds_df.copy()
    df["first"] = df["combination"].str.split("-").str[0].astype(int)
    df["implied_combo_prob"] = 1.0 / df["odds"]

    grouped = df.groupby(["race_id", "first"])["implied_combo_prob"].sum().reset_index()
    # レースごとに正規化
    totals = grouped.groupby("race_id")["implied_combo_prob"].sum().rename("z").reset_index()
    grouped = grouped.merge(totals, on="race_id")
    grouped["implied_first"] = grouped["implied_combo_prob"] / grouped["z"]
    grouped = grouped.rename(columns={"first": "boat_number"})
    return grouped[["race_id", "boat_number", "implied_first"]]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--version", default="v0.2")
    p.add_argument("--date-from", required=True)
    p.add_argument("--date-to", required=True)
    p.add_argument("--split-ratio", type=float, default=0.85)
    p.add_argument("--snapshot", default="final")
    args = p.parse_args()

    artifact = load_artifact(args.version)
    df_all = build_training_frame(date_from=args.date_from, date_to=args.date_to)
    _, df_val = split_time_ratio(df_all, args.split_ratio)
    df_pred = predict_with_probs(artifact, df_val)

    # オッズ取得
    df_from = date.fromisoformat(args.date_from)
    df_to = date.fromisoformat(args.date_to)
    sql = """
        SELECT o.race_id, o.combination, o.odds
          FROM odds_trifecta o
          JOIN races r ON o.race_id = r.race_id
         WHERE r.race_date BETWEEN ? AND ?
           AND o.snapshot_label = ?
    """
    with db_connect() as conn:
        odds_df = pd.read_sql_query(sql, conn, params=(df_from.isoformat(), df_to.isoformat(), args.snapshot))
    if odds_df.empty:
        print(f"No odds for snapshot={args.snapshot}")
        return
    print(f"odds rows: {len(odds_df):,}, races: {odds_df['race_id'].nunique():,}")

    # market implied
    market = compute_market_implied_first(odds_df)
    races_with_odds = set(odds_df["race_id"].unique())

    # 予測×市場 マージ
    df_pred = df_pred[df_pred["race_id"].isin(races_with_odds)].copy()
    print(f"val races with odds: {df_pred['race_id'].nunique():,}")

    merged = df_pred[["race_id", "boat_number", "prob_first"]].merge(
        market, on=["race_id", "boat_number"], how="left",
    )
    merged["edge"] = (merged["prob_first"] - merged["implied_first"]) / merged["implied_first"]
    merged["edge_abs"] = merged["prob_first"] - merged["implied_first"]

    # 単勝払戻
    win_p = load_payouts(config.DB_PATH, df_from, df_to, "win")
    win_map = dict(zip(zip(win_p["race_id"], win_p["combination"]), win_p["payout"]))

    # 各レースで「最大 edge の艇」を選ぶ
    idx = merged.groupby("race_id")["edge"].idxmax()
    selected = merged.loc[idx].copy()
    selected["actual_payout"] = selected.apply(
        lambda r: float(win_map.get((r["race_id"], str(int(r["boat_number"]))), 0)), axis=1)
    selected["hit"] = (selected["actual_payout"] > 0).astype(int)

    # edge 帯別の ROI
    print("\n" + "=" * 60)
    print(" max-edge boat 選択時のedge帯別 ROI")
    print("=" * 60)
    bins = [-1, 0, 0.10, 0.25, 0.50, 1.0, 5.0]
    labels = ["<0%", "0-10%", "10-25%", "25-50%", "50-100%", "100%+"]
    selected["edge_bin"] = pd.cut(selected["edge"], bins=bins, labels=labels)
    g = selected.groupby("edge_bin", observed=True).agg(
        n=("hit", "size"),
        hit_rate=("hit", "mean"),
        total_payout=("actual_payout", "sum"),
    ).reset_index()
    g["roi"] = (g["total_payout"] - 100 * g["n"]) / (100 * g["n"])
    print(g.to_string(index=False, float_format="%.4f"))

    # edge > 閾値 のレースだけベット
    print("\n" + "=" * 60)
    print(" 単勝 edge しきい値別の ROI (edge >= thr の艇に固定100円)")
    print("=" * 60)
    rows = []
    for thr in [0.0, 0.05, 0.10, 0.15, 0.20, 0.30, 0.50, 0.75]:
        sub = selected[selected["edge"] >= thr]
        if len(sub) == 0:
            continue
        n = len(sub)
        n_hits = int(sub["hit"].sum())
        total = float(sub["actual_payout"].sum())
        roi = (total - 100 * n) / (100 * n)
        rows.append({"edge>=": thr, "n": n, "hit_rate": n_hits / n, "roi": roi,
                     "avg_market_prob": sub["implied_first"].mean(),
                     "avg_model_prob": sub["prob_first"].mean()})
    print(pd.DataFrame(rows).to_string(index=False, float_format="%.4f"))

    # model top1 vs market top1 一致 / 不一致 の場合
    print("\n" + "=" * 60)
    print(" モデル top1 と市場 top1 の一致パターン別")
    print("=" * 60)
    # 市場 top1 (per race)
    mkt_top = market.loc[market.groupby("race_id")["implied_first"].idxmax()][["race_id", "boat_number"]].rename(columns={"boat_number": "market_top1"})
    mdl_top = df_pred.loc[df_pred.groupby("race_id")["prob_first"].idxmax()][["race_id", "boat_number"]].rename(columns={"boat_number": "model_top1"})
    cmp = mdl_top.merge(mkt_top, on="race_id")
    cmp["agree"] = cmp["model_top1"] == cmp["market_top1"]
    # actual hit (モデル top1 でベット時)
    cmp["actual_payout"] = cmp.apply(
        lambda r: float(win_map.get((r["race_id"], str(int(r["model_top1"]))), 0)), axis=1)
    cmp["hit"] = (cmp["actual_payout"] > 0).astype(int)
    g = cmp.groupby("agree").agg(
        n=("hit", "size"),
        hit_rate=("hit", "mean"),
        total_payout=("actual_payout", "sum"),
    ).reset_index()
    g["roi"] = (g["total_payout"] - 100 * g["n"]) / (100 * g["n"])
    g["pattern"] = g["agree"].map({True: "model==market top1", False: "model!=market top1 (反張り)"})
    print(g[["pattern", "n", "hit_rate", "roi"]].to_string(index=False, float_format="%.4f"))


if __name__ == "__main__":
    main()
