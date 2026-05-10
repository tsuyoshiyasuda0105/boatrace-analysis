"""
バックテスト

時系列データの正しい検証は Walk-Forward Validation。
通常の cross validation は確実にリークするので使わない。

評価指標:
  - 的中率 (参考程度)
  - ROI = (合計払戻 - 合計賭け金) / 合計賭け金
  - Sharpe ratio (安定性指標)
  - 最大ドローダウン

戦略:
  - --strategy single-split: 期間を1回だけ time-ratio で分割 (データが少ない時)
  - --strategy walk-forward: train_days/val_days で複数窓 (本来の仕様)

使い方:
    python -m src.evaluation.backtest --strategy single-split \\
        --start 2025-05-08 --end 2026-05-07 --split-ratio 0.8

    python -m src.evaluation.backtest --strategy walk-forward \\
        --start 2024-01-01 --end 2025-12-31 --train-days 180 --val-days 30
"""
from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

import config
from src.db.connection import connect as db_connect
from src.features.builder import build_training_frame
from src.models.train import train_ranker, predict_probs
from src.models.calibration import fit_calibrators, apply_calibrators
from src.evaluation.value_bet import find_value_bets_trifecta

logger = logging.getLogger(__name__)


# ============================================================
# Walk-Forward 窓生成
# ============================================================

def walk_forward_splits(
    start: date,
    end: date,
    train_days: int = 365,
    val_days: int = 30,
):
    cur = start
    while cur + timedelta(days=train_days + val_days) <= end:
        train_from = cur
        train_to = cur + timedelta(days=train_days - 1)
        val_from = train_to + timedelta(days=1)
        val_to = val_from + timedelta(days=val_days - 1)
        yield (train_from, train_to, val_from, val_to)
        cur = val_from


# ============================================================
# DB ロード
# ============================================================

def load_trifecta_odds(db_path: str, date_from: date, date_to: date) -> pd.DataFrame:
    """
    is_final=1 のオッズを優先。同 race_id に確定オッズが無い場合は recorded_at 最新を採用。
    """
    sql = """
        SELECT o.race_id, o.combination, o.odds, o.is_final, o.recorded_at, r.race_date
          FROM odds_trifecta o
          JOIN races r ON o.race_id = r.race_id
         WHERE r.race_date BETWEEN ? AND ?
    """
    with db_connect(db_path) as conn:
        df = pd.read_sql_query(sql, conn, params=(date_from.isoformat(), date_to.isoformat()))
    if df.empty:
        return df
    # 各 (race_id, combination) で is_final=1 を優先、次に recorded_at 最新
    df = df.sort_values(["race_id", "combination", "is_final", "recorded_at"],
                        ascending=[True, True, False, False])
    df = df.drop_duplicates(subset=["race_id", "combination"], keep="first")
    return df[["race_id", "combination", "odds"]].reset_index(drop=True)


def load_trifecta_payouts(db_path: str, date_from: date, date_to: date) -> pd.DataFrame:
    sql = """
        SELECT p.race_id, p.combination, p.payout
          FROM race_payouts p
          JOIN races r ON p.race_id = r.race_id
         WHERE p.bet_type = 'trifecta'
           AND r.race_date BETWEEN ? AND ?
    """
    with db_connect(db_path) as conn:
        return pd.read_sql_query(sql, conn, params=(date_from.isoformat(), date_to.isoformat()))


# ============================================================
# 予測 → ベット
# ============================================================

def build_boat_probs_dict(df: pd.DataFrame) -> dict:
    """
    df 必須列: race_id, boat_number, prob_first, prob_top_2, prob_top_3
    Returns: { race_id: { boat_number: {prob_first, prob_top_2, prob_top_3} } }
    """
    out: dict[str, dict[int, dict[str, float]]] = {}
    needed = ["prob_first", "prob_top_2", "prob_top_3"]
    for col in needed:
        if col not in df.columns:
            raise ValueError(f"missing column: {col}")
    for race_id, g in df.groupby("race_id"):
        out[race_id] = {
            int(r["boat_number"]): {
                "prob_first": float(r["prob_first"]),
                "prob_top_2": float(r["prob_top_2"]),
                "prob_top_3": float(r["prob_top_3"]),
            }
            for _, r in g.iterrows()
        }
    return out


def attach_actual_outcome(bets: pd.DataFrame, payouts: pd.DataFrame) -> pd.DataFrame:
    """三連単の実払戻を bets にマージ。当たり=1/payout、外れ=0/0。"""
    if bets.empty:
        return bets
    merged = bets.merge(payouts, on=["race_id", "combination"], how="left")
    merged["actual_hit"] = merged["payout"].notna().astype(int)
    merged["actual_payout"] = merged["payout"].fillna(0).astype(float)
    return merged.drop(columns=["payout"])


# ============================================================
# シミュレーション
# ============================================================

def simulate_betting(bets: pd.DataFrame, bet_amount: float = 100.0) -> dict:
    """三連単 Value Bet の払戻シミュレーション (フラット & Kelly)"""
    if bets.empty:
        return {"n_bets": 0, "n_hits": 0, "hit_rate": 0.0,
                "flat_stake": 0.0, "flat_payout": 0.0, "flat_roi": 0.0,
                "kelly_stake": 0.0, "kelly_payout": 0.0, "kelly_roi": 0.0}

    flat_stake = bet_amount * len(bets)
    flat_payout = (bets["actual_hit"] * bets["actual_payout"]).sum()

    # 1/4 Kelly 前提なので ×4 で素のKelly換算
    kelly_stakes = bet_amount * bets["kelly_fraction"] * 4
    kelly_total_stake = kelly_stakes.sum()
    kelly_payouts = bets["actual_hit"] * bets["actual_payout"] * (kelly_stakes / bet_amount)
    kelly_total_payout = kelly_payouts.sum()

    return {
        "n_bets": int(len(bets)),
        "n_hits": int(bets["actual_hit"].sum()),
        "hit_rate": float(bets["actual_hit"].mean()),
        "flat_stake": float(flat_stake),
        "flat_payout": float(flat_payout),
        "flat_roi": float((flat_payout - flat_stake) / flat_stake) if flat_stake > 0 else 0.0,
        "kelly_stake": float(kelly_total_stake),
        "kelly_payout": float(kelly_total_payout),
        "kelly_roi": float((kelly_total_payout - kelly_total_stake) / kelly_total_stake) if kelly_total_stake > 0 else 0.0,
    }


def compute_drawdown(daily_pnl: pd.Series) -> float:
    """最大ドローダウン (累積PnL の最大値からの最大下落)"""
    if daily_pnl.empty:
        return 0.0
    cum = daily_pnl.cumsum()
    peak = cum.cummax()
    dd = cum - peak
    return float(dd.min())


def compute_sharpe(daily_pnl: pd.Series) -> float:
    if daily_pnl.empty or daily_pnl.std() == 0:
        return 0.0
    return float(daily_pnl.mean() / daily_pnl.std() * np.sqrt(252))


# ============================================================
# 1窓を回す
# ============================================================

def run_window(
    train_from: date, train_to: date,
    val_from: date, val_to: date,
    db_path: str,
    ev_threshold: float = config.EV_THRESHOLD,
) -> tuple[dict, pd.DataFrame]:
    """
    1つの (train, val) ウィンドウで学習 → 予測 → ベット → ROI 計算。

    Returns: (metrics_dict, bets_df_with_outcome)
    """
    df_train = build_training_frame(
        db_path=db_path, date_from=str(train_from), date_to=str(train_to)
    )
    df_val = build_training_frame(
        db_path=db_path, date_from=str(val_from), date_to=str(val_to)
    )

    if df_train.empty or df_val.empty:
        logger.warning("empty data train=%s..%s val=%s..%s",
                       train_from, train_to, val_from, val_to)
        return simulate_betting(pd.DataFrame()), pd.DataFrame()

    model, feature_cols = train_ranker(df_train)

    # 較正は train 末尾20%で fit (val は使わない)
    df_train["race_date"] = pd.to_datetime(df_train["race_date"])
    cutoff = df_train["race_date"].quantile(0.8, interpolation="nearest")
    df_calib = df_train[df_train["race_date"] >= cutoff]
    df_calib_pred = predict_probs(model, df_calib, feature_cols)
    calibrators = fit_calibrators(df_calib_pred)

    # val 期間で予測 → 較正
    df_val_pred = predict_probs(model, df_val, feature_cols)
    df_val_pred = apply_calibrators(df_val_pred, calibrators)

    # オッズと払戻
    odds_df = load_trifecta_odds(db_path, val_from, val_to)
    payouts_df = load_trifecta_payouts(db_path, val_from, val_to)

    if odds_df.empty:
        logger.warning("no odds for val window %s..%s", val_from, val_to)
        return simulate_betting(pd.DataFrame()), pd.DataFrame()

    # Value Bet 検出
    boat_probs = build_boat_probs_dict(df_val_pred)
    bets = find_value_bets_trifecta(boat_probs, odds_df, ev_threshold=ev_threshold)
    bets = attach_actual_outcome(bets, payouts_df)
    metrics = simulate_betting(bets)
    return metrics, bets


# ============================================================
# CLI
# ============================================================

def _strategy_single_split(args) -> list:
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    df_all = build_training_frame(date_from=str(start), date_to=str(end))
    if df_all.empty:
        print("[ERROR] 学習対象期間にデータがありません")
        return []
    df_all["race_date"] = pd.to_datetime(df_all["race_date"])
    cutoff = df_all["race_date"].quantile(args.split_ratio, interpolation="nearest").date()
    train_from, train_to = start, cutoff - timedelta(days=1)
    val_from, val_to = cutoff, end
    print(f"single-split: train [{train_from}..{train_to}]  val [{val_from}..{val_to}]")
    return [(train_from, train_to, val_from, val_to)]


def _strategy_walk_forward(args) -> list:
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    splits = list(walk_forward_splits(start, end, args.train_days, args.val_days))
    print(f"walk-forward windows: {len(splits)}")
    for tf, tt, vf, vt in splits:
        print(f"  train [{tf}..{tt}]  val [{vf}..{vt}]")
    return splits


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", choices=["single-split", "walk-forward"],
                        default="single-split")
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--split-ratio", type=float, default=0.8,
                        help="single-split 用")
    parser.add_argument("--train-days", type=int, default=365,
                        help="walk-forward 用")
    parser.add_argument("--val-days", type=int, default=30,
                        help="walk-forward 用")
    parser.add_argument("--ev-threshold", type=float, default=config.EV_THRESHOLD)
    parser.add_argument("--db-path", default=config.DB_PATH)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    if args.strategy == "single-split":
        splits = _strategy_single_split(args)
    else:
        splits = _strategy_walk_forward(args)

    if not splits:
        return

    all_bets: list[pd.DataFrame] = []
    print("\n=== 各ウィンドウ結果 ===")
    for tf, tt, vf, vt in splits:
        m, bets = run_window(tf, tt, vf, vt, args.db_path, args.ev_threshold)
        print(
            f"  [{vf}..{vt}] n_bets={m['n_bets']} hit_rate={m['hit_rate']:.3f} "
            f"flat_roi={m['flat_roi']:+.3f} kelly_roi={m['kelly_roi']:+.3f}"
        )
        if not bets.empty:
            all_bets.append(bets)

    if not all_bets:
        print("\n[INFO] 全ウィンドウでベット無し")
        return

    combined = pd.concat(all_bets, ignore_index=True)
    overall = simulate_betting(combined)

    # 日次PnL: race_id の先頭8桁を日付として集計
    combined["race_date"] = pd.to_datetime(combined["race_id"].str[:8], format="%Y%m%d", errors="coerce")
    daily = combined.groupby("race_date").apply(
        lambda g: (g["actual_hit"] * g["actual_payout"]).sum() - 100 * len(g)
    )
    sharpe = compute_sharpe(daily)
    max_dd = compute_drawdown(daily)

    print("\n=== 集計 ===")
    print(f"  n_bets       : {overall['n_bets']}")
    print(f"  n_hits       : {overall['n_hits']}")
    print(f"  hit_rate     : {overall['hit_rate']:.4f}")
    print(f"  flat_roi     : {overall['flat_roi']:+.4f}")
    print(f"  kelly_roi    : {overall['kelly_roi']:+.4f}")
    print(f"  sharpe (日次) : {sharpe:+.3f}")
    print(f"  max_drawdown : {max_dd:+.0f} 円 (フラット100円ベット換算)")


if __name__ == "__main__":
    main()
