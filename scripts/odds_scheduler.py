"""
オッズスナップショット スケジューラー

毎分起動 → race_closed_at を見て、各レースに対して:
  - 締切 24h 前 ±2分 (大きいレースのみ): T-1d スナップショット
  - 締切 5分前 ±1分: T-5min
  - 締切 1分前 ±30秒: T-1min
  - 締切後 30分以内: final (race_results が入ったら)

「大きいレース」判定:
  - race_grade_number IN (1, 2)  (SG / G1)
  - is_yusho = 1
  - is_jun_yusho = 1

Windows Task Scheduler から毎分起動する想定:
    schtasks /Create /TN "BoatraceOddsScheduler" /SC MINUTE /MO 1 ...

usage:
    python scripts/odds_scheduler.py        # 1回スキャンして終了 (cron用)
    python scripts/odds_scheduler.py --daemon  # 常駐モード (60秒ループ)
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.collectors.odds import collect_one_race
from src.db.connection import connect as db_connect


# JST (UTC+9) ベースで race_closed_at が記録されている前提
JST = timezone(timedelta(hours=9))


# 各 snapshot label のターゲット時間と許容ウィンドウ
# (label, minutes_before_close, tolerance_minutes)
SNAPSHOT_RULES = [
    ("T-15min", 15, 2),  # 締切15分前 ±2分 (Pro 期待値表示用)
    ("T-5min", 5, 1),    # 締切5分前 ±1分
    ("T-1min", 1, 1),    # 締切1分前 ±1分
]
# 大きいレース用
BIG_SNAPSHOT_RULES = [
    ("T-1d", 24 * 60, 5),  # 24時間前 ±5分
] + SNAPSHOT_RULES


def _is_big_race(race: dict) -> bool:
    g = race.get("race_grade_number")
    return (g in (1, 2)) or bool(race.get("is_yusho")) or bool(race.get("is_jun_yusho"))


def _parse_close_jst(closed_at: str, race_date: str) -> datetime:
    """
    race_closed_at は 'HH:MM:SS' or 'YYYY-MM-DD HH:MM:SS' or 同 JST 形式と推定。
    JST のローカルタイム値として扱い、TZ 付きで返す。
    """
    s = closed_at.strip()
    try:
        if " " in s and len(s) >= 16:
            # 'YYYY-MM-DD HH:MM[:SS]'
            t = datetime.fromisoformat(s)
        else:
            # 'HH:MM:SS' のみ → race_date と合成
            time_part = s if len(s) >= 5 else f"{s}:00"
            t = datetime.fromisoformat(f"{race_date} {time_part}")
    except ValueError:
        return None
    return t.replace(tzinfo=JST)


def find_due_snapshots(now_jst: datetime, lookahead_min: int = 30) -> list[tuple[str, str]]:
    """
    今この瞬間に取得すべき (race_id, snapshot_label) のリストを返す。
    既に同 label が取得済みなら除外。
    """
    sql = """
        SELECT r.race_id, r.race_date, r.race_closed_at,
               r.race_grade_number, r.is_yusho, r.is_jun_yusho
          FROM races r
         WHERE r.race_date BETWEEN ? AND ?
           AND r.race_closed_at IS NOT NULL
    """
    with db_connect() as conn:
        rows = conn.execute(
            sql,
            (
                (now_jst - timedelta(days=2)).date().isoformat(),
                (now_jst + timedelta(days=2)).date().isoformat(),
            ),
        ).fetchall()
        # 既に取得済みの (race_id, snapshot_label) セット
        existing = set()
        for r in conn.execute(
            "SELECT DISTINCT race_id, snapshot_label FROM odds_trifecta WHERE snapshot_label IS NOT NULL"
        ).fetchall():
            existing.add((r[0], r[1]))

    due: list[tuple[str, str]] = []
    keys = ["race_id", "race_date", "race_closed_at",
            "race_grade_number", "is_yusho", "is_jun_yusho"]
    for row in rows:
        race = dict(zip(keys, row))
        close = _parse_close_jst(race["race_closed_at"], race["race_date"])
        if close is None:
            continue
        rules = BIG_SNAPSHOT_RULES if _is_big_race(race) else SNAPSHOT_RULES
        for label, mins_before, tol in rules:
            target = close - timedelta(minutes=mins_before)
            delta = (now_jst - target).total_seconds() / 60.0
            if -tol <= delta <= tol:
                if (race["race_id"], label) in existing:
                    continue
                due.append((race["race_id"], label))
    return due


def run_one_pass(verbose: bool = False) -> dict:
    """1回スキャン → 該当レースに対しスナップショット取得"""
    now_jst = datetime.now(tz=JST)
    due = find_due_snapshots(now_jst)
    if verbose:
        print(f"[{now_jst.strftime('%H:%M:%S')}] due snapshots: {len(due)}")

    summary = {"now": now_jst.isoformat(), "n_due": len(due), "n_done": 0, "items": []}
    for race_id, label in due:
        try:
            r = collect_one_race(race_id, snapshot_label=label)
            summary["items"].append(r)
            if r.get("odds_inserted", 0) > 0:
                summary["n_done"] += 1
            if verbose:
                print(f"  {race_id} [{label}] inserted={r.get('odds_inserted', 0)}")
        except Exception as e:
            if verbose:
                print(f"  {race_id} [{label}] ERROR: {e}")
    return summary


def daemon_loop(interval_sec: int = 60, verbose: bool = False) -> None:
    while True:
        try:
            run_one_pass(verbose=verbose)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"[ERROR] {e}")
        time.sleep(interval_sec)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--daemon", action="store_true", help="60秒ループで常駐")
    p.add_argument("--interval", type=int, default=60)
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    if args.daemon:
        daemon_loop(interval_sec=args.interval, verbose=args.verbose)
    else:
        s = run_one_pass(verbose=args.verbose)
        print(f"due={s['n_due']} done={s['n_done']}")


if __name__ == "__main__":
    main()
