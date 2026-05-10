"""
期待値 (Value Bet) 検出ロジック

予測確率 vs 市場オッズ で期待値プラスの券を抽出する。

EV = predicted_prob × odds - 1

EV > 閾値 (例: 0.15) のものを買い目とし、Kelly基準でベットサイズを決める。

注意:
  - 予測確率は較正されていることが前提 (生のLightGBMスコアそのままはNG)
  - 控除率 25% を越える期待値プラスは滅多に存在しない
  - サンプル数が少ない大穴帯は分散大なので運用に注意
"""
from __future__ import annotations

import sqlite3
from typing import Optional
import pandas as pd

import config


# ============================================================
# 期待値計算
# ============================================================

def compute_ev(prob: float, odds: float) -> float:
    """期待値 = (確率 × オッズ) - 1"""
    return prob * odds - 1.0


def kelly_fraction(prob: float, odds: float, fraction: float = 0.25) -> float:
    """
    Kelly基準ベットサイズ (資金に対する割合)。
    f* = (p × b - q) / b   where b = odds - 1, q = 1 - p

    実運用では分散を抑えるため 1/4 Kelly などの fractional Kelly を使う。
    結果が負なら賭けない。
    """
    b = odds - 1.0
    if b <= 0:
        return 0.0
    q = 1.0 - prob
    f_star = (prob * b - q) / b
    return max(0.0, f_star * fraction)


# ============================================================
# 単勝の期待値検出
# ============================================================

def find_value_bets_win(
    predictions_df: pd.DataFrame,
    odds_df: pd.DataFrame,
    ev_threshold: float = config.EV_THRESHOLD,
) -> pd.DataFrame:
    """
    1着予測 vs 単勝オッズで期待値プラスの艇を抽出。

    Args:
      predictions_df: race_id, boat_number, prob_first (較正済) の DataFrame
      odds_df: race_id, boat_number, odds の単勝オッズ DataFrame

    Returns:
      EV >= ev_threshold の行のみ。kelly_fraction 列付き。
    """
    merged = predictions_df.merge(
        odds_df, on=["race_id", "boat_number"], how="inner"
    )
    merged["expected_value"] = merged["prob_first"] * merged["odds"] - 1.0
    merged["kelly_fraction"] = merged.apply(
        lambda r: kelly_fraction(r["prob_first"], r["odds"]), axis=1
    )
    return merged[merged["expected_value"] >= ev_threshold].sort_values(
        "expected_value", ascending=False
    )


# ============================================================
# 三連単の期待値検出
# ============================================================

def trifecta_combination_prob(
    boat_probs: dict[int, dict[str, float]],
) -> dict[str, float]:
    """
    各艇の prob_first / prob_top_2 / prob_top_3 から
    三連単 120通りの確率を近似計算。

    厳密にはマルコフ的な順序モデルが必要だが、
    実用近似として「Plackett-Luce モデル」風に:

        P(A→B→C) ≒ P(A=1着) × P(B=2着 | A出現除く) × P(C=3着 | A,B出現除く)

    各艇の "強さスコア" を 1着確率と等価とみなし、残った艇から再正規化していく。
    """
    boats = list(boat_probs.keys())
    p_first = {b: boat_probs[b]["prob_first"] for b in boats}

    combos = {}
    for a in boats:
        rest1 = {b: p_first[b] for b in boats if b != a}
        z1 = sum(rest1.values()) or 1.0
        for b in rest1:
            p_b = rest1[b] / z1
            rest2 = {c: p_first[c] for c in boats if c not in (a, b)}
            z2 = sum(rest2.values()) or 1.0
            for c in rest2:
                p_c = rest2[c] / z2
                combos[f"{a}-{b}-{c}"] = p_first[a] * p_b * p_c
    return combos


def find_value_bets_trifecta(
    boat_probs_by_race: dict[str, dict[int, dict[str, float]]],
    odds_df: pd.DataFrame,
    ev_threshold: float = config.EV_THRESHOLD,
) -> pd.DataFrame:
    """
    三連単の期待値プラス検出。

    Args:
      boat_probs_by_race: { race_id: { boat_number: {prob_first, prob_top_2, prob_top_3} } }
      odds_df: race_id, combination ('1-2-3'形式), odds
    """
    rows = []
    for race_id, boat_probs in boat_probs_by_race.items():
        combos = trifecta_combination_prob(boat_probs)
        race_odds = odds_df[odds_df["race_id"] == race_id]
        for _, ro in race_odds.iterrows():
            comb = ro["combination"]
            prob = combos.get(comb, 0.0)
            ev = compute_ev(prob, ro["odds"])
            if ev >= ev_threshold:
                rows.append({
                    "race_id": race_id,
                    "combination": comb,
                    "predicted_prob": prob,
                    "market_odds": ro["odds"],
                    "expected_value": ev,
                    "kelly_fraction": kelly_fraction(prob, ro["odds"]),
                })
    return pd.DataFrame(rows).sort_values("expected_value", ascending=False)


# ============================================================
# DB保存
# ============================================================

def save_value_bets(
    df: pd.DataFrame,
    bet_type: str,
    model_version: str,
    db_path: Optional[str] = None,
) -> int:
    """value_bets テーブルに保存"""
    db_path = db_path or config.DB_PATH
    if df.empty:
        return 0

    from datetime import datetime
    now = datetime.utcnow().isoformat()

    with sqlite3.connect(db_path) as conn:
        rows = []
        for _, r in df.iterrows():
            rows.append((
                r["race_id"],
                bet_type,
                r.get("combination", str(r.get("boat_number", ""))),
                float(r.get("predicted_prob", r.get("prob_first", 0.0))),
                float(r.get("market_odds", r.get("odds", 0.0))),
                float(r["expected_value"]),
                float(r.get("kelly_fraction", 0.0)),
                model_version,
                now,
            ))

        conn.executemany("""
            INSERT OR REPLACE INTO value_bets (
                race_id, bet_type, combination,
                predicted_prob, market_odds, expected_value,
                kelly_fraction, model_version, detected_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, rows)
        conn.commit()
    return len(rows)
