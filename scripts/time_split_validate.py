"""時期分割検証 (train/test split validation)。

search_strategies.py や verification_agent.py が発見した候補手法は
data-snooping (多重比較) で偽優位が混入する。それを炙り出すために、
全期間を 2 分割し、train と test の両方で生き残る手法だけを採用候補とする。

判定:
  - robust    : train ROI ≥ THRESHOLD かつ test ROI ≥ THRESHOLD
  - one-sided : train か test の一方だけ ≥ THRESHOLD (偽優位の典型)
  - dead      : 両方とも < THRESHOLD

使い方:
    # 内蔵 TOP_PICKS (search 1-4 の交集合候補) を一括検証
    python scripts/time_split_validate.py

    # ratio で訓練/検証割合を変更 (default 0.5)
    python scripts/time_split_validate.py --ratio 0.6

    # しきい値変更
    python scripts/time_split_validate.py --threshold 130

実装:
  - 期間中央日を計算 → race_date で train/test 分割
  - backtest_method を date range 付きで実行
  - 並べて表示 + markdown 出力
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

import os
import sqlite3

import config
from src.verification.backtest import _build_where, _tier


# search 1-3 で頻出した候補 + race番号別新発見 5件
# (会場名で書くため STADIUMS dict との対応をコメント)
TOP_PICKS = [
    # 3本検索ヒット
    {"stadium": [19], "racer_class": [2], "weather_exclude": [3],
     "finish_pattern": "1-3-2", "bet_type": "trifecta",
     "label": "下関 × A2 × 雨除外 × 1-3-2"},
    {"stadium": [7], "racer_class": [1], "weather_exclude": [3],
     "finish_pattern": "1-2-3", "bet_type": "trifecta",
     "label": "蒲郡 × A1 × 雨除外 × 1-2-3 (L4 universe)"},
    {"stadium": [4], "weather_exclude": [3],
     "finish_pattern": "1-2-3", "bet_type": "trifecta",
     "label": "平和島 × 雨除外 × 1-2-3"},
    {"stadium": [7], "racer_class": [1],
     "finish_pattern": "1-2-3", "bet_type": "trifecta",
     "label": "蒲郡 × A1 × 1-2-3"},
    {"stadium": [19], "racer_class": [2],
     "finish_pattern": "1-3-2", "bet_type": "trifecta",
     "label": "下関 × A2 × 1-3-2"},
    {"stadium": [17], "racer_class": [2], "weather_exclude": [3],
     "finish_pattern": "1-3-2", "bet_type": "trifecta",
     "label": "宮島 × A2 × 雨除外 × 1-3-2"},
    {"stadium": [4],
     "finish_pattern": "1-2-3", "bet_type": "trifecta",
     "label": "平和島 × 1-2-3"},
    # race番号別 (search 2)
    {"racer_class": [1], "race_number": [3], "weather_exclude": [3],
     "finish_pattern": "1-2-3", "bet_type": "trifecta",
     "label": "A1 × 3R × 雨除外 × 1-2-3"},
    {"racer_class": [2], "race_number": [12], "weather_exclude": [3],
     "finish_pattern": "1-2-3", "bet_type": "trifecta",
     "label": "A2 × 12R × 雨除外 × 1-2-3"},
    {"racer_class": [2], "race_number": [4],
     "finish_pattern": "1-3-2", "bet_type": "trifecta",
     "label": "A2 × 4R × 1-3-2"},
    {"racer_class": [1], "race_number": [4],
     "finish_pattern": "1-2-3", "bet_type": "trifecta",
     "label": "A1 × 4R × 1-2-3"},
    {"racer_class": [2], "race_number": [5], "weather_exclude": [3],
     "finish_pattern": "1-3-2", "bet_type": "trifecta",
     "label": "A2 × 5R × 雨除外 × 1-3-2"},
    # ▼ 4号艇カド分析の新発見 (boat4_kado.py より)
    # NOTE: backtest_method の WHERE は boat 1 ベースなので、ここでは
    # 会場 + 1号艇 A1 で部分代用。本来は boat 4 専用 WHERE が必要。
    # 取り急ぎ会場別フルレース × boat4 単勝 LIKE 検証は別途。
]


# 4号艇カド単勝の追加 TOP_PICKS (boat4 専用 WHERE を後で実装)
BOAT4_PICKS = [
    {"stadium": [21], "bet_type": "win", "boat4_class": [1],
     "boat4_motor_top2_min": 40.0, "bet_combo": "4",
     "label": "芦屋 × 4号艇A1 × motor40+ 単勝"},
    {"stadium": [17], "bet_type": "win", "boat4_class": [1],
     "boat4_motor_top2_min": 40.0, "bet_combo": "4",
     "label": "宮島 × 4号艇A1 × motor40+ 単勝"},
    {"stadium": [7], "bet_type": "win", "boat4_class": [1],
     "boat4_motor_top2_min": 40.0, "bet_combo": "4",
     "label": "蒲郡 × 4号艇A1 × motor40+ 単勝"},
    {"stadium": [6], "bet_type": "win", "boat4_class": [1],
     "boat4_motor_top2_min": 40.0, "bet_combo": "4",
     "label": "浜名湖 × 4号艇A1 × motor40+ 単勝"},
]


def _conn():
    if os.getenv("DATABASE_URL", "").strip():
        try:
            from src.db.connection import connect as db_connect
            return db_connect()
        except Exception:  # noqa: BLE001
            pass
    return sqlite3.connect(config.DB_PATH)


def find_split_date(ratio: float) -> str:
    """全レースの race_date を期間順で取得し ratio で分割する境界日を返す。"""
    conn = _conn()
    rows = conn.execute(
        "SELECT MIN(race_date), MAX(race_date) FROM races"
    ).fetchone()
    conn.close()
    if not rows or not rows[0]:
        return date.today().isoformat()
    start = datetime.strptime(rows[0], "%Y-%m-%d").date()
    end = datetime.strptime(rows[1], "%Y-%m-%d").date()
    span = (end - start).days
    split = start.toordinal() + int(span * ratio)
    return date.fromordinal(split).isoformat()


def backtest_in_range(cond: dict, date_from: str, date_to: str) -> dict:
    """指定日付範囲で backtest_method 相当を実行 (期間条件を WHERE に追加)。"""
    where, args, joins = _build_where(cond)
    bet_type = cond.get("bet_type", "trifecta")
    finish_pat = cond.get("finish_pattern")
    bet_combo = finish_pat if finish_pat and "-" in finish_pat else "1-2-3"
    joins_str = "\n          ".join(joins)
    full_where = f"{where} AND r.race_date >= ? AND r.race_date <= ?"
    args_full = list(args) + [date_from, date_to]

    sql_total = f"""
        SELECT COUNT(DISTINCT r.race_id)
          FROM races r
          LEFT JOIN race_entries e1 ON e1.race_id=r.race_id AND e1.boat_number=1
          LEFT JOIN race_previews pv ON pv.race_id=r.race_id AND pv.boat_number=1
          {joins_str}
         WHERE {full_where}
    """
    sql_hits = f"""
        SELECT COUNT(*), COALESCE(SUM(pp.payout), 0)
          FROM races r
          LEFT JOIN race_entries e1 ON e1.race_id=r.race_id AND e1.boat_number=1
          LEFT JOIN race_previews pv ON pv.race_id=r.race_id AND pv.boat_number=1
          {joins_str}
          JOIN race_payouts pp ON pp.race_id=r.race_id
                              AND pp.bet_type=? AND pp.combination=?
         WHERE {full_where}
    """
    conn = _conn()
    try:
        n = conn.execute(sql_total, args_full).fetchone()[0] or 0
        if n == 0:
            return {"n_races": 0, "roi": 0.0, "n_hits": 0}
        row = conn.execute(sql_hits, [bet_type, bet_combo] + args_full).fetchone()
        hits = row[0] or 0
        pay = int(row[1] or 0)
    finally:
        conn.close()
    roi = (pay / (100 * n) * 100) if n else 0.0
    return {"n_races": n, "n_hits": hits, "sum_payout": pay,
            "roi": roi, "hit_rate": (hits / n * 100) if n else 0.0}


def backtest_boat4(stadium: list[int], boat4_class: list[int],
                   motor_min: float, date_from: str, date_to: str,
                   bet_combo: str = "4", bet_type: str = "win") -> dict:
    """4号艇 単勝 (or 3連単 4-x-y 全流し) のバックテスト。"""
    where_parts = ["e4.boat_number=4", "rr.boat_number=4"]
    args: list = []
    if stadium:
        ph = ",".join("?" * len(stadium))
        where_parts.append(f"r.stadium_number IN ({ph})")
        args.extend(stadium)
    if boat4_class:
        ph = ",".join("?" * len(boat4_class))
        where_parts.append(f"e4.class_number IN ({ph})")
        args.extend(boat4_class)
    if motor_min is not None:
        where_parts.append("e4.assigned_motor_top_2_percent >= ?")
        args.append(float(motor_min))
    where_parts.append("r.race_date >= ? AND r.race_date <= ?")
    args.append(date_from)
    args.append(date_to)
    where = " AND ".join(where_parts)

    # bet_combo = "4" = 単勝, "4-all" = 3連単 4頭流し
    if bet_combo == "4" and bet_type == "win":
        sql = f"""
            SELECT COUNT(*) AS n_races,
                   SUM(CASE WHEN rr.finishing_position=1 THEN 1 ELSE 0 END) AS wins,
                   COALESCE(SUM(CASE WHEN rr.finishing_position=1
                                THEN COALESCE(pw.payout,0) ELSE 0 END),0) AS pay
              FROM races r
              JOIN race_entries e4 ON e4.race_id=r.race_id
              JOIN race_results rr ON rr.race_id=r.race_id
              LEFT JOIN race_payouts pw ON pw.race_id=r.race_id
                                  AND pw.bet_type='win' AND pw.combination='4'
             WHERE {where}
        """
        cost_per_race = 100
    elif bet_combo == "4-all" and bet_type == "trifecta":
        # 3連単 4頭-全流し (20点 = 2000円/race)
        sql = f"""
            SELECT COUNT(*) AS n_races,
                   SUM(CASE WHEN rr.finishing_position=1 THEN 1 ELSE 0 END) AS wins,
                   COALESCE(SUM(CASE WHEN rr.finishing_position=1
                                THEN COALESCE(pt.payout,0) ELSE 0 END),0) AS pay
              FROM races r
              JOIN race_entries e4 ON e4.race_id=r.race_id
              JOIN race_results rr ON rr.race_id=r.race_id
              LEFT JOIN race_payouts pt ON pt.race_id=r.race_id
                                  AND pt.bet_type='trifecta' AND pt.combination LIKE ?
             WHERE {where}
        """
        args = ["4-%"] + args
        cost_per_race = 2000
    else:
        return {"error": f"unknown bet_combo {bet_combo}"}

    conn = _conn()
    try:
        n, wins, pay = conn.execute(sql, args).fetchone()
        n = n or 0; wins = wins or 0; pay = int(pay or 0)
    finally:
        conn.close()
    roi = pay / (cost_per_race * n) * 100 if n else 0.0
    return {"n_races": n, "wins": wins, "sum_payout": pay,
            "roi": roi, "win_rate": (wins / n * 100) if n else 0.0}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ratio", type=float, default=0.5,
                        help="train 比率 (0.5 = 前半50%%)")
    parser.add_argument("--threshold", type=float, default=130.0,
                        help="robust 判定の ROI 閾値 (%%)")
    parser.add_argument("--output", default="reports")
    args = parser.parse_args()

    split_date = find_split_date(args.ratio)
    print(f"=== 時期分割検証 (split={split_date}, ratio={args.ratio}) ===\n")

    # 各 method を train / test 両方で評価
    results = []
    for m in TOP_PICKS:
        cond = {k: v for k, v in m.items() if k != "label"}
        # train: 〜 split_date 前日
        from datetime import timedelta
        split_d = datetime.strptime(split_date, "%Y-%m-%d").date()
        prev = (split_d - timedelta(days=1)).isoformat()
        train = backtest_in_range(cond, "0000-01-01", prev)
        test = backtest_in_range(cond, split_date, "9999-12-31")
        results.append({"label": m["label"], "train": train, "test": test})
        verdict = (
            "🏆 robust" if train["roi"] >= args.threshold and test["roi"] >= args.threshold
            else "⚠ one-sided" if train["roi"] >= args.threshold or test["roi"] >= args.threshold
            else "❌ dead")
        print(f"  [{verdict:<12}] train n={train['n_races']:>5} ROI={train['roi']:>6.1f}% "
              f"| test n={test['n_races']:>5} ROI={test['roi']:>6.1f}% | {m['label']}")

    # markdown 出力
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    fpath = out_dir / f"validate_{datetime.now():%Y%m%d_%H%M}.md"
    lines = [f"# 時期分割検証レポート  {datetime.now():%Y-%m-%d %H:%M}", "",
             f"- 分割日: **{split_date}** (train ≤ {prev} / test ≥ {split_date})",
             f"- robust 判定: train・test 両方 ROI ≥ **{args.threshold}%**",
             "",
             "| 手法 | train n | train ROI | test n | test ROI | 判定 |",
             "|------|---------|-----------|--------|----------|------|"]
    for r in results:
        tr, te = r["train"], r["test"]
        verdict = (
            "🏆 robust" if tr["roi"] >= args.threshold and te["roi"] >= args.threshold
            else "⚠ one-sided" if tr["roi"] >= args.threshold or te["roi"] >= args.threshold
            else "❌ dead")
        lines.append(
            f"| {r['label']} | {tr['n_races']:,} | {tr['roi']:.1f}% | "
            f"{te['n_races']:,} | {te['roi']:.1f}% | {verdict} |")
    # ▼ BOAT4 PICKS の検証 (単勝 + 4頭流し 2 種)
    print("\n=== 4号艇カド候補の時期分割検証 ===\n")
    boat4_lines = ["", "## 4号艇カド候補 (単勝 & 4頭流し)", "",
                   "| 手法 | bet | train n | train ROI | test n | test ROI | 判定 |",
                   "|------|-----|---------|-----------|--------|----------|------|"]
    for m in BOAT4_PICKS:
        for combo, btype in [("4", "win"), ("4-all", "trifecta")]:
            tr = backtest_boat4(m["stadium"], m.get("boat4_class") or [],
                                m.get("boat4_motor_top2_min") or 0,
                                "0000-01-01", prev, combo, btype)
            te = backtest_boat4(m["stadium"], m.get("boat4_class") or [],
                                m.get("boat4_motor_top2_min") or 0,
                                split_date, "9999-12-31", combo, btype)
            verdict = (
                "🏆 robust" if tr["roi"] >= args.threshold and te["roi"] >= args.threshold
                else "⚠ one-sided" if tr["roi"] >= args.threshold or te["roi"] >= args.threshold
                else "❌ dead")
            bet_lbl = "単勝" if combo == "4" else "3連単4頭流し"
            label_full = f"{m['label']} ({bet_lbl})"
            print(f"  [{verdict:<12}] train n={tr['n_races']:>4} ROI={tr['roi']:>6.1f}% "
                  f"| test n={te['n_races']:>4} ROI={te['roi']:>6.1f}% | {label_full}")
            boat4_lines.append(
                f"| {m['label']} | {bet_lbl} | {tr['n_races']:,} | {tr['roi']:.1f}% | "
                f"{te['n_races']:,} | {te['roi']:.1f}% | {verdict} |")

    fpath.write_text("\n".join(lines + boat4_lines) + "\n", encoding="utf-8")
    print(f"\nレポート出力: {fpath}")


if __name__ == "__main__":
    main()
