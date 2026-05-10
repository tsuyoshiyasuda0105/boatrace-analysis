"""
ニッチ +EV 探索 (会場 × 条件)

Discovery 期間で多数のサブグループを Bootstrap CI で評価し、
CI 下限が 0 を超える「統計的に有意な +EV ニッチ」を抽出する。

その後、別期間 (Validation) で同じ条件を再評価し、replication を確認する。

usage:
    # Step 1: Discovery
    python -m src.evaluation.niche_scanner --version v0.6 \\
        --date-from 2025-06-01 --date-to 2026-03-31 \\
        --mode discover --min-n 30 --n-iter 1000

    # Step 2: Validation (発見条件を別期間で確認)
    python -m src.evaluation.niche_scanner --version v0.6-test \\
        --date-from 2026-04-01 --date-to 2026-05-09 \\
        --mode validate --conditions discovery_top.json --n-iter 2000
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd

import config
from src.features.builder import build_training_frame
from src.evaluation.evaluate_with_payouts import load_artifact, predict_with_probs, load_payouts
from src.evaluation.bootstrap_ci import bootstrap_roi


STADIUM_NAMES = {
    1: "桐生", 2: "戸田", 3: "江戸川", 4: "平和島", 5: "多摩川", 6: "浜名湖",
    7: "蒲郡", 8: "常滑", 9: "津", 10: "三国", 11: "びわこ", 12: "住之江",
    13: "尼崎", 14: "鳴門", 15: "丸亀", 16: "児島", 17: "宮島", 18: "徳山",
    19: "下関", 20: "若松", 21: "芦屋", 22: "福岡", 23: "唐津", 24: "大村",
}


def build_picks_df(version: str, date_from: str, date_to: str) -> pd.DataFrame:
    """予測 + 実払戻を結合した bet レベルの DataFrame を返す"""
    artifact = load_artifact(version)
    df_test = build_training_frame(date_from=date_from, date_to=date_to)
    df_pred = predict_with_probs(artifact, df_test)

    # 各レースで argmax(prob_first) を選ぶ
    idx = df_pred.groupby("race_id")["prob_first"].idxmax()
    cols_keep = ["race_id", "stadium_number", "race_number", "boat_number",
                 "prob_first", "race_grade_number", "wind_speed", "wave_height",
                 "class_number", "national_top_2_percent",
                 "assigned_motor_top_2_percent"]
    cols_keep = [c for c in cols_keep if c in df_pred.columns]
    top1 = df_pred.loc[idx, cols_keep].copy()
    top1 = top1.rename(columns={"boat_number": "top1_boat",
                                "prob_first": "top1_prob",
                                "class_number": "top1_class",
                                "national_top_2_percent": "top1_top2pct",
                                "assigned_motor_top_2_percent": "top1_motor_top2"})

    df_from = date.fromisoformat(date_from)
    df_to = date.fromisoformat(date_to)
    win_p = load_payouts(config.DB_PATH, df_from, df_to, "win")
    win_map = dict(zip(zip(win_p["race_id"], win_p["combination"]), win_p["payout"]))

    rows = []
    for _, r in top1.iterrows():
        rid = r["race_id"]
        b = int(r["top1_boat"])
        ap = float(win_map.get((rid, str(b)), 0))
        d = {
            "race_id": rid,
            "stadium_number": int(r["stadium_number"]),
            "stadium_name": STADIUM_NAMES.get(int(r["stadium_number"]), "?"),
            "race_number": int(r["race_number"]) if "race_number" in r else 0,
            "top1_boat": b,
            "top1_prob": float(r["top1_prob"]),
            "race_grade": int(r.get("race_grade_number", 0)) if pd.notna(r.get("race_grade_number")) else 0,
            "wind_speed": float(r.get("wind_speed", 0)) if pd.notna(r.get("wind_speed")) else 0,
            "wave_height": float(r.get("wave_height", 0)) if pd.notna(r.get("wave_height")) else 0,
            "top1_class": int(r.get("top1_class", 0)) if pd.notna(r.get("top1_class")) else 0,
            "top1_top2pct": float(r.get("top1_top2pct", 0)) if pd.notna(r.get("top1_top2pct")) else 0,
            "top1_motor_top2": float(r.get("top1_motor_top2", 0)) if pd.notna(r.get("top1_motor_top2")) else 0,
            "actual_payout": ap,
            "hit": 1 if ap > 0 else 0,
        }
        rows.append(d)
    return pd.DataFrame(rows)


def scan_niches(df: pd.DataFrame, n_iter: int = 1000, min_n: int = 30) -> pd.DataFrame:
    """多次元サブグループを総当たりで Bootstrap CI 評価"""
    results = []

    def add(label: str, sub: pd.DataFrame, group: str):
        if len(sub) < min_n:
            return
        ci = bootstrap_roi(sub, n_iter=n_iter)
        results.append({
            "group": group,
            "label": label,
            "n": ci["n"],
            "hit_rate": sub["hit"].mean(),
            "roi": ci["roi_mean"],
            "ci_lo": ci["roi_lo"],
            "ci_hi": ci["roi_hi"],
            "p_positive": ci["p_positive"],
        })

    # 1) 会場別 (1号艇本命のみ)
    base = df[df["top1_boat"] == 1]
    for s in sorted(df["stadium_number"].unique()):
        sub = base[base["stadium_number"] == s]
        add(f"{STADIUM_NAMES.get(s, s)} (1号艇本命)", sub, "stadium")

    # 2) 会場 × prob_bucket
    prob_buckets = [(0.50, 0.60), (0.60, 0.70), (0.70, 0.80), (0.80, 0.90), (0.90, 1.01)]
    for s in sorted(df["stadium_number"].unique()):
        for lo, hi in prob_buckets:
            sub = base[(base["stadium_number"] == s)
                       & (base["top1_prob"] >= lo) & (base["top1_prob"] < hi)]
            add(f"{STADIUM_NAMES.get(s, s)} 1号艇 prob{lo:.2f}-{hi:.2f}", sub,
                "stadium_x_prob")

    # 3) レース番号別 (1R, 12R 等の時間帯バイアス) × 1号艇本命
    for r in sorted(df["race_number"].unique()):
        if r == 0:
            continue
        sub = base[base["race_number"] == r]
        add(f"R{r} (1号艇本命)", sub, "race_number")

    # 4) 風速バケット × 1号艇本命
    for lo, hi in [(0, 2), (2, 4), (4, 6), (6, 99)]:
        sub = base[(base["wind_speed"] >= lo) & (base["wind_speed"] < hi)]
        add(f"風速 {lo}-{hi}m", sub, "wind")

    # 5) 1号艇 class (A1=1, A2=2, B1=3, B2=4)
    for c in [1, 2, 3, 4]:
        sub = base[base["top1_class"] == c]
        cls_name = {1: "A1", 2: "A2", 3: "B1", 4: "B2"}.get(c, str(c))
        add(f"1号艇 {cls_name}級", sub, "class")

    # 6) モーター top2% バケット
    for lo, hi in [(0, 30), (30, 40), (40, 50), (50, 100)]:
        sub = base[(base["top1_motor_top2"] >= lo) & (base["top1_motor_top2"] < hi)]
        add(f"モーター2連率 {lo}-{hi}%", sub, "motor")

    # 7) 会場 × class
    for s in sorted(df["stadium_number"].unique()):
        for c in [1, 2]:
            sub = base[(base["stadium_number"] == s) & (base["top1_class"] == c)]
            cls_name = {1: "A1", 2: "A2"}[c]
            add(f"{STADIUM_NAMES.get(s, s)} 1号艇{cls_name}", sub, "stadium_x_class")

    return pd.DataFrame(results)


def fmt_ci(lo, hi):
    return f"[{lo:+.3f},{hi:+.3f}]"


def cmd_discover(args):
    print(f"=== Niche Discovery ({args.version}) ===")
    print(f"  period: {args.date_from} .. {args.date_to}")
    df = build_picks_df(args.version, args.date_from, args.date_to)
    print(f"  bets:   {len(df):,}")
    print(f"  hit_rate (全体): {df['hit'].mean():.4f}\n")

    res = scan_niches(df, n_iter=args.n_iter, min_n=args.min_n)
    print(f"  scanned {len(res)} niches (min_n={args.min_n})")

    # 統計的有意 (CI 下限 > 0)
    sig = res[res["ci_lo"] > 0].sort_values("ci_lo", ascending=False)
    print(f"\n=== CI 下限 > 0 のニッチ (Discovery) ===\n")
    if len(sig) == 0:
        print("  該当無し")
    else:
        print(f"{'group':<18}{'label':<32}{'n':>6}{'hit':>8}{'ROI':>10}{'CI':>20}{'P>0':>7}")
        for _, r in sig.iterrows():
            print(f"{r['group']:<18}{r['label']:<32}{r['n']:>6}{r['hit_rate']:>8.3f}"
                  f"{r['roi']:>+10.4f}{fmt_ci(r['ci_lo'], r['ci_hi']):>20}{r['p_positive']:>7.2f}")

    # CI 下限 >= -0.02 (ボーダーライン候補) 上位
    border = res[(res["ci_lo"] > -0.02) & (res["ci_lo"] <= 0)].sort_values("ci_lo", ascending=False).head(15)
    if len(border) > 0:
        print(f"\n=== CI 下限 -2%〜0% (ボーダー、検証候補) ===\n")
        print(f"{'group':<18}{'label':<32}{'n':>6}{'hit':>8}{'ROI':>10}{'CI':>20}{'P>0':>7}")
        for _, r in border.iterrows():
            print(f"{r['group']:<18}{r['label']:<32}{r['n']:>6}{r['hit_rate']:>8.3f}"
                  f"{r['roi']:>+10.4f}{fmt_ci(r['ci_lo'], r['ci_hi']):>20}{r['p_positive']:>7.2f}")

    # 上位 ROI も参考表示 (n が小さいかも)
    top_roi = res.sort_values("roi", ascending=False).head(15)
    print(f"\n=== ROI 上位 15 (参考、CI 確認必要) ===\n")
    print(f"{'group':<18}{'label':<32}{'n':>6}{'hit':>8}{'ROI':>10}{'CI':>20}{'P>0':>7}")
    for _, r in top_roi.iterrows():
        print(f"{r['group']:<18}{r['label']:<32}{r['n']:>6}{r['hit_rate']:>8.3f}"
              f"{r['roi']:>+10.4f}{fmt_ci(r['ci_lo'], r['ci_hi']):>20}{r['p_positive']:>7.2f}")

    # 検証用の条件 JSON を出力
    if args.save_json:
        # 統計的有意 + ボーダー + Top ROI 上位 を全て検証候補に
        candidates = pd.concat([sig, border, top_roi]).drop_duplicates(subset=["label"])
        out = []
        for _, r in candidates.iterrows():
            out.append({
                "group": r["group"], "label": r["label"], "n_disc": int(r["n"]),
                "roi_disc": float(r["roi"]),
                "ci_lo_disc": float(r["ci_lo"]),
                "ci_hi_disc": float(r["ci_hi"]),
                "p_pos_disc": float(r["p_positive"]),
            })
        Path(args.save_json).write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
        print(f"\n  保存: {args.save_json} ({len(out)}件)")


def cmd_validate(args):
    """Discovery で抽出された条件を別期間で再評価"""
    print(f"=== Niche Validation ({args.version}) ===")
    print(f"  period: {args.date_from} .. {args.date_to}")
    df = build_picks_df(args.version, args.date_from, args.date_to)
    print(f"  bets: {len(df):,}\n")

    res = scan_niches(df, n_iter=args.n_iter, min_n=10)  # validation は min_n 緩める

    # discovery 条件 load
    disc = json.loads(Path(args.conditions).read_text(encoding="utf-8"))
    print(f"=== Validation: Discovery {len(disc)} 条件を再評価 ===\n")
    print(f"{'label':<32}{'n_d':>5}{'ROI_d':>8}{'n_v':>5}{'ROI_v':>8}{'CI_v':>20}"
          f"{'replicates':>12}")
    print("-" * 92)
    repl = 0
    for d in disc:
        match = res[res["label"] == d["label"]]
        if len(match) == 0:
            print(f"{d['label']:<32}{d['n_disc']:>5}{d['roi_disc']:>+8.3f}"
                  f"{'—':>5}{'—':>8}{'—':>20}{'no data':>12}")
            continue
        v = match.iloc[0]
        is_repl = "YES" if v["roi"] > 0 else "no"
        if v["roi"] > 0:
            repl += 1
        print(f"{d['label']:<32}{d['n_disc']:>5}{d['roi_disc']:>+8.3f}"
              f"{v['n']:>5}{v['roi']:>+8.3f}{fmt_ci(v['ci_lo'], v['ci_hi']):>20}"
              f"{is_repl:>12}")

    print(f"\n  replicated (ROI>0): {repl}/{len(disc)} ({repl/len(disc)*100:.0f}%)")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--version", required=True)
    p.add_argument("--date-from", required=True)
    p.add_argument("--date-to", required=True)
    p.add_argument("--mode", choices=["discover", "validate"], default="discover")
    p.add_argument("--n-iter", type=int, default=1000)
    p.add_argument("--min-n", type=int, default=30)
    p.add_argument("--save-json", default=None,
                   help="discover 時、ニッチ条件を JSON で保存")
    p.add_argument("--conditions", default=None,
                   help="validate 時、discover で出した JSON")
    args = p.parse_args()

    if args.mode == "discover":
        cmd_discover(args)
    else:
        if not args.conditions:
            raise ValueError("--conditions が必要")
        cmd_validate(args)


if __name__ == "__main__":
    main()
