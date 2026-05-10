"""
選手 × モーター相性 (synergy) 分析

仮説:
  整備力のある選手は弱いモーターでも実績を出す。
  → racer × motor_strength の交互作用に既存特徴で説明できない signal が残る可能性。

評価:
  各選手について、モーター強度バケット (low/mid/high の公式 top2%) ごとの residual を計算し、
  「弱モーター時 + (= 整備力ある)」「強モーター時 + (= モーター頼り)」を識別。
  そのパターンが時期をまたいで持続するかを測定。

usage:
    python -m src.evaluation.racer_motor_synergy \\
        --version v0.6 --date-from 2024-01-01 --date-to 2026-03-31 --min-n 50
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
from src.evaluation.evaluate_with_payouts import load_artifact, predict_with_probs


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--version", default="v0.6")
    p.add_argument("--date-from", required=True)
    p.add_argument("--date-to", required=True)
    p.add_argument("--min-n", type=int, default=50)
    args = p.parse_args()

    print(f"=== 選手 × モーター相性 (synergy) 分析 ===")
    print(f"  period: {args.date_from} .. {args.date_to}\n")

    artifact = load_artifact(args.version)
    df = build_training_frame(date_from=args.date_from, date_to=args.date_to)
    df_pred = predict_with_probs(artifact, df)

    sql = """
        SELECT re.race_id, re.boat_number, re.racer_number, re.racer_name,
               re.assigned_motor_number, re.assigned_motor_top_2_percent AS mtop2,
               r.stadium_number,
               res.finishing_position
          FROM race_entries re
          JOIN races r ON re.race_id = r.race_id
          JOIN race_results res ON re.race_id=res.race_id AND re.boat_number=res.boat_number
         WHERE r.race_date BETWEEN ? AND ?
           AND res.finishing_position IS NOT NULL
    """
    with db_connect(config.DB_PATH) as conn:
        meta = pd.read_sql_query(sql, conn, params=(args.date_from, args.date_to))

    df_p = df_pred[["race_id", "boat_number", "prob_first"]].merge(
        meta, on=["race_id", "boat_number"], how="inner"
    )
    df_p["actual_1st"] = (df_p["finishing_position"] == 1).astype(float)
    df_p["residual"] = df_p["actual_1st"] - df_p["prob_first"]
    df_p["yyyymm"] = df_p["race_id"].str.slice(0, 6)
    print(f"  rows: {len(df_p):,}, races: {df_p['race_id'].nunique():,}, "
          f"racers: {df_p['racer_number'].nunique():,}")

    # ===== Part 1: 選手単独残差 =====
    racer_grp = df_p.groupby(["racer_number"]).agg(
        n=("residual", "size"), pred_mean=("prob_first", "mean"),
        actual_mean=("actual_1st", "mean"), residual_mean=("residual", "mean")
    ).reset_index()
    racer_grp = racer_grp[racer_grp["n"] >= args.min_n].copy()
    print(f"\n=== 選手単独 (n>={args.min_n}, {len(racer_grp):,}名) ===")
    res = racer_grp["residual_mean"]
    print(f"  residual mean={res.mean():+.4f}  std={res.std():.4f}")
    print(f"  p10/p25/p50/p75/p90: {res.quantile(0.10):+.4f} / {res.quantile(0.25):+.4f}"
          f" / {res.quantile(0.50):+.4f} / {res.quantile(0.75):+.4f} / {res.quantile(0.90):+.4f}")

    # 持続性 (前後半)
    months = sorted(df_p["yyyymm"].unique())
    half = len(months) // 2
    g1 = df_p[df_p["yyyymm"].isin(months[:half])].groupby("racer_number").agg(
        n=("residual", "size"), residual_mean=("residual", "mean")
    ).reset_index()
    g2 = df_p[df_p["yyyymm"].isin(months[half:])].groupby("racer_number").agg(
        n=("residual", "size"), residual_mean=("residual", "mean")
    ).reset_index()
    g1 = g1[g1["n"] >= 25]; g2 = g2[g2["n"] >= 25]
    merged = g1.merge(g2, on="racer_number", suffixes=("_1", "_2"))
    if len(merged) > 50:
        c = np.corrcoef(merged["residual_mean_1"], merged["residual_mean_2"])[0, 1]
        print(f"\n  持続性 (前後相関, n={len(merged):,}): r={c:+.3f}")
        if c > 0.3:
            print(f"  → 強い持続性 (real signal)")
        elif c > 0.15:
            print(f"  → 弱-中持続性")
        else:
            print(f"  → ほぼノイズ")

    # ===== Part 2: 選手 × モーター強度バケット =====
    print(f"\n=== 選手 × モーター強度バケット (synergy) ===")
    df_p["motor_bucket"] = pd.qcut(df_p["mtop2"].fillna(35), q=3,
                                    labels=["weak", "mid", "strong"], duplicates="drop")
    print(f"  バケット別行数:")
    for b, n in df_p["motor_bucket"].value_counts().items():
        sub = df_p[df_p["motor_bucket"] == b]
        print(f"    {b}: n={n:,}, mtop2 範囲=[{sub['mtop2'].min():.1f}, {sub['mtop2'].max():.1f}]"
              f", 1着率={sub['actual_1st'].mean():.4f}")

    # 各選手の (residual_weak, residual_strong) を計算
    rg = df_p.groupby(["racer_number", "motor_bucket"], observed=True).agg(
        n=("residual", "size"), residual_mean=("residual", "mean")
    ).reset_index()
    rg_w = rg[rg["motor_bucket"] == "weak"][["racer_number", "n", "residual_mean"]].rename(
        columns={"n": "n_w", "residual_mean": "res_w"})
    rg_s = rg[rg["motor_bucket"] == "strong"][["racer_number", "n", "residual_mean"]].rename(
        columns={"n": "n_s", "residual_mean": "res_s"})
    pair = rg_w.merge(rg_s, on="racer_number", how="inner")
    pair = pair[(pair["n_w"] >= 20) & (pair["n_s"] >= 20)].copy()
    pair["motor_skill"] = pair["res_w"] - pair["res_s"]  # 弱モーター時の相対残差優位
    print(f"\n  両バケット n>=20 の選手: {len(pair):,}名")
    ms = pair["motor_skill"]
    print(f"  motor_skill (= res_weak - res_strong)")
    print(f"    mean={ms.mean():+.4f}  std={ms.std():.4f}")
    print(f"    p10={ms.quantile(0.10):+.4f}  p90={ms.quantile(0.90):+.4f}")

    # 持続性: 前後半でこの skill 指標が一致するか
    def per_racer_skill(sub: pd.DataFrame, min_n: int = 10):
        g = sub.groupby(["racer_number", "motor_bucket"], observed=True).agg(
            n=("residual", "size"), res=("residual", "mean")
        ).reset_index()
        gw = g[g["motor_bucket"] == "weak"][["racer_number", "n", "res"]].rename(
            columns={"n": "nw", "res": "rw"})
        gs = g[g["motor_bucket"] == "strong"][["racer_number", "n", "res"]].rename(
            columns={"n": "ns", "res": "rs"})
        p = gw.merge(gs, on="racer_number")
        p = p[(p["nw"] >= min_n) & (p["ns"] >= min_n)]
        p["skill"] = p["rw"] - p["rs"]
        return p[["racer_number", "skill"]]

    ps1 = per_racer_skill(df_p[df_p["yyyymm"].isin(months[:half])])
    ps2 = per_racer_skill(df_p[df_p["yyyymm"].isin(months[half:])])
    merged_skill = ps1.merge(ps2, on="racer_number", suffixes=("_1", "_2"))
    if len(merged_skill) > 30:
        c = np.corrcoef(merged_skill["skill_1"], merged_skill["skill_2"])[0, 1]
        print(f"\n  整備力 skill の持続性 (前後相関, n={len(merged_skill):,}): r={c:+.3f}")
        if c > 0.3:
            print(f"  → 強い持続性 ⇒ 真の整備力 signal あり!")
        elif c > 0.15:
            print(f"  → 弱-中持続性")
        else:
            print(f"  → ほぼノイズ (整備力は機械学習で取れない可能性)")

        top_overlap_1 = set(ps1.nlargest(30, "skill")["racer_number"])
        top_overlap_2 = set(ps2.nlargest(30, "skill")["racer_number"])
        ov = len(top_overlap_1 & top_overlap_2)
        print(f"  TOP30 重複: {ov}/30 (期待値 30/{len(merged_skill)} = {30/len(merged_skill)*30:.1f})")

    # ===== Part 3: 上位/下位 racer をリスト =====
    pair_sorted = pair.merge(racer_grp[["racer_number", "actual_mean", "n"]].rename(
        columns={"n": "n_total"}), on="racer_number", how="left")
    name_map = df_p.groupby("racer_number")["racer_name"].first().to_dict()
    pair_sorted["name"] = pair_sorted["racer_number"].map(name_map)

    print(f"\n=== motor_skill TOP 10 (弱モーター得意 = 整備力上位の可能性) ===")
    print(f"{'racer':>6}{'name':<10}{'n_w':>5}{'n_s':>5}{'res_w':>9}{'res_s':>9}{'skill':>9}")
    for _, r in pair_sorted.nlargest(10, "motor_skill").iterrows():
        print(f"{int(r['racer_number']):>6}{(r['name'] or '?')[:9]:<10}"
              f"{int(r['n_w']):>5}{int(r['n_s']):>5}"
              f"{r['res_w']:>+9.4f}{r['res_s']:>+9.4f}{r['motor_skill']:>+9.4f}")

    print(f"\n=== motor_skill BOTTOM 10 (モーター頼り) ===")
    for _, r in pair_sorted.nsmallest(10, "motor_skill").iterrows():
        print(f"{int(r['racer_number']):>6}{(r['name'] or '?')[:9]:<10}"
              f"{int(r['n_w']):>5}{int(r['n_s']):>5}"
              f"{r['res_w']:>+9.4f}{r['res_s']:>+9.4f}{r['motor_skill']:>+9.4f}")


if __name__ == "__main__":
    main()
