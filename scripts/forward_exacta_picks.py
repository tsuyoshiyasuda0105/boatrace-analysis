"""二連単の前向き記録を作る / 確定させる / 集計する。

  python scripts/forward_exacta_picks.py --date 2026-09-19        # 翌日の候補を記録
  python scripts/forward_exacta_picks.py --settle                  # 結果が出た分を確定
  python scripts/forward_exacta_picks.py --report                  # 選別あり/なしの回収率

読む先は PC のローカル (data/boatrace.db の predictions・race_payouts、
data/kachisuji_slim.db の当日 forward 行)。書く先は db_connect() が返す DB
(DATABASE_URL があれば本番 Postgres、無ければローカル SQLite)。
本番の odds-cron はこの表に載ったレースの二連単オッズを締切5分前に取る。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.db.connection import connect as db_connect  # noqa: E402
from src.evaluation import exacta_forward as fx  # noqa: E402

LOCAL_DB = ROOT / "data" / "boatrace.db"
SLIM_DB = ROOT / "data" / "kachisuji_slim.db"


def _ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def load_races(target_date: str, local_db: Path = LOCAL_DB, slim_db: Path = SLIM_DB,
               model_version: str = fx.MODEL_VERSION) -> list[dict]:
    """予測 (6 艇そろい) と当日 forward 行があるレースだけ返す。"""
    with _ro(local_db) as b:
        pred = b.execute(
            """SELECT p.race_id, p.boat_number, p.prob_first, p.prob_top_2, p.prob_top_3
                 FROM predictions p JOIN races r ON r.race_id = p.race_id
                WHERE r.race_date = ? AND p.model_version = ?
                ORDER BY p.race_id, p.boat_number""",
            (target_date, model_version),
        ).fetchall()
    by_race: dict[str, list] = {}
    for rid, boat, p1, p2, p3 in pred:
        by_race.setdefault(rid, []).append((boat, p1, p2, p3))
    with _ro(slim_db) as s:
        feats = s.execute(
            """SELECT race_id, jcd, female_present,
                      b2_entry_change_rate, b3_entry_change_rate, b4_entry_change_rate,
                      b5_entry_change_rate, b6_entry_change_rate
                 FROM asof_race_features WHERE race_date = ?""",
            (target_date,),
        ).fetchall()
    feat_by_race = {r[0]: r for r in feats}
    races = []
    for rid, rows in by_race.items():
        if len(rows) != 6 or [r[0] for r in rows] != [1, 2, 3, 4, 5, 6]:
            continue
        if any(r[1] is None or r[2] is None or r[3] is None for r in rows):
            continue
        f = feat_by_race.get(rid)
        if f is None:
            continue
        races.append({
            "race_id": rid,
            "race_date": target_date,
            "jcd": f[1],
            "female_present": f[2],
            "entry_change_rates": list(f[3:8]),
            "prob_first": [r[1] for r in rows],
            "prob_top_2": [r[2] for r in rows],
            "prob_top_3": [r[3] for r in rows],
        })
    return races


def cmd_build(target_date: str) -> int:
    races = load_races(target_date)
    picks = fx.build_picks(races)
    with db_connect() as conn:
        n = fx.save_picks(conn, picks)
    sel = sum(p["selected"] for p in picks)
    print(f"[forward-exacta] {target_date}: races={len(races)} recorded={n} selected={sel}")
    for p in picks:
        if p["selected"]:
            print(f"    {p['race_id']} {p['combination']} p={p['prob']:.3f}")
    return 0


def cmd_settle(until_date: str, local_db: Path = LOCAL_DB) -> int:
    with _ro(local_db) as b:
        rows = b.execute(
            """SELECT p.race_id, REPLACE(p.combination, '=', '-'), MAX(p.payout)
                 FROM race_payouts p JOIN races r ON r.race_id = p.race_id
                WHERE p.bet_type = 'exacta' AND p.payout > 0
                  AND r.race_date >= ? AND r.race_date <= ?
                GROUP BY p.race_id, REPLACE(p.combination, '=', '-')""",
            ((date.fromisoformat(until_date) - timedelta(days=60)).isoformat(), until_date),
        ).fetchall()
    payouts = {(rid, comb): int(pay) for rid, comb, pay in rows}
    with db_connect() as conn:
        fx.ensure_table(conn)
        pending = conn.execute(
            "SELECT race_id, combination FROM forward_exacta_picks WHERE settled = 0 AND race_date <= ?",
            (until_date,),
        ).fetchall()
        t5: dict[tuple[str, str], float] = {}
        if pending:
            try:
                for rid, comb in pending:
                    row = conn.execute(
                        "SELECT odds FROM odds_exacta WHERE race_id = ? AND combination = ? "
                        "AND snapshot_label = 'T-5min' ORDER BY recorded_at DESC LIMIT 1",
                        (rid, comb),
                    ).fetchone()
                    if row and row[0] is not None:
                        t5[(rid, comb)] = float(row[0])
            except Exception as exc:  # noqa: BLE001 - odds_exacta がまだ無い環境
                print(f"[forward-exacta] odds_exacta unavailable: {type(exc).__name__}: {exc}")
                try:
                    conn.rollback()
                except Exception:  # noqa: BLE001
                    pass
        n = fx.settle(conn, payouts, t5, until_date)
    print(f"[forward-exacta] settled {n} rows up to {until_date} (t5 odds for {len(t5)})")
    return 0


def cmd_report() -> int:
    with db_connect() as conn:
        fx.ensure_table(conn)
        for row in fx.summarize(conn):
            label = "選別あり" if row["selected"] else "選別なし"
            print(
                f"{label}: {row['races']}R 的中{row['hits']} ({row['hit_rate']:.1f}%) "
                f"回収率 {row['roi']:.1f}%  {row['from']}〜{row['to']}"
            )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="候補を作る日 (既定: 明日)")
    ap.add_argument("--settle", action="store_true", help="昨日までの結果を確定")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    rc = 0
    if args.settle:
        rc |= cmd_settle((datetime.now().date() - timedelta(days=1)).isoformat())
    if args.report:
        rc |= cmd_report()
    if not args.settle and not args.report:
        target = args.date or (datetime.now().date() + timedelta(days=1)).isoformat()
        rc |= cmd_build(target)
    elif args.date:
        rc |= cmd_build(args.date)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
