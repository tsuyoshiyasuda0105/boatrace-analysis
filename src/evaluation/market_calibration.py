"""
市場 (final odds) vs モデル (v0.6 joint P) 較正比較

目的: 市場が「集合知」として我々のモデルより正確かを確認。
  - 市場が正確 → market-augmented model に改善余地あり
  - 同程度 → drift 系戦略も期待薄

評価:
  - Brier Score / Log Loss
  - 確率帯別 calibration plot
  - 大穴領域での過大評価度

usage:
    python -m src.evaluation.market_calibration \\
        --version v0.6 --pw-version pw-v0.6 \\
        --date-from 2026-04-28 --date-to 2026-05-08
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
from src.models.cascade import load_cascade
from src.models.cascade_per_winner import load_per_winner_cascade, predict_trifecta_per_winner
from src.models.joint_calibration import load_joint_calibrator, apply_joint_calibrator


def load_final_odds(date_from: date, date_to: date) -> pd.DataFrame:
    sql = """
        SELECT o.race_id, o.combination, o.odds
          FROM odds_trifecta o
          JOIN races r ON o.race_id = r.race_id
         WHERE r.race_date BETWEEN ? AND ?
           AND o.snapshot_label = 'final'
    """
    with db_connect(config.DB_PATH) as conn:
        return pd.read_sql_query(sql, conn,
                                 params=(date_from.isoformat(), date_to.isoformat()))


def load_payouts(date_from: date, date_to: date) -> pd.DataFrame:
    sql = """
        SELECT p.race_id, p.combination, p.payout
          FROM race_payouts p
          JOIN races r ON p.race_id = r.race_id
         WHERE p.bet_type = 'trifecta'
           AND r.race_date BETWEEN ? AND ?
    """
    with db_connect(config.DB_PATH) as conn:
        return pd.read_sql_query(sql, conn,
                                 params=(date_from.isoformat(), date_to.isoformat()))


def brier_score(p, y):
    return float(np.mean((p - y) ** 2))


def log_loss_clip(p, y):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--version", default="v0.6")
    p.add_argument("--pw-version", default="pw-v0.6")
    p.add_argument("--cascade-version", default="cascade-v0.6")
    p.add_argument("--joint-calib-version", default="pw-v0.6")
    p.add_argument("--date-from", required=True)
    p.add_argument("--date-to", required=True)
    args = p.parse_args()

    df_from = date.fromisoformat(args.date_from)
    df_to = date.fromisoformat(args.date_to)

    print(f"=== 市場 vs モデル 較正比較 ===")
    print(f"  period: {args.date_from} .. {args.date_to}\n")

    artifact = load_artifact(args.version)
    pw = load_per_winner_cascade(args.pw_version)
    fb = load_cascade(args.cascade_version)
    fb_s2 = fb["stage2_model"] if fb else None
    fb_f2 = fb["stage2_features"] if fb else None
    fb_s3 = fb["stage3_model"] if fb else None
    fb_f3 = fb["stage3_features"] if fb else None

    iso = load_joint_calibrator(args.joint_calib_version) if args.joint_calib_version else None

    df_test = build_training_frame(date_from=args.date_from, date_to=args.date_to)
    print(f"  races: {df_test['race_id'].nunique():,}")
    df_pred = predict_with_probs(artifact, df_test)

    print("  predicting trifecta joint ...")
    combos = predict_trifecta_per_winner(
        df_pred, pw["s2"], pw["s3"],
        fallback_s2_model=fb_s2, fallback_s2_features=fb_f2,
        fallback_s3_model=fb_s3, fallback_s3_features=fb_f3,
    )
    if iso is not None:
        combos = apply_joint_calibrator(combos, iso, renormalize=True)

    rows = []
    for rid, c in combos.items():
        for combo, p in c.items():
            rows.append((rid, combo, p))
    df_model = pd.DataFrame(rows, columns=["race_id", "combination", "model_p"])

    odds = load_final_odds(df_from, df_to)
    odds["raw_p"] = 1.0 / odds["odds"]
    z = odds.groupby("race_id")["raw_p"].transform("sum")
    odds["market_p"] = odds["raw_p"] / z

    print(f"  odds rows: {len(odds):,}, races: {odds['race_id'].nunique():,}")

    payouts = load_payouts(df_from, df_to)
    win_set = set(zip(payouts["race_id"], payouts["combination"]))

    df = df_model.merge(odds[["race_id", "combination", "odds", "market_p"]],
                         on=["race_id", "combination"], how="inner")
    df["hit"] = [1 if (r, c) in win_set else 0 for r, c in zip(df["race_id"], df["combination"])]
    print(f"  joined rows: {len(df):,}\n")

    # 4) 全体精度
    print(f"=== 全組合せ精度 (n={len(df):,}, 平均1着率=1/120≈0.83%) ===")
    print(f"{'metric':<14}{'model':>12}{'market':>12}{'勝者':>8}")
    print("-" * 48)
    bm = brier_score(df["model_p"].values, df["hit"].values)
    bk = brier_score(df["market_p"].values, df["hit"].values)
    print(f"{'Brier':<14}{bm:>12.6f}{bk:>12.6f}{('market' if bk<bm else 'model'):>8}")
    lm = log_loss_clip(df["model_p"].values, df["hit"].values)
    lk = log_loss_clip(df["market_p"].values, df["hit"].values)
    print(f"{'LogLoss':<14}{lm:>12.6f}{lk:>12.6f}{('market' if lk<lm else 'model'):>8}")

    # 5) 確率帯別 calibration
    print(f"\n=== 確率帯別 calibration (絶対誤差小さい方が正確) ===\n")
    bins = [0.0, 0.005, 0.01, 0.02, 0.05, 0.10, 0.20, 0.40, 1.00]
    for label, col in [("model", "model_p"), ("market", "market_p")]:
        print(f"--- {label} ---")
        df["_bin"] = pd.cut(df[col], bins=bins, include_lowest=True)
        agg = df.groupby("_bin", observed=True).agg(
            n=("hit", "size"), hit_rate=("hit", "mean"),
            pred_mean=(col, "mean")).reset_index()
        agg["err"] = agg["pred_mean"] - agg["hit_rate"]
        for _, r in agg.iterrows():
            n = r["n"]
            sign = "+" if r["err"] >= 0 else ""
            note = " ← 過大" if r["err"] > 0.005 else (" ← 過小" if r["err"] < -0.005 else "")
            print(f"  {str(r['_bin']):<22} n={n:>6,}  pred={r['pred_mean']:.4f}  "
                  f"actual={r['hit_rate']:.4f}  err={sign}{r['err']:.4f}{note}")
        print()

    # 6) 大穴/本命領域
    print(f"=== オッズ帯別 比較 ===")
    for lo, hi, label in [(0, 10, "本命 (1-10倍)"),
                           (10, 30, "中穴 (10-30倍)"),
                           (30, 100, "穴 (30-100倍)"),
                           (100, 1e6, "大穴 (100倍+)")]:
        sub = df[(df["odds"] >= lo) & (df["odds"] < hi)]
        if len(sub) == 0:
            continue
        print(f"\n  {label}: n={len(sub):,}")
        actual = sub["hit"].mean()
        m_p = sub["model_p"].mean()
        k_p = sub["market_p"].mean()
        print(f"    actual hit_rate: {actual:.4f}")
        print(f"    model_p mean:    {m_p:.4f}  ratio={m_p/max(actual,1e-9):.2f}x")
        print(f"    market_p mean:   {k_p:.4f}  ratio={k_p/max(actual,1e-9):.2f}x")

    # 7) 同じ組合せに対するモデル vs 市場の実際の合致度
    print(f"\n=== モデルと市場の予測が乖離する組合せの結果 ===")
    df["log_ratio"] = np.log(df["model_p"].clip(1e-6) / df["market_p"].clip(1e-6))
    print(f"  log(model_p / market_p) 分布:")
    print(f"    mean: {df['log_ratio'].mean():+.3f}")
    print(f"    p10/p50/p90: {df['log_ratio'].quantile(0.10):+.2f} / "
          f"{df['log_ratio'].quantile(0.50):+.2f} / "
          f"{df['log_ratio'].quantile(0.90):+.2f}")

    # 大幅にモデルが強気の組合せ (model >> market) の hit rate
    extr_model = df.nlargest(int(len(df) * 0.01), "log_ratio")  # 上位1%
    extr_market = df.nsmallest(int(len(df) * 0.01), "log_ratio")  # 下位1%
    print(f"\n  モデル超強気 top 1% (model >> market):")
    print(f"    n={len(extr_model):,}  hit_rate={extr_model['hit'].mean():.4f}")
    print(f"    model_p mean={extr_model['model_p'].mean():.4f}  "
          f"market_p mean={extr_model['market_p'].mean():.4f}")
    print(f"  市場超強気 top 1% (market >> model):")
    print(f"    n={len(extr_market):,}  hit_rate={extr_market['hit'].mean():.4f}")
    print(f"    model_p mean={extr_market['model_p'].mean():.4f}  "
          f"market_p mean={extr_market['market_p'].mean():.4f}")


if __name__ == "__main__":
    main()
