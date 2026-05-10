"""
選手 × 条件 (会場/グレード/時間帯/節日/コース等) の "得意不得意" 残差分析

目的: v0.6 に残っている選手単独 alpha (r=0.334) の中身を分解する。
      どの軸での個性が persistence を持つかを検出する。

axes:
  - stadium       : 会場 (既存 feature: stadium_recent_20)
  - race_grade    : グレード (一般/G3/G2/G1/SG)
  - race_number   : R1-12 のタイムスロット (1-4/5-8/9-12)
  - series_day    : 節何日目か
  - course_number : 進入コース (既存 feature: course_recent_30)

各軸について:
  per-(racer, axis_value) の residual mean
  前期/後期に分けて持続性 r を測定
  → r > 0.15 なら新特徴量化候補

usage:
    python -m src.evaluation.racer_condition_specialty \\
        --version v0.6 --date-from 2024-01-01 --date-to 2026-03-31 --min-n 30
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


def persistence_test(df: pd.DataFrame, axis_col: str, min_n_per_cell: int = 20) -> dict:
    """前後半に分けて (racer, axis_col) ごとの residual の相関を測る"""
    months = sorted(df["yyyymm"].unique())
    half = len(months) // 2

    def per_cell(sub):
        g = sub.groupby(["racer_number", axis_col], observed=True).agg(
            n=("residual", "size"), residual_mean=("residual", "mean")
        ).reset_index()
        return g[g["n"] >= min_n_per_cell]

    g1 = per_cell(df[df["yyyymm"].isin(months[:half])])
    g2 = per_cell(df[df["yyyymm"].isin(months[half:])])
    merged = g1.merge(g2, on=["racer_number", axis_col], suffixes=("_1", "_2"))

    if len(merged) < 30:
        return {"axis": axis_col, "n_cells": len(merged), "r": None,
                "msg": "too few cells"}

    r = float(np.corrcoef(merged["residual_mean_1"], merged["residual_mean_2"])[0, 1])
    res_std = df.groupby(["racer_number", axis_col], observed=True)["residual"].mean().std()

    return {
        "axis": axis_col,
        "n_cells_paired": len(merged),
        "residual_std": float(res_std),
        "persistence_r": r,
        "interpretation": (
            "強い persistence" if r > 0.3 else
            "弱-中 persistence" if r > 0.15 else
            "ノイズ"
        )
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--version", default="v0.6")
    p.add_argument("--date-from", required=True)
    p.add_argument("--date-to", required=True)
    p.add_argument("--min-n", type=int, default=30)
    args = p.parse_args()

    print(f"=== 選手 × 条件 残差持続性 分析 ===")
    print(f"  period: {args.date_from} .. {args.date_to}\n")

    # 予測
    artifact = load_artifact(args.version)
    df = build_training_frame(date_from=args.date_from, date_to=args.date_to)
    df_pred = predict_with_probs(artifact, df)

    # 条件列を取り戻す
    sql = """
        SELECT re.race_id, re.boat_number, re.racer_number,
               re.branch_number,
               r.stadium_number, r.race_number, r.race_grade_number,
               r.series_day,
               res.finishing_position, res.course_number
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
    print(f"  rows: {len(df_p):,}, racers: {df_p['racer_number'].nunique():,}")

    # 軸別の bucket 化
    df_p["race_number_bucket"] = pd.cut(df_p["race_number"],
                                         bins=[0, 4, 8, 12],
                                         labels=["AM (1-4)", "Mid (5-8)", "PM (9-12)"])
    df_p["series_day_bucket"] = pd.cut(df_p["series_day"].fillna(1),
                                        bins=[0, 2, 4, 99],
                                        labels=["1-2日目", "3-4日目", "5日目以降"])
    df_p["race_grade_bucket"] = df_p["race_grade_number"].fillna(1).astype(int)

    print(f"\n=== 軸別 (racer × axis) の残差持続性テスト ===")
    print(f"  各 cell: 前期 n>={args.min_n} かつ後期 n>={args.min_n} の選手×軸ペア\n")
    print(f"{'axis':<22}{'n_cells':>10}{'res_std':>10}{'r':>8}{'判定':>20}")
    print("-" * 72)

    axes_to_test = [
        ("stadium_number", "会場"),
        ("course_number", "進入コース"),
        ("race_number_bucket", "時間帯 (R1-4/5-8/9-12)"),
        ("series_day_bucket", "節日 (1-2/3-4/5-)"),
        ("race_grade_bucket", "レースグレード"),
        ("branch_number", "支部"),
    ]
    results = []
    for col, label in axes_to_test:
        if col not in df_p.columns:
            continue
        sub = df_p.dropna(subset=[col])
        if len(sub) < 1000:
            continue
        r = persistence_test(sub, col, min_n_per_cell=args.min_n)
        results.append((label, r))
        n = r.get("n_cells_paired", 0)
        rstd = r.get("residual_std", 0) or 0
        rr = r.get("persistence_r")
        rr_str = f"{rr:+.3f}" if rr is not None else "—"
        interp = r.get("interpretation", r.get("msg", ""))
        print(f"{label:<22}{n:>10,}{rstd:>10.4f}{rr_str:>8}{interp:>20}")

    print(f"\n=== 参考: 選手単独 (axis なし) の持続性 ===")
    g1 = df_p[df_p["yyyymm"].isin(sorted(df_p['yyyymm'].unique())[:len(df_p['yyyymm'].unique())//2])].groupby("racer_number").agg(n=("residual", "size"), res=("residual", "mean")).reset_index()
    g2 = df_p[df_p["yyyymm"].isin(sorted(df_p['yyyymm'].unique())[len(df_p['yyyymm'].unique())//2:])].groupby("racer_number").agg(n=("residual", "size"), res=("residual", "mean")).reset_index()
    g1 = g1[g1["n"] >= args.min_n]
    g2 = g2[g2["n"] >= args.min_n]
    m = g1.merge(g2, on="racer_number", suffixes=("_1", "_2"))
    if len(m) > 50:
        c = float(np.corrcoef(m["res_1"], m["res_2"])[0, 1])
        print(f"  選手のみ (n_cells={len(m):,}): r={c:+.3f}")

    # 最も persistence の高い軸の中身を見る
    print(f"\n=== 最良の軸の residual TOP/BOTTOM サンプル ===")
    best = max([r for _, r in results if r.get("persistence_r") is not None],
               key=lambda r: r["persistence_r"], default=None)
    if best:
        col = best["axis"]
        print(f"  軸: {col} (r={best['persistence_r']:+.3f})")
        gtbl = df_p.groupby(["racer_number", col], observed=True).agg(
            n=("residual", "size"),
            pred_mean=("prob_first", "mean"),
            actual_mean=("actual_1st", "mean"),
            res=("residual", "mean")
        ).reset_index()
        gtbl = gtbl[gtbl["n"] >= 50]
        # 名前を貼る
        nm = df_p.groupby("racer_number")["racer_number"].first().to_dict()
        # 簡易 name lookup
        with db_connect(config.DB_PATH) as conn:
            names = pd.read_sql_query(
                "SELECT DISTINCT racer_number, racer_name FROM race_entries", conn
            )
        nm2 = dict(zip(names["racer_number"], names["racer_name"]))
        gtbl["name"] = gtbl["racer_number"].map(nm2)

        print(f"\n  TOP 10 (条件×選手 残差>0):")
        for _, r in gtbl.nlargest(10, "res").iterrows():
            print(f"    racer={int(r['racer_number'])} {r.get('name', '?')[:8]:<8}"
                  f"  {col}={r[col]}  n={int(r['n'])}  pred={r['pred_mean']:.3f}"
                  f"  actual={r['actual_mean']:.3f}  res={r['res']:+.4f}")
        print(f"\n  BOTTOM 10:")
        for _, r in gtbl.nsmallest(10, "res").iterrows():
            print(f"    racer={int(r['racer_number'])} {r.get('name', '?')[:8]:<8}"
                  f"  {col}={r[col]}  n={int(r['n'])}  pred={r['pred_mean']:.3f}"
                  f"  actual={r['actual_mean']:.3f}  res={r['res']:+.4f}")


if __name__ == "__main__":
    main()
