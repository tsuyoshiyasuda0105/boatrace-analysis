"""
モーターの隠れた癖 (residual quirk) 分析

仮説: 公式モーター2連率や long-term 特徴で大部分捕捉できているはず。
      が、特定モーターには「既存特徴で説明できない」癖が残るかも。

評価:
  各 (stadium, motor_number) について
    residual = mean(actual_1st - model_predicted_1st)
  が systematic に外れているかを検出。

  - residual stdev が小さい → 既存特徴で十分捕捉済 (新特徴の余地少)
  - residual stdev が大きい → 残った癖あり (motor_id を特徴に入れる価値あり)

usage:
    python -m src.evaluation.motor_quirk \\
        --version v0.6 --date-from 2025-06-01 --date-to 2026-03-31
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
    p.add_argument("--min-n", type=int, default=80)
    args = p.parse_args()

    print(f"=== モーター癖 (residual) 分析 ===")
    print(f"  period: {args.date_from} .. {args.date_to}\n")

    # 予測
    artifact = load_artifact(args.version)
    df = build_training_frame(date_from=args.date_from, date_to=args.date_to)
    df_pred = predict_with_probs(artifact, df)

    # finishing_position と assigned_motor_number を取り戻す
    sql = """
        SELECT re.race_id, re.boat_number, re.assigned_motor_number,
               r.stadium_number, res.finishing_position
          FROM race_entries re
          JOIN races r ON re.race_id = r.race_id
          JOIN race_results res ON re.race_id=res.race_id AND re.boat_number=res.boat_number
         WHERE r.race_date BETWEEN ? AND ?
           AND res.finishing_position IS NOT NULL
    """
    with db_connect(config.DB_PATH) as conn:
        meta = pd.read_sql_query(sql, conn, params=(args.date_from, args.date_to))
    meta = meta.rename(columns={"assigned_motor_number": "motor_no"})

    df_p = df_pred[["race_id", "boat_number", "prob_first"]].merge(
        meta, on=["race_id", "boat_number"], how="inner"
    )
    df_p["actual_1st"] = (df_p["finishing_position"] == 1).astype(float)
    df_p["residual"] = df_p["actual_1st"] - df_p["prob_first"]
    print(f"  rows: {len(df_p):,}, races: {df_p['race_id'].nunique():,}")

    # ====== ベースライン: 全体の calibration ======
    overall_pred = df_p["prob_first"].mean()
    overall_actual = df_p["actual_1st"].mean()
    print(f"  全体: pred={overall_pred:.4f}  actual={overall_actual:.4f}  "
          f"residual={overall_actual-overall_pred:+.4f}\n")

    # ====== モーター別残差 ======
    grp = df_p.groupby(["stadium_number", "motor_no"]).agg(
        n=("residual", "size"),
        pred_mean=("prob_first", "mean"),
        actual_mean=("actual_1st", "mean"),
        residual_mean=("residual", "mean"),
        residual_std=("residual", "std"),
    ).reset_index()
    grp = grp[grp["n"] >= args.min_n].copy()
    print(f"  motors with n>={args.min_n}: {len(grp):,}")

    # 残差の全体分布
    print(f"\n=== 全モーターの residual_mean 分布 ===")
    res = grp["residual_mean"]
    print(f"  mean: {res.mean():+.4f}  std: {res.std():.4f}")
    print(f"  p10: {res.quantile(0.10):+.4f}  p25: {res.quantile(0.25):+.4f}  "
          f"p50: {res.quantile(0.50):+.4f}  p75: {res.quantile(0.75):+.4f}  "
          f"p90: {res.quantile(0.90):+.4f}")
    print(f"  min: {res.min():+.4f}  max: {res.max():+.4f}")
    print(f"  → 残差 std={res.std():.4f}  ⇒ "
          f"{'有意な癖あり' if res.std() > 0.03 else '既存特徴で大部分捕捉済'}")

    # 持続性テスト: 期間を前後半に分けて相関
    df_p["yyyymm"] = df_p["race_id"].str.slice(0, 6)
    months = sorted(df_p["yyyymm"].unique())
    half = len(months) // 2
    first_months = months[:half]
    second_months = months[half:]
    print(f"\n=== 持続性テスト ===")
    print(f"  前期間: {first_months[0]} 〜 {first_months[-1]}")
    print(f"  後期間: {second_months[0]} 〜 {second_months[-1]}")

    def per_motor_residual(sub: pd.DataFrame, min_n: int = 30):
        g = sub.groupby(["stadium_number", "motor_no"]).agg(
            n=("residual", "size"), residual_mean=("residual", "mean")
        ).reset_index()
        return g[g["n"] >= min_n]

    g1 = per_motor_residual(df_p[df_p["yyyymm"].isin(first_months)])
    g2 = per_motor_residual(df_p[df_p["yyyymm"].isin(second_months)])
    merged = g1.merge(g2, on=["stadium_number", "motor_no"], suffixes=("_1", "_2"))
    if len(merged) > 50:
        from numpy import corrcoef
        c = corrcoef(merged["residual_mean_1"], merged["residual_mean_2"])[0, 1]
        print(f"  前後相関 (n={len(merged)}): r={c:+.3f}")
        if c > 0.3:
            print(f"  → 持続性あり (癖は real signal)")
        elif c > 0.1:
            print(f"  → 弱い持続性 (一部 signal、大部分 noise)")
        else:
            print(f"  → 持続性なし (random noise が支配的)")

        # 上位/下位の overlap
        top_1 = set(zip(g1.nlargest(20, "residual_mean")["stadium_number"],
                        g1.nlargest(20, "residual_mean")["motor_no"]))
        top_2 = set(zip(g2.nlargest(20, "residual_mean")["stadium_number"],
                        g2.nlargest(20, "residual_mean")["motor_no"]))
        print(f"  TOP20 重複: {len(top_1 & top_2)} / 20")

    # ====== 癖の大きいモーター TOP/BOTTOM ======
    grp_sort = grp.sort_values("residual_mean")
    print(f"\n=== 残差が systematically POSITIVE (好機, モデル過小) ===")
    print(f"{'stadium':>8}{'motor':>7}{'n':>6}{'pred':>10}{'actual':>10}{'residual':>12}")
    for _, r in grp_sort.tail(10).iloc[::-1].iterrows():
        print(f"{int(r['stadium_number']):>8}{int(r['motor_no']):>7}{int(r['n']):>6}"
              f"{r['pred_mean']:>10.4f}{r['actual_mean']:>10.4f}{r['residual_mean']:>+12.4f}")

    print(f"\n=== 残差が systematically NEGATIVE (不機, モデル過大) ===")
    for _, r in grp_sort.head(10).iterrows():
        print(f"{int(r['stadium_number']):>8}{int(r['motor_no']):>7}{int(r['n']):>6}"
              f"{r['pred_mean']:>10.4f}{r['actual_mean']:>10.4f}{r['residual_mean']:>+12.4f}")


if __name__ == "__main__":
    main()
