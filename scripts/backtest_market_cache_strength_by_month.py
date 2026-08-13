from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


DB = Path("C:/boat_project/boatrace-analysis/data/boatrace.db")


@dataclass(frozen=True)
class Pick:
    race_date: str
    race_id: str
    strategy_key: str
    strategy_label: str
    bet_type: str
    combination: str


def parse_bet_text(text: str) -> list[tuple[str, str]]:
    text = str(text or "")
    if not text:
        return []
    if "3連単" in text:
        bet_type = "trifecta"
    elif "2連単" in text:
        bet_type = "exacta"
    elif "2連複" in text:
        bet_type = "quinella"
    elif "単勝" in text:
        bet_type = "win"
    else:
        return []
    combos = re.findall(r"\d(?:-\d){0,2}", text)
    return [(bet_type, combo) for combo in combos]


def unique_preserve(items: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen = set()
    out = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def load_cache_picks(month: str) -> list[Pick]:
    like = f"market_signals:%:{month}-%"
    picks: list[Pick] = []
    with sqlite3.connect(DB) as conn:
        rows = conn.execute(
            """
            SELECT cache_key, html
              FROM page_html_cache
             WHERE cache_key LIKE ?
             ORDER BY cache_key
            """,
            (like,),
        ).fetchall()
    for cache_key, html in rows:
        try:
            payload = json.loads(html)
        except Exception:
            continue
        race_date = str(payload.get("date") or cache_key[-10:])
        signals = payload.get("signals") or {}
        if not isinstance(signals, dict):
            continue
        for rid, signal in signals.items():
            if not isinstance(signal, dict):
                continue
            l4 = signal.get("l4") or {}
            if not isinstance(l4, dict):
                continue
            if l4.get("is_after_exhibition_out") or l4.get("start_prediction_filter_status") == "failed":
                continue
            race_id = str(signal.get("race_id") or rid or "")
            levels = [str(l4.get("level") or "")]
            levels.extend(str(x) for x in (l4.get("matched_levels") or []) if x)
            labels = [str(l4.get("label") or "")]
            labels.extend(str(x) for x in (l4.get("matched_labels") or []) if x)
            bets = [str(l4.get("bet") or "")]
            bets.extend(str(x) for x in (l4.get("matched_bets") or []) if x)
            # Pair matched arrays when possible. Otherwise fall back to the primary level/bet.
            candidates: list[tuple[str, str, str]] = []
            max_len = max(len(levels), len(bets), len(labels))
            for idx in range(max_len):
                level = levels[idx] if idx < len(levels) else levels[0]
                label = labels[idx] if idx < len(labels) else labels[0]
                bet = bets[idx] if idx < len(bets) else bets[0]
                if not level or level.startswith("morning_watch_"):
                    continue
                candidates.append((level, label, bet))
            seen_candidates = set()
            for level, label, bet in candidates:
                if (level, bet) in seen_candidates:
                    continue
                seen_candidates.add((level, bet))
                for bet_type, combo in unique_preserve(parse_bet_text(bet)):
                    if race_id:
                        picks.append(Pick(race_date, race_id, level, label, bet_type, combo))
    return picks


def load_strength_and_payouts(picks: list[Pick]) -> pd.DataFrame:
    if not picks:
        return pd.DataFrame()
    race_ids = sorted({p.race_id for p in picks})
    with sqlite3.connect(DB) as conn:
        ph = ",".join("?" for _ in race_ids)
        pred = pd.read_sql_query(
            f"""
            SELECT race_id, boat_number, prob_first
              FROM predictions
             WHERE model_version = 'v0.8'
               AND race_id IN ({ph})
            """,
            conn,
            params=race_ids,
        )
        rows = []
        for p in picks:
            pay_row = conn.execute(
                """
                SELECT payout
                  FROM race_payouts
                 WHERE race_id = ?
                   AND bet_type = ?
                   AND combination = ?
                 ORDER BY payout DESC
                 LIMIT 1
                """,
                (p.race_id, p.bet_type, p.combination),
            ).fetchone()
            rows.append(
                {
                    "race_date": p.race_date,
                    "race_id": p.race_id,
                    "strategy_key": p.strategy_key,
                    "strategy_label": p.strategy_label,
                    "bet_type": p.bet_type,
                    "combination": p.combination,
                    "payout": int(pay_row[0] or 0) if pay_row else 0,
                }
            )
    df = pd.DataFrame(rows)
    if pred.empty:
        df["p1"] = None
        df["head"] = None
        return df
    pred["rank"] = pred.groupby("race_id")["prob_first"].rank(ascending=False, method="first")
    head = pred[pred["rank"].eq(1)][["race_id", "boat_number", "prob_first"]].rename(
        columns={"boat_number": "head", "prob_first": "p1"}
    )
    return df.merge(head, on="race_id", how="left")


def summarize(df: pd.DataFrame) -> dict[str, float]:
    n = int(df["ticket_id"].nunique()) if "ticket_id" in df else len(df)
    if n <= 0:
        return {"n": 0, "hits": 0, "stake": 0.0, "pay": 0.0, "hit_rate": 0.0, "roi": 0.0}
    # Aggregate multiple combinations under the same strategy/race as one ticket.
    grouped = df.groupby(["race_date", "race_id", "strategy_key"], dropna=False)["payout"].sum().reset_index()
    n = len(grouped)
    pay = float(grouped["payout"].sum())
    hits = int(grouped["payout"].gt(0).sum())
    stake = float(n * 100)
    return {"n": n, "hits": hits, "stake": stake, "pay": pay, "hit_rate": hits / n * 100, "roi": pay / stake * 100}


def fmt(m: dict[str, float]) -> str:
    return f"n={int(m['n'])} hit={m['hit_rate']:.1f}% ROI={m['roi']:.1f}% profit={int(m['pay'] - m['stake']):+,}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--month", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    picks = load_cache_picks(args.month)
    df = load_strength_and_payouts(picks)
    if df.empty:
        Path(args.out).write_text(f"# Market cache strength {args.month}\n\nNo picks found.\n", encoding="utf-8")
        print("no picks")
        return
    df["ticket_id"] = df["race_date"] + "|" + df["race_id"] + "|" + df["strategy_key"]
    base = df
    strong = df[df["p1"].ge(0.55)]
    rows = []
    for key, g in base.groupby("strategy_key", dropna=False):
        label = str(g["strategy_label"].iloc[0] or key)
        b = summarize(g)
        s = summarize(g[g["p1"].ge(0.55)])
        rows.append((key, label, b, s))
    rows.sort(key=lambda x: (x[2]["n"], x[2]["roi"]), reverse=True)

    lines = [
        f"# Market-signals cache strategy validation: {args.month}",
        "",
        "- Source: local `page_html_cache` market_signals snapshots.",
        "- Unit: one strategy/race = 100 yen. Multiple combinations under the same strategy/race are treated as one ticket group.",
        "- Filter: `p1>=0.55` means the model's top first-place probability is at least 55%.",
        "- Caveat: this validates saved June market-signal snapshots, not strategies added after June that were never present in those snapshots.",
        "",
        "## Overall",
        "",
        "| Filter | Result |",
        "|---|---:|",
        f"| all cached MD/adopted signals | {fmt(summarize(base))} |",
        f"| p1>=0.55 | {fmt(summarize(strong))} |",
        "",
        "## By Strategy",
        "",
        "| Strategy | Base | p1>=0.55 | Change |",
        "|---|---:|---:|---:|",
    ]
    for key, label, b, s in rows:
        if b["n"] < 1:
            continue
        change = f"hit {s['hit_rate'] - b['hit_rate']:+.1f}pt / ROI {s['roi'] - b['roi']:+.1f}pt"
        lines.append(f"| {label} (`{key}`) | {fmt(b)} | {fmt(s)} | {change} |")

    lines.extend(
        [
            "",
            "## Raw",
            "",
            f"- Cache picks rows: {len(picks):,}",
            f"- Ticket groups: {base['ticket_id'].nunique():,}",
            f"- p1>=0.55 ticket groups: {strong['ticket_id'].nunique():,}",
        ]
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {out}")
    print("\n".join(lines[:28]))


if __name__ == "__main__":
    main()
