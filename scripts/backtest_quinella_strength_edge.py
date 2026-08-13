from __future__ import annotations

from pathlib import Path

import pandas as pd


PROJECT = Path("C:/boat_project/boatrace-analysis")
PKL = PROJECT / "reports" / "ev_picks_4y.pkl"
OUT = PROJECT / "reports" / "quinella_strength_edge_4y.md"
SPLIT = "2024-05-01"


def metrics(df: pd.DataFrame) -> dict[str, float]:
    n = len(df)
    if n == 0:
        return {"n": 0, "hits": 0, "hit_rate": 0.0, "roi": 0.0, "avg_hit_pay": 0.0}
    hit = (df["head_pos"].le(2) & df["second_pos"].le(2))
    payout = df["win_qn_pay"].where(hit, 0).fillna(0).astype(float)
    hits = int(hit.sum())
    return {
        "n": n,
        "hits": hits,
        "hit_rate": hits / n * 100.0,
        "roi": payout.sum() / (100.0 * n) * 100.0,
        "avg_hit_pay": payout[hit].mean() if hits else 0.0,
    }


def fmt(m: dict[str, float]) -> str:
    breakeven = (10000.0 / m["hit_rate"]) if m["hit_rate"] else 0.0
    return (
        f"n={int(m['n']):,} hit={m['hit_rate']:.1f}% "
        f"ROI={m['roi']:.1f}% avg_pay={m['avg_hit_pay']:.0f} "
        f"BE={breakeven:.0f}"
    )


def add_row(rows: list[dict], label: str, mask: pd.Series, df: pd.DataFrame) -> None:
    tr = df[df["race_date_str"] < SPLIT]
    te = df[df["race_date_str"] >= SPLIT]
    rows.append(
        {
            "label": label,
            "train": metrics(tr[mask.loc[tr.index]]),
            "test": metrics(te[mask.loc[te.index]]),
            "all": metrics(df[mask]),
        }
    )


def main() -> None:
    df = pd.read_pickle(PKL).copy()
    df["pair_prob"] = df["p1"] * df["p2"]
    df["p1_over_p2"] = df["p1"] / df["p2"].clip(lower=0.001)
    df["top2_nat2_min"] = df[["head_natl2", "second_natl2"]].min(axis=1)
    df["top2_avgst_max"] = df[["head_avgst", "second_avgst"]].max(axis=1)
    df["top2_motor2_min"] = df[["head_motor2", "second_motor2"]].min(axis=1)
    df["outer_head"] = df["head"].isin([4, 5, 6])
    df["head1"] = df["head"].eq(1)

    rows: list[dict] = []
    base = pd.Series(True, index=df.index)
    add_row(rows, "ALL model top2 quinella", base, df)

    for p1 in [0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65]:
        add_row(rows, f"p1 >= {p1:.2f}", df["p1"].ge(p1), df)

    for margin in [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35]:
        add_row(rows, f"margin >= {margin:.2f}", df["margin"].ge(margin), df)

    for pp in [0.08, 0.10, 0.12, 0.14, 0.16]:
        add_row(rows, f"p1*p2 >= {pp:.2f}", df["pair_prob"].ge(pp), df)

    combos = [
        ("p1>=.45 & margin>=.10", df["p1"].ge(0.45) & df["margin"].ge(0.10)),
        ("p1>=.50 & margin>=.15", df["p1"].ge(0.50) & df["margin"].ge(0.15)),
        ("p1>=.55 & margin>=.20", df["p1"].ge(0.55) & df["margin"].ge(0.20)),
        ("p1>=.50 & p1*p2>=.10", df["p1"].ge(0.50) & df["pair_prob"].ge(0.10)),
        ("p1>=.55 & p1*p2>=.10", df["p1"].ge(0.55) & df["pair_prob"].ge(0.10)),
        ("p1>=.50 & top2_nat2_min>=40", df["p1"].ge(0.50) & df["top2_nat2_min"].ge(40)),
        ("p1>=.50 & top2_avgst_max<=0.17", df["p1"].ge(0.50) & df["top2_avgst_max"].le(0.17)),
        ("p1>=.50 & top2_motor2_min>=35", df["p1"].ge(0.50) & df["top2_motor2_min"].ge(35)),
        ("head==1 & p1>=.55", df["head1"] & df["p1"].ge(0.55)),
        ("outer head & p1>=.40", df["outer_head"] & df["p1"].ge(0.40)),
    ]
    for label, mask in combos:
        add_row(rows, label, mask, df)

    ranked = sorted(
        [r for r in rows if r["test"]["n"] >= 500],
        key=lambda r: (r["test"]["roi"], r["test"]["hit_rate"]),
        reverse=True,
    )

    lines = [
        "# 2-renpuku strength-filter edge check",
        "",
        f"- Data: `{PKL}`",
        f"- Period: {df['race_date_str'].min()} to {df['race_date_str'].max()}",
        f"- Split: train < {SPLIT}, test >= {SPLIT}",
        "- Bet: one-point quinella on model top2 boats, 100 yen per race",
        "- Note: this does not use pre-race quinella odds because the DB only stores winning quinella payout.",
        "",
        "## Top candidates",
        "",
        "| Filter | Train | Test | All |",
        "|---|---:|---:|---:|",
    ]
    for r in ranked[:18]:
        lines.append(f"| {r['label']} | {fmt(r['train'])} | **{fmt(r['test'])}** | {fmt(r['all'])} |")

    lines.extend(
        [
            "",
            "## Year check",
            "",
            "| Filter | Year | n | Hit | ROI | Avg hit pay | Break-even pay |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for r in ranked[:5]:
        mask = next(row_mask for row_label, row_mask in [
            ("ALL model top2 quinella", base),
            *[(f"p1 >= {p1:.2f}", df["p1"].ge(p1)) for p1 in [0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65]],
            *[(f"margin >= {margin:.2f}", df["margin"].ge(margin)) for margin in [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35]],
            *[(f"p1*p2 >= {pp:.2f}", df["pair_prob"].ge(pp)) for pp in [0.08, 0.10, 0.12, 0.14, 0.16]],
            *combos,
        ] if row_label == r["label"])
        sub = df[mask]
        for year, yd in sub.groupby("year"):
            m = metrics(yd)
            be = (10000.0 / m["hit_rate"]) if m["hit_rate"] else 0.0
            lines.append(
                f"| {r['label']} | {year} | {int(m['n']):,} | {m['hit_rate']:.1f}% | "
                f"{m['roi']:.1f}% | {m['avg_hit_pay']:.0f} | {be:.0f} |"
            )

    lines.extend(
        [
            "",
            "## Verdict",
            "",
            "The tested strength filters raise hit rate but do not create a durable positive ROI on quinella one-point bets.",
            "The best large-sample test ROI remains below breakeven. This means strength alone is not enough; a real pre-race odds filter is needed before adopting the idea.",
            "",
            "## Leakage warning",
            "",
            "Filtering by `win_qn_pay` would be future data because it is only known for the winning pair after the race.",
            "To test the heard method correctly, the app must collect the full pre-race quinella odds board for every target race and combination.",
        ]
    )
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT}")
    print("\n".join(lines[:28]))


if __name__ == "__main__":
    main()
