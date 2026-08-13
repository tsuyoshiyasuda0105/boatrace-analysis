from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path("C:/boat_project/boatrace-analysis")
sys.path.insert(0, str(REPO))

import sqlite3

import pandas as pd

from src.db.connection import connect as pg_connect


LOCAL_DB = REPO / "data" / "boatrace.db"
OUT = REPO / "reports" / "existing_roi_strength_filter_recent.md"


def summarize(df: pd.DataFrame) -> dict[str, float]:
    n = len(df)
    if n == 0:
        return {"n": 0, "hits": 0, "stake": 0, "pay": 0, "hit_rate": 0.0, "roi": 0.0}
    stake = float(df["stake_amount"].sum())
    pay = float(df["payout_amount"].sum())
    hits = int(df["is_hit"].sum())
    return {
        "n": n,
        "hits": hits,
        "stake": stake,
        "pay": pay,
        "hit_rate": hits / n * 100.0,
        "roi": pay / stake * 100.0 if stake else 0.0,
    }


def fmt(m: dict[str, float]) -> str:
    return f"n={int(m['n']):,} hit={m['hit_rate']:.1f}% ROI={m['roi']:.1f}% profit={int(m['pay']-m['stake']):+,}"


def load_roi_history() -> pd.DataFrame:
    with pg_connect() as conn:
        rows = conn.execute(
            """
            SELECT race_date, race_id, strategy_key, strategy_label, bet_json,
                   stake_amount, payout_amount, is_hit, is_settled
              FROM roi_race_history
             WHERE is_active = 1
               AND is_settled = 1
             ORDER BY race_date, race_id, strategy_key
            """
        ).fetchall()
    return pd.DataFrame(
        rows,
        columns=[
            "race_date",
            "race_id",
            "strategy_key",
            "strategy_label",
            "bet_json",
            "stake_amount",
            "payout_amount",
            "is_hit",
            "is_settled",
        ],
    )


def load_strength(race_ids: list[str]) -> pd.DataFrame:
    if not race_ids:
        return pd.DataFrame()
    placeholders = ",".join("?" for _ in race_ids)
    query = f"""
        SELECT race_id, boat_number, prob_first, prob_top_2
          FROM predictions
         WHERE model_version = 'v0.8'
           AND race_id IN ({placeholders})
    """
    with sqlite3.connect(LOCAL_DB) as conn:
        pred = pd.read_sql_query(query, conn, params=race_ids)
    if pred.empty:
        return pred
    pred["rank"] = pred.groupby("race_id")["prob_first"].rank(ascending=False, method="first")
    head = pred[pred["rank"].eq(1)][["race_id", "boat_number", "prob_first"]].rename(
        columns={"boat_number": "head", "prob_first": "p1"}
    )
    second = pred[pred["rank"].eq(2)][["race_id", "boat_number", "prob_first"]].rename(
        columns={"boat_number": "second", "prob_first": "p2"}
    )
    out = head.merge(second, on="race_id", how="inner")
    out["pair_prob"] = out["p1"] * out["p2"]
    out["margin"] = out["p1"] - out["p2"]
    return out


def main() -> None:
    hist = load_roi_history()
    strength = load_strength(sorted(hist["race_id"].unique()))
    df = hist.merge(strength, on="race_id", how="left")

    filters = [
        ("all", df["race_id"].notna()),
        ("p1*p2 >= 0.16", df["pair_prob"].ge(0.16)),
        ("p1*p2 >= 0.14", df["pair_prob"].ge(0.14)),
        ("p1 >= 0.50", df["p1"].ge(0.50)),
        ("p1 >= 0.55", df["p1"].ge(0.55)),
        ("margin >= 0.10", df["margin"].ge(0.10)),
        ("outer head & p1 >= 0.40", df["head"].isin([4, 5, 6]) & df["p1"].ge(0.40)),
    ]

    lines = [
        "# Existing adopted ROI strategies with model-strength filters",
        "",
        f"- History rows: {len(df):,}",
        f"- Date range: {df['race_date'].min()} to {df['race_date'].max()}",
        "- Strength source: local `predictions` model_version `v0.8`",
        "- Caution: this is recent operational history only, not a full 4-year backtest.",
        "",
        "## Overall",
        "",
        "| Filter | Result |",
        "|---|---:|",
    ]
    for label, mask in filters:
        lines.append(f"| {label} | {fmt(summarize(df[mask]))} |")

    lines.extend(["", "## By Strategy", "", "| Strategy | Base | p1*p2>=0.16 | p1>=0.50 |", "|---|---:|---:|---:|"])
    for key, g in df.groupby("strategy_key"):
        if len(g) < 2:
            continue
        label = str(g["strategy_label"].iloc[0] or key)
        lines.append(
            f"| {label} (`{key}`) | {fmt(summarize(g))} | "
            f"{fmt(summarize(g[g['pair_prob'].ge(0.16)]))} | "
            f"{fmt(summarize(g[g['p1'].ge(0.50)]))} |"
        )

    lines.extend(
        [
            "",
            "## Raw filter counts",
            "",
            "```json",
            json.dumps(
                {
                    label: {
                        "races": int(df[mask]["race_id"].nunique()),
                        "rows": int(mask.sum()),
                    }
                    for label, mask in filters
                },
                ensure_ascii=False,
                indent=2,
            ),
            "```",
        ]
    )
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT}")
    print("\n".join(lines[:24]))


if __name__ == "__main__":
    main()
