"""
本格的な Value Bet 評価 (Layer 3 オッズ + カスケード予測)

実運用想定:
  - 各レースで全120組合せの「予測確率」と「市場オッズ」を取得
  - EV = predicted_prob × market_odds - 1
  - EV >= threshold (例: 0.0 or 0.15) のみベット
  - hit したか・実払戻はいくらかで実 ROI を計算

評価指標:
  - n_bets: 期間内の総ベット数
  - n_hits / hit_rate: 当たった数
  - flat_roi: フラット100円ベット時 ROI
  - kelly_roi: Kelly基準ベット時 ROI
  - sharpe / max_drawdown
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
from src.evaluation.evaluate_with_payouts import load_artifact, predict_with_probs
from src.evaluation.value_bet import kelly_fraction
from src.models.cascade import predict_trifecta_joint, load_cascade
from src.models.cascade_per_winner import predict_trifecta_per_winner, load_per_winner_cascade


def load_full_odds(db_path: str, date_from: date, date_to: date) -> pd.DataFrame:
    """odds_trifecta から全レース全組合せのオッズを取得"""
    sql = """
        SELECT o.race_id, o.combination, o.odds, o.is_final, o.recorded_at
          FROM odds_trifecta o
          JOIN races r ON o.race_id = r.race_id
         WHERE r.race_date BETWEEN ? AND ?
    """
    with db_connect(db_path) as conn:
        df = pd.read_sql_query(sql, conn, params=(date_from.isoformat(), date_to.isoformat()))
    if df.empty:
        return df
    # 各 (race_id, combo) で is_final=1 → 最新 recorded_at を採用
    df = df.sort_values(["race_id", "combination", "is_final", "recorded_at"],
                        ascending=[True, True, False, False])
    df = df.drop_duplicates(subset=["race_id", "combination"], keep="first")
    return df[["race_id", "combination", "odds"]].reset_index(drop=True)


def load_payouts_trifecta(db_path: str, date_from: date, date_to: date) -> dict[str, tuple[str, float]]:
    """{race_id: (winning_combination, payout)}"""
    sql = """
        SELECT p.race_id, p.combination, p.payout
          FROM race_payouts p
          JOIN races r ON p.race_id = r.race_id
         WHERE p.bet_type = 'trifecta'
           AND r.race_date BETWEEN ? AND ?
    """
    with db_connect(db_path) as conn:
        df = pd.read_sql_query(sql, conn, params=(date_from.isoformat(), date_to.isoformat()))
    return {r["race_id"]: (r["combination"], float(r["payout"])) for _, r in df.iterrows()}


def find_and_evaluate_value_bets(
    predicted_combos: dict[str, dict[str, float]],
    odds_df: pd.DataFrame,
    payouts: dict[str, tuple[str, float]],
    ev_threshold: float = 0.0,
    bet_amount: float = 100.0,
    max_bets_per_race: int = 5,
    min_prob: float = 0.01,         # 1% 未満の長い目はノイズなので除外
    max_odds: float = 200.0,        # 200倍超えの大穴は当てに行かない
    top_k_filter: int = 20,         # 各レースで予測 top-K のみ EV 検討
) -> dict:
    """
    予測×オッズ で EV>=threshold のベットを抽出 + 実 ROI 計算
    """
    odds_lookup: dict[tuple[str, str], float] = {
        (r["race_id"], r["combination"]): float(r["odds"])
        for _, r in odds_df.iterrows()
    }
    bets = []
    for race_id, combos in predicted_combos.items():
        race_bets = []
        # 予測確率上位 top_k_filter のみ検討
        sorted_combos = sorted(combos.items(), key=lambda x: -x[1])[:top_k_filter]
        for comb, prob in sorted_combos:
            if prob < min_prob:
                continue
            o = odds_lookup.get((race_id, comb))
            if o is None or o < 1.0 or o > max_odds:
                continue
            ev = prob * o - 1.0
            if ev < ev_threshold:
                continue
            kf = kelly_fraction(prob, o, fraction=0.25)
            race_bets.append({
                "race_id": race_id,
                "combination": comb,
                "predicted_prob": prob,
                "market_odds": o,
                "ev": ev,
                "kelly_fraction": kf,
            })
        # レース内では EV 高い順に max_bets_per_race だけ採用
        race_bets.sort(key=lambda x: -x["ev"])
        bets.extend(race_bets[:max_bets_per_race])

    if not bets:
        return {"n_bets": 0, "warning": "no value bets found"}

    bets_df = pd.DataFrame(bets)
    bets_df["race_date"] = pd.to_datetime(bets_df["race_id"].str[:8], format="%Y%m%d")

    # 当たり判定
    actual_hit = []
    actual_payout = []
    for _, b in bets_df.iterrows():
        win = payouts.get(b["race_id"])
        if win and win[0] == b["combination"]:
            actual_hit.append(1)
            actual_payout.append(win[1])
        else:
            actual_hit.append(0)
            actual_payout.append(0.0)
    bets_df["actual_hit"] = actual_hit
    bets_df["actual_payout"] = actual_payout

    flat_stake = bet_amount * len(bets_df)
    flat_payout = (bets_df["actual_hit"] * bets_df["actual_payout"]).sum()

    # Kelly ベット (1/4 Kelly)
    kelly_stakes = bet_amount * bets_df["kelly_fraction"] * 4
    kelly_total_stake = kelly_stakes.sum()
    kelly_payouts = bets_df["actual_hit"] * bets_df["actual_payout"] * (kelly_stakes / bet_amount)
    kelly_total_payout = kelly_payouts.sum()

    # 日次 PnL
    daily_pnl = bets_df.groupby("race_date").apply(
        lambda g: (g["actual_hit"] * g["actual_payout"]).sum() - bet_amount * len(g)
    )
    if len(daily_pnl) > 1 and daily_pnl.std() > 0:
        sharpe = float(daily_pnl.mean() / daily_pnl.std() * np.sqrt(252))
    else:
        sharpe = 0.0
    cum = daily_pnl.cumsum()
    max_dd = float((cum - cum.cummax()).min()) if len(cum) > 0 else 0.0

    return {
        "n_bets": int(len(bets_df)),
        "n_hits": int(bets_df["actual_hit"].sum()),
        "hit_rate": float(bets_df["actual_hit"].mean()),
        "flat_stake": float(flat_stake),
        "flat_payout": float(flat_payout),
        "flat_roi": float((flat_payout - flat_stake) / flat_stake) if flat_stake else 0.0,
        "kelly_stake": float(kelly_total_stake),
        "kelly_payout": float(kelly_total_payout),
        "kelly_roi": float((kelly_total_payout - kelly_total_stake) / kelly_total_stake) if kelly_total_stake else 0.0,
        "avg_ev_predicted": float(bets_df["ev"].mean()),
        "avg_odds": float(bets_df["market_odds"].mean()),
        "sharpe_daily": sharpe,
        "max_drawdown": max_dd,
        "n_dates": int(len(daily_pnl)),
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="v0.2")
    p.add_argument("--cascade", default=None)
    p.add_argument("--per-winner", default="pw-v0.1")
    p.add_argument("--date-from", required=True)
    p.add_argument("--date-to", required=True)
    p.add_argument("--ev-threshold", type=float, default=0.0)
    p.add_argument("--max-bets-per-race", type=int, default=5)
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    print(f"[load] base={args.base} per_winner={args.per_winner}")
    artifact = load_artifact(args.base)
    pw = load_per_winner_cascade(args.per_winner)
    fallback = load_cascade(args.cascade) if args.cascade else None

    print(f"[data] {args.date_from} .. {args.date_to}")
    df = build_training_frame(date_from=args.date_from, date_to=args.date_to)

    df_from = date.fromisoformat(args.date_from)
    df_to = date.fromisoformat(args.date_to)
    odds_df = load_full_odds(config.DB_PATH, df_from, df_to)
    payouts = load_payouts_trifecta(config.DB_PATH, df_from, df_to)
    print(f"      odds rows: {len(odds_df):,}  races with odds: {odds_df['race_id'].nunique():,}")
    print(f"      trifecta payouts: {len(payouts):,}")

    if odds_df.empty:
        print("[ERROR] no odds data found. Layer 3 odds collection required.")
        return

    # オッズが取れているレースだけに絞る
    races_with_odds = set(odds_df["race_id"].unique())
    df = df[df["race_id"].isin(races_with_odds)]
    print(f"      target races (with odds): {df['race_id'].nunique():,}")

    print("[predict] stage1 + cascade per-winner")
    df_pred = predict_with_probs(artifact, df)
    pw_pred = predict_trifecta_per_winner(
        df_pred, pw["s2"], pw["s3"],
        fallback_s2_model=fallback["stage2_model"] if fallback else None,
        fallback_s2_features=fallback["stage2_features"] if fallback else None,
        fallback_s3_model=fallback["stage3_model"] if fallback else None,
        fallback_s3_features=fallback["stage3_features"] if fallback else None,
    )

    print("\n" + "=" * 60)
    print(f" Value Bet 評価 (filter: top-20 予測, prob>=1%, odds<=200)")
    print("=" * 60)
    for thr in [0.0, 0.05, 0.10, 0.15, 0.25]:
        r = find_and_evaluate_value_bets(
            pw_pred, odds_df, payouts,
            ev_threshold=thr, max_bets_per_race=args.max_bets_per_race,
            min_prob=0.01, max_odds=200.0, top_k_filter=20,
        )
        print(f"\n  [EV >= {thr:+.2f}]")
        for k, v in r.items():
            if isinstance(v, float):
                print(f"    {k}: {v:+.4f}")
            else:
                print(f"    {k}: {v}")


if __name__ == "__main__":
    main()
