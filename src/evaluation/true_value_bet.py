"""
True Value Bet 戦略評価 (三連単 EV >= threshold)

予測 joint P(A→B→C) × 締切前オッズ - 1 = EV
EV が閾値を超えた組合せに 100円ずつベット → ROI を Bootstrap CI で評価。

前提:
  odds_trifecta に snapshot_label='final' のオッズが保存されていること
  cascade_pw + (optional) joint_calib_pw が学習済みであること

usage:
    python -m src.evaluation.true_value_bet \\
        --version v0.6 --pw-version pw-v0.6 \\
        --date-from 2026-04-09 --date-to 2026-05-08
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
from src.evaluation.bootstrap_ci import bootstrap_roi
from src.models.cascade import load_cascade
from src.models.cascade_per_winner import load_per_winner_cascade, predict_trifecta_per_winner
from src.models.joint_calibration import load_joint_calibrator, apply_joint_calibrator


def load_trifecta_odds(date_from: date, date_to: date,
                       snapshot_label: str = "final") -> pd.DataFrame:
    """odds_trifecta から指定スナップショットのオッズを取得"""
    sql = """
        SELECT o.race_id, o.combination, o.odds
          FROM odds_trifecta o
          JOIN races r ON o.race_id = r.race_id
         WHERE r.race_date BETWEEN ? AND ?
           AND COALESCE(o.snapshot_label, '') = ?
    """
    with db_connect(config.DB_PATH) as conn:
        df = pd.read_sql_query(sql, conn,
                               params=(date_from.isoformat(), date_to.isoformat(),
                                       snapshot_label))
    return df


def load_trifecta_payouts(date_from: date, date_to: date) -> pd.DataFrame:
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


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--version", default="v0.6")
    p.add_argument("--pw-version", default="pw-v0.6")
    p.add_argument("--cascade-version", default="cascade-v0.6")
    p.add_argument("--joint-calib-version", default="pw-v0.6",
                   help="joint_calib_<version>.pkl を読む。空文字なら較正無し")
    p.add_argument("--date-from", required=True)
    p.add_argument("--date-to", required=True)
    p.add_argument("--snapshot", default="final",
                   help="odds_trifecta.snapshot_label ('final' | '' でNULL)")
    p.add_argument("--n-iter", type=int, default=2000)
    args = p.parse_args()

    df_from = date.fromisoformat(args.date_from)
    df_to = date.fromisoformat(args.date_to)

    print(f"=== True Value Bet 評価 ===")
    print(f"  ranker:  {args.version}")
    print(f"  pw:      {args.pw_version}")
    print(f"  joint:   {args.joint_calib_version or '(none)'}")
    print(f"  period:  {args.date_from} .. {args.date_to}")
    print(f"  snap:    {args.snapshot}\n")

    # 1) モデルロード
    artifact = load_artifact(args.version)
    pw = load_per_winner_cascade(args.pw_version)
    if pw is None:
        raise FileNotFoundError(f"pw cascade not found: {args.pw_version}")
    fb = load_cascade(args.cascade_version)
    fb_s2 = fb["stage2_model"] if fb else None
    fb_f2 = fb["stage2_features"] if fb else None
    fb_s3 = fb["stage3_model"] if fb else None
    fb_f3 = fb["stage3_features"] if fb else None

    iso = None
    if args.joint_calib_version:
        iso = load_joint_calibrator(args.joint_calib_version)
        print(f"  joint calib loaded: {iso is not None}")

    # 2) データロード + 1着確率予測
    df_test = build_training_frame(date_from=args.date_from, date_to=args.date_to)
    print(f"  test races: {df_test['race_id'].nunique():,}")
    df_pred = predict_with_probs(artifact, df_test)

    # 3) 三連単 joint 予測
    print("  predicting trifecta joint probabilities ...")
    combos = predict_trifecta_per_winner(
        df_pred, pw["s2"], pw["s3"],
        fallback_s2_model=fb_s2, fallback_s2_features=fb_f2,
        fallback_s3_model=fb_s3, fallback_s3_features=fb_f3,
    )
    print(f"  predicted races: {len(combos):,}")

    if iso is not None:
        combos = apply_joint_calibrator(combos, iso, renormalize=True)

    # 4) DataFrame 化
    rows = []
    for rid, c in combos.items():
        for combo, p in c.items():
            rows.append((rid, combo, p))
    df_pred_combos = pd.DataFrame(rows, columns=["race_id", "combination", "prob"])
    print(f"  predicted combos: {len(df_pred_combos):,}")

    # 5) odds + payouts 結合
    odds = load_trifecta_odds(df_from, df_to, snapshot_label=args.snapshot)
    print(f"  odds rows ({args.snapshot}): {len(odds):,}, races: {odds['race_id'].nunique():,}")
    payouts = load_trifecta_payouts(df_from, df_to)
    payouts = payouts.rename(columns={"payout": "actual_payout"})
    print(f"  trifecta payouts races: {payouts['race_id'].nunique():,}")

    df = df_pred_combos.merge(odds, on=["race_id", "combination"], how="inner")
    df = df.merge(payouts, on=["race_id", "combination"], how="left")
    df["actual_payout"] = df["actual_payout"].fillna(0).astype(float)
    df["hit"] = (df["actual_payout"] > 0).astype(int)
    df["ev"] = df["prob"] * df["odds"] - 1.0

    print(f"\n  joined rows: {len(df):,}")
    print(f"  prob mean: {df['prob'].mean():.4f}, odds mean: {df['odds'].mean():.2f}")
    print(f"  EV distribution: <0={(df['ev']<0).mean():.3f}  >=0.05={(df['ev']>=0.05).mean():.3f}  "
          f">=0.15={(df['ev']>=0.15).mean():.3f}  >=0.30={(df['ev']>=0.30).mean():.3f}")

    # 6) 閾値別評価
    thresholds = [0.00, 0.05, 0.10, 0.15, 0.20, 0.30, 0.50, 1.00]
    print(f"\n=== EV 閾値別 Bootstrap CI 95% (n_iter={args.n_iter}) ===\n")
    print(f"{'EV>=':>6}{'n_bets':>10}{'hit':>9}{'odds_avg':>10}"
          f"{'ROI':>10}{'CI_lo':>9}{'CI_hi':>9}{'P>0':>7}")
    print("-" * 70)
    for th in thresholds:
        sub = df[df["ev"] >= th].copy()
        if len(sub) < 10:
            print(f"{th:>+6.2f}{len(sub):>10,}  too few")
            continue
        ci = bootstrap_roi(sub, n_iter=args.n_iter)
        print(f"{th:>+6.2f}{ci['n']:>10,}{sub['hit'].mean():>9.4f}"
              f"{sub['odds'].mean():>10.1f}{ci['roi_mean']:>+10.4f}"
              f"{ci['roi_lo']:>+9.3f}{ci['roi_hi']:>+9.3f}{ci['p_positive']:>7.2f}")

    # 6b) 長尾切り評価: prob >= P_MIN かつ EV >= TH
    print(f"\n=== EV+ × 最低確率フィルタ (長尾除外) ===\n")
    p_mins = [0.05, 0.10, 0.15, 0.20]
    th_evs = [0.05, 0.15, 0.30]
    print(f"{'p_min':>7}{'EV>=':>7}{'n':>9}{'hit':>9}{'odds_avg':>10}"
          f"{'ROI':>10}{'CI_lo':>9}{'CI_hi':>9}{'P>0':>7}")
    print("-" * 70)
    for pm in p_mins:
        for th in th_evs:
            sub = df[(df["ev"] >= th) & (df["prob"] >= pm)]
            if len(sub) < 30:
                print(f"{pm:>7.2f}{th:>+7.2f}{len(sub):>9}  too few")
                continue
            ci = bootstrap_roi(sub, n_iter=args.n_iter)
            print(f"{pm:>7.2f}{th:>+7.2f}{ci['n']:>9}{sub['hit'].mean():>9.4f}"
                  f"{sub['odds'].mean():>10.1f}{ci['roi_mean']:>+10.4f}"
                  f"{ci['roi_lo']:>+9.3f}{ci['roi_hi']:>+9.3f}{ci['p_positive']:>7.2f}")

    # 7) 上位 EV 例 (sanity check)
    print(f"\n=== EV 上位 15 ベット (sanity check) ===")
    top = df.nlargest(15, "ev")[["race_id", "combination", "prob", "odds", "ev",
                                  "hit", "actual_payout"]]
    print(top.to_string(index=False))


if __name__ == "__main__":
    main()
