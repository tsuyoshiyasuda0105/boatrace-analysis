from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd

REPO = Path("C:/boat_project/boatrace-analysis")
sys.path.insert(0, str(REPO))

from src.db.connection import connect as pg_connect


LOCAL_DB = REPO / "data" / "boatrace.db"


def summarize(df: pd.DataFrame) -> dict[str, float]:
    n = len(df)
    if n == 0:
        return {"n": 0, "hits": 0, "stake": 0.0, "pay": 0.0, "hit_rate": 0.0, "roi": 0.0}
    stake = float(df["stake_amount"].fillna(0).sum())
    pay = float(df["payout_amount"].fillna(0).sum())
    hits = int(df["is_hit"].fillna(False).astype(bool).sum())
    return {
        "n": n,
        "hits": hits,
        "stake": stake,
        "pay": pay,
        "hit_rate": hits / n * 100.0,
        "roi": pay / stake * 100.0 if stake else 0.0,
    }


def fmt(m: dict[str, float]) -> str:
    return (
        f"n={int(m['n']):,} hit={m['hit_rate']:.1f}% "
        f"ROI={m['roi']:.1f}% profit={int(m['pay'] - m['stake']):+,}"
    )


def load_roi_history(start: str, end: str) -> pd.DataFrame:
    with pg_connect() as conn:
        rows = conn.execute(
            """
            SELECT race_date, race_id, strategy_key, strategy_label, bet_json,
                   stake_amount, payout_amount, is_hit, is_settled
              FROM roi_race_history
             WHERE is_active = 1
               AND is_settled = 1
               AND race_date >= %s
               AND race_date <= %s
             ORDER BY race_date, race_id, strategy_key
            """,
            (start, end),
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
        return pd.DataFrame(
            columns=["race_id", "head", "p1", "second", "p2", "pair_prob", "margin"]
        )
    placeholders = ",".join("?" for _ in race_ids)
    query = f"""
        SELECT race_id, boat_number, prob_first, prob_top_2, prob_top_3
          FROM predictions
         WHERE model_version = 'v0.8'
           AND race_id IN ({placeholders})
    """
    with sqlite3.connect(LOCAL_DB) as conn:
        pred = pd.read_sql_query(query, conn, params=race_ids)
    if pred.empty:
        return pd.DataFrame(
            columns=["race_id", "head", "p1", "second", "p2", "pair_prob", "margin"]
        )
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    hist = load_roi_history(args.start, args.end)
    if hist.empty:
        lines = [
            "# ROI strength filter by period",
            "",
            f"- Period: {args.start} to {args.end}",
            "- Source: production `roi_race_history`",
            "- Result: no settled active rows found for this period.",
        ]
        Path(args.out).write_text("\n".join(lines), encoding="utf-8")
        print("\n".join(lines))
        return

    strength = load_strength(sorted(hist["race_id"].dropna().unique()))
    df = hist.merge(strength, on="race_id", how="left")

    filters = [
        ("all", df["race_id"].notna()),
        ("p1 >= 0.50", df["p1"].ge(0.50)),
        ("p1 >= 0.55", df["p1"].ge(0.55)),
        ("p1 >= 0.60", df["p1"].ge(0.60)),
        ("p1*p2 >= 0.14", df["pair_prob"].ge(0.14)),
        ("p1*p2 >= 0.16", df["pair_prob"].ge(0.16)),
        ("margin >= 0.10", df["margin"].ge(0.10)),
    ]

    lines = [
        "# ROI adopted strategies with model-strength filters",
        "",
        f"- Period: {args.start} to {args.end}",
        f"- History rows: {len(df):,}",
        f"- Unique races: {df['race_id'].nunique():,}",
        f"- Strategies: {df['strategy_key'].nunique():,}",
        "- Source: production `roi_race_history` + local `predictions` model_version `v0.8`",
        "- Note: this validates rows that already exist in ROI history; it does not recreate missing June signals.",
        "",
        "## Overall",
        "",
        "| Filter | Result |",
        "|---|---:|",
    ]
    for label, mask in filters:
        lines.append(f"| {label} | {fmt(summarize(df[mask]))} |")

    lines.extend(
        [
            "",
            "## By Strategy",
            "",
            "| Strategy | Base | p1>=0.55 | Change | p1>=0.50 | p1*p2>=0.16 |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for key, g in df.groupby("strategy_key", dropna=False):
        base = summarize(g)
        strong = summarize(g[g["p1"].ge(0.55)])
        label = str(g["strategy_label"].iloc[0] or key)
        change = f"hit {strong['hit_rate'] - base['hit_rate']:+.1f}pt / ROI {strong['roi'] - base['roi']:+.1f}pt"
        lines.append(
            f"| {label} (`{key}`) | {fmt(base)} | {fmt(strong)} | {change} | "
            f"{fmt(summarize(g[g['p1'].ge(0.50)]))} | "
            f"{fmt(summarize(g[g['pair_prob'].ge(0.16)]))} |"
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
                        "rows": int(mask.sum()),
                        "races": int(df[mask]["race_id"].nunique()),
                    }
                    for label, mask in filters
                },
                ensure_ascii=False,
                indent=2,
            ),
            "```",
        ]
    )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {out}")
    print("\n".join(lines[:26]))


if __name__ == "__main__":
    main()
