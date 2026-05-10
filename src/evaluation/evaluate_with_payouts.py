"""
race_payouts のみで評価 (Layer 3 オッズ不要のバックテスト)

Layer 2 Open API の race_payouts には全券種の「実払戻金」が入っているので、
モデル予測 + 払戻金で複数戦略の ROI を計算できる。

ただし制約として:
  - 「外れた目のオッズ」は分からない (実払戻のみ)
  - Value bet 検出は「実現オッズ vs 予測確率」で代用 (実運用では締切前オッズを使う)
  - これは "in-sample でのオッズ後出し評価" なので、現実より楽観的になる傾向

戦略:
  A. fixed_top1_win: 毎レース、予測1着艇に単勝100円ベット
  B. fixed_top1_trifecta: 予測順位 (1-2-3) でトリフェクタ100円ベット
  C. value_win: 単勝 EV>閾値 のみベット (実払戻 / 100 を実現オッズとして使用)
"""
from __future__ import annotations

import argparse
import logging
import pickle
import sys
from datetime import date
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd

import config
from src.db.connection import connect as db_connect
from src.features.builder import build_training_frame
from src.models.train import predict_probs
from src.models.calibration import apply_calibrators

logger = logging.getLogger(__name__)


# ============================================================
# DB ロード
# ============================================================

def load_payouts(db_path: str, date_from: date, date_to: date,
                 bet_type: str) -> pd.DataFrame:
    sql = """
        SELECT p.race_id, p.combination, p.payout
          FROM race_payouts p
          JOIN races r ON p.race_id = r.race_id
         WHERE p.bet_type = ?
           AND r.race_date BETWEEN ? AND ?
    """
    with db_connect(db_path) as conn:
        return pd.read_sql_query(
            sql, conn, params=(bet_type, date_from.isoformat(), date_to.isoformat())
        )


# ============================================================
# モデル artifact ロード
# ============================================================

def load_artifact(version: str) -> dict:
    path = config.MODEL_DIR / f"ranker_{version}.pkl"
    if not path.exists():
        raise FileNotFoundError(f"artifact not found: {path}")
    with open(path, "rb") as f:
        return pickle.load(f)


# ============================================================
# 予測 → 各艇の確率データ
# ============================================================

def predict_with_probs(artifact: dict, df_val: pd.DataFrame) -> pd.DataFrame:
    """val 期間の DataFrame に prob_first/top_2/top_3, raw_score を付与"""
    df = predict_probs(artifact["model"], df_val, artifact["feature_cols"])
    if artifact.get("calibrators"):
        df = apply_calibrators(df, artifact["calibrators"])
    else:
        # 較正器が無い場合は uncalibrated をそのまま使う
        df["prob_first"] = df["prob_first_uncalibrated"]
        from src.models.calibration import add_top_k_uncalibrated
        df = add_top_k_uncalibrated(df)
        df["prob_top_2"] = df["prob_top_2_uncalibrated"]
        df["prob_top_3"] = df["prob_top_3_uncalibrated"]
    return df


# ============================================================
# 戦略 A: 単勝 1着予測 (毎レース)
# ============================================================

def evaluate_fixed_top1_win(df_pred: pd.DataFrame, win_payouts: pd.DataFrame,
                            bet_amount: float = 100.0) -> dict:
    """
    各レースで argmax(prob_first) の艇に固定ベット。
    実払戻 = race_payouts(bet_type='win', combination=str(boat_no)).payout
    """
    # 各レースの予測1着艇
    idx = df_pred.groupby("race_id")["prob_first"].idxmax()
    picks = df_pred.loc[idx, ["race_id", "boat_number", "prob_first"]].copy()
    picks["combination"] = picks["boat_number"].astype(str)

    merged = picks.merge(win_payouts, on=["race_id", "combination"], how="left")
    merged["actual_payout"] = merged["payout"].fillna(0).astype(float)
    merged["actual_hit"] = (merged["payout"].notna()).astype(int)

    n_bets = len(merged)
    n_hits = int(merged["actual_hit"].sum())
    total_stake = bet_amount * n_bets
    total_payout = float(merged["actual_payout"].sum())
    return {
        "strategy": "fixed_top1_win",
        "n_bets": n_bets,
        "n_hits": n_hits,
        "hit_rate": n_hits / n_bets if n_bets else 0.0,
        "total_stake": total_stake,
        "total_payout": total_payout,
        "roi": (total_payout - total_stake) / total_stake if total_stake else 0.0,
    }


# ============================================================
# 戦略 B: 単勝 Value Bet (実現オッズ vs 予測確率)
# ============================================================

def evaluate_value_win(df_pred: pd.DataFrame, win_payouts: pd.DataFrame,
                       ev_threshold: float = config.EV_THRESHOLD,
                       bet_amount: float = 100.0) -> dict:
    """
    各艇 × race の prob_first と win 払戻 (= 実現単勝オッズ × 100) を結合。
    EV = prob × odds - 1 が ev_threshold 以上ならベット。

    NOTE: 実払戻ベースなので「当たった目のオッズ」しか分からない。
          実装上は win_payouts に存在しない (boat_number, race_id) ペアは
          「外れた」として odds=0, EV=-1 とみなしベット対象から除外。
    """
    df = df_pred[["race_id", "boat_number", "prob_first"]].copy()
    df["combination"] = df["boat_number"].astype(str)
    merged = df.merge(win_payouts, on=["race_id", "combination"], how="left")
    # 外れ艇は payout NaN → odds 計算できないが、実運用では締切前オッズで判定するので
    # ここでは「実現オッズが取れている目だけ」を value bet 候補とする (上方バイアス注意)
    merged["odds"] = merged["payout"] / bet_amount
    merged["ev"] = merged["prob_first"] * merged["odds"] - 1.0
    candidates = merged.dropna(subset=["odds"])
    bets = candidates[candidates["ev"] >= ev_threshold].copy()

    if bets.empty:
        return {
            "strategy": f"value_win (EV>={ev_threshold})",
            "n_bets": 0, "n_hits": 0, "hit_rate": 0.0,
            "total_stake": 0.0, "total_payout": 0.0, "roi": 0.0,
        }

    # 当たり (= win 払戻が存在する → そもそも勝った目) なので全員 hit
    bets["actual_payout"] = bets["payout"]
    bets["actual_hit"] = 1
    n_bets = len(bets)
    total_stake = bet_amount * n_bets
    total_payout = float(bets["actual_payout"].sum())
    return {
        "strategy": f"value_win (EV>={ev_threshold})",
        "n_bets": n_bets,
        "n_hits": n_bets,  # 構造上、評価対象は実現オッズが取れた目=勝った目のみ
        "hit_rate": 1.0,
        "total_stake": total_stake,
        "total_payout": total_payout,
        "roi": (total_payout - total_stake) / total_stake if total_stake else 0.0,
        "warning": "実現オッズベース。外れ目のオッズ不明のため上方バイアスあり",
    }


# ============================================================
# 戦略 C: トリフェクタ Top1 (毎レース)
# ============================================================

def evaluate_fixed_top1_trifecta(df_pred: pd.DataFrame, trifecta_payouts: pd.DataFrame,
                                 bet_amount: float = 100.0) -> dict:
    """
    各レースで raw_score 順に並べて 1-2-3 の組合せをベット。
    """
    df = df_pred.sort_values(["race_id", "raw_score"], ascending=[True, False]).copy()
    df["rank"] = df.groupby("race_id").cumcount() + 1
    top3 = df[df["rank"] <= 3].copy()

    picks = (
        top3.groupby("race_id")["boat_number"]
        .apply(lambda s: "-".join(s.astype(str).tolist()))
        .reset_index(name="combination")
    )

    merged = picks.merge(trifecta_payouts, on=["race_id", "combination"], how="left")
    merged["actual_payout"] = merged["payout"].fillna(0).astype(float)
    merged["actual_hit"] = (merged["payout"].notna()).astype(int)

    n_bets = len(merged)
    n_hits = int(merged["actual_hit"].sum())
    total_stake = bet_amount * n_bets
    total_payout = float(merged["actual_payout"].sum())
    return {
        "strategy": "fixed_top1_trifecta",
        "n_bets": n_bets,
        "n_hits": n_hits,
        "hit_rate": n_hits / n_bets if n_bets else 0.0,
        "total_stake": total_stake,
        "total_payout": total_payout,
        "roi": (total_payout - total_stake) / total_stake if total_stake else 0.0,
    }


# ============================================================
# 予測精度メトリクス (オッズ無関係)
# ============================================================

def predictive_metrics(df_pred: pd.DataFrame) -> dict:
    """的中率系メトリクス (購入無し、純粋な予測精度)"""
    df = df_pred.dropna(subset=["finishing_position"]).copy()
    df["finishing_position"] = df["finishing_position"].astype(int)

    # 各レースで argmax(prob_first) の艇が 1着だったかどうか
    idx = df.groupby("race_id")["prob_first"].idxmax()
    top1 = df.loc[idx]
    hit_top1 = (top1["finishing_position"] == 1).mean()

    # 各レースで raw_score 上位2艇が 1-2着 (順序問わず) を含むか
    df_sorted = df.sort_values(["race_id", "raw_score"], ascending=[True, False]).copy()
    df_sorted["rank"] = df_sorted.groupby("race_id").cumcount() + 1
    top2_pred = set(zip(df_sorted[df_sorted["rank"] <= 2]["race_id"],
                        df_sorted[df_sorted["rank"] <= 2]["boat_number"]))
    actual_top2 = set(zip(df[df["finishing_position"] <= 2]["race_id"],
                          df[df["finishing_position"] <= 2]["boat_number"]))
    # レースごとに両方含むか
    races = df["race_id"].unique()
    hit_quinella = sum(
        len({(r, b) for (r, b) in top2_pred if r == race}
            & {(r, b) for (r, b) in actual_top2 if r == race}) == 2
        for race in races
    ) / len(races)

    return {
        "top1_accuracy": float(hit_top1),
        "top2_set_accuracy": float(hit_quinella),
        "n_races": int(df["race_id"].nunique()),
    }


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default=config.DEFAULT_MODEL_VERSION)
    parser.add_argument("--date-from", required=True, help="val 期間 YYYY-MM-DD")
    parser.add_argument("--date-to", required=True, help="val 期間 YYYY-MM-DD")
    parser.add_argument("--ev-threshold", type=float, default=config.EV_THRESHOLD)
    parser.add_argument("--db-path", default=config.DB_PATH)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    print(f"[1/3] artifact 読み込み (version={args.version})")
    artifact = load_artifact(args.version)
    print(f"      features: {len(artifact['feature_cols'])} 列")
    print(f"      calibrators: {list((artifact.get('calibrators') or {}).keys())}")

    print(f"\n[2/3] val 期間 {args.date_from} .. {args.date_to} を予測")
    df_val = build_training_frame(
        db_path=args.db_path, date_from=args.date_from, date_to=args.date_to
    )
    print(f"      {len(df_val):,} 行 / {df_val['race_id'].nunique():,} レース")
    df_pred = predict_with_probs(artifact, df_val)

    print(f"\n[3/3] 評価")

    pm = predictive_metrics(df_pred)
    print(f"  予測精度 (オッズ無関係):")
    print(f"    1着的中率   : {pm['top1_accuracy']:.4f}  (n_races={pm['n_races']})")
    print(f"    2連的中率   : {pm['top2_set_accuracy']:.4f}")

    df_to = date.fromisoformat(args.date_to)
    df_from = date.fromisoformat(args.date_from)

    win_p = load_payouts(args.db_path, df_from, df_to, "win")
    tri_p = load_payouts(args.db_path, df_from, df_to, "trifecta")

    print(f"\n  単勝 (race_payouts win 件数={len(win_p):,}):")
    for fn in [evaluate_fixed_top1_win]:
        r = fn(df_pred, win_p)
        print(f"    [{r['strategy']}]")
        print(f"      n_bets={r['n_bets']} hits={r['n_hits']} hit_rate={r['hit_rate']:.4f}")
        print(f"      stake={r['total_stake']:,.0f} payout={r['total_payout']:,.0f}")
        print(f"      ROI = {r['roi']:+.4f}")

    r = evaluate_value_win(df_pred, win_p, ev_threshold=args.ev_threshold)
    print(f"    [{r['strategy']}]")
    print(f"      n_bets={r['n_bets']} hits={r['n_hits']} hit_rate={r['hit_rate']:.4f}")
    print(f"      stake={r['total_stake']:,.0f} payout={r['total_payout']:,.0f}")
    print(f"      ROI = {r['roi']:+.4f}")
    if "warning" in r:
        print(f"      [!] {r['warning']}")

    print(f"\n  三連単 (race_payouts trifecta 件数={len(tri_p):,}):")
    r = evaluate_fixed_top1_trifecta(df_pred, tri_p)
    print(f"    [{r['strategy']}]")
    print(f"      n_bets={r['n_bets']} hits={r['n_hits']} hit_rate={r['hit_rate']:.4f}")
    print(f"      stake={r['total_stake']:,.0f} payout={r['total_payout']:,.0f}")
    print(f"      ROI = {r['roi']:+.4f}")


if __name__ == "__main__":
    main()
