"""Fill bN_entry_change_rate on existing as-of feature rows.

  python scripts/backfill_entry_change_rate.py --dry-run
  python scripts/backfill_entry_change_rate.py

Schema v11 added the entry-change (前づけ) rate.  Rebuilding every feature row
just to gain one column would take hours and would rewrite values that are
already correct, so this fills the new column in place and moves the row to
v11.  No other column is touched.

The rate is the share of a racer's starts, over the 365 days ending on the
row's own ``asof_date``, where the restored actual course differed from the
frame they were assigned.  ``asof_date`` is the day before the race, so no
information from the race day itself can reach the value.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from array import array
from bisect import bisect_left, bisect_right
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.features.asof_builder import ENTRY_CHANGE_MIN_STARTS  # noqa: E402

DEFAULT_DB = ROOT / "data" / "kachisuji_search.db"
WINDOW_DAYS = 364          # asof を含む 365 日
BATCH = 20000


def _key(iso: str) -> int:
    """'2026-09-09' → 20260909。日付を整数にして軽くする。"""
    return int(iso[:4]) * 10000 + int(iso[5:7]) * 100 + int(iso[8:10])


def _window_start(asof: str) -> int:
    from datetime import date, timedelta

    d = date.fromisoformat(asof) - timedelta(days=WINDOW_DAYS)
    return d.year * 10000 + d.month * 100 + d.day


def load_history(db: Path) -> dict[int, tuple[array, array]]:
    """選手ごとに（出走日, 累積の進入変更回数）を作る。"""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    dates: dict[int, array] = {}
    prefix: dict[int, array] = {}
    rows = conn.execute(
        "SELECT racer_number, race_date, boat_number, course_number "
        "FROM start_timing_events "
        "WHERE course_number BETWEEN 1 AND 6 AND boat_number BETWEEN 1 AND 6 "
        "ORDER BY racer_number, race_date"
    )
    for racer, race_date, boat, course in rows:
        racer = int(racer)
        d = dates.get(racer)
        if d is None:
            d = dates[racer] = array("i")
            prefix[racer] = array("i", [0])
        d.append(_key(race_date))
        p = prefix[racer]
        p.append(p[-1] + (1 if int(course) != int(boat) else 0))
    conn.close()
    return {racer: (dates[racer], prefix[racer]) for racer in dates}


def rate_for(history, racer, asof: str, start_key: int) -> float | None:
    if racer is None:
        return None
    found = history.get(int(racer))
    if found is None:
        return None
    dates, prefix = found
    left = bisect_left(dates, start_key)
    right = bisect_right(dates, _key(asof))
    starts = right - left
    if starts < ENTRY_CHANGE_MIN_STARTS:
        return None
    return (prefix[right] - prefix[left]) * 100.0 / starts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="この行数だけ書いて止める (速度計測用)")
    a = ap.parse_args()

    print("履歴を読み込み中…", flush=True)
    history = load_history(a.db)
    events = sum(len(d) for d, _ in history.values())
    print(f"  選手 {len(history):,} 人 / 出走 {events:,} 件", flush=True)

    # 読み切ってから書く。カーソルを開いたまま同じ DB へ書くと、読み手の
    # 共有ロックと書き手の排他ロックが取り合いになり、ほとんど進まない
    # (2026-09-10: 2 時間で 0 行)。先に全部メモリへ載せてから書く。
    print("対象行を読み込み中…", flush=True)
    read = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True, timeout=60)
    targets = read.execute(
        "SELECT race_id, asof_date, b1_racer_id, b2_racer_id, b3_racer_id, "
        "b4_racer_id, b5_racer_id, b6_racer_id FROM asof_race_features "
        "WHERE schema_version < 11"
    ).fetchall()
    read.close()
    print(f"  対象 {len(targets):,} 行", flush=True)
    if not targets:
        print("  すでに全行が v11 です。")
        return 0

    print("計算中…", flush=True)
    cache: dict[str, int] = {}
    updates: list[tuple] = []
    filled = 0
    for race_id, asof, *racers in targets:
        start_key = cache.get(asof)
        if start_key is None:
            start_key = cache[asof] = _window_start(asof)
        values = [rate_for(history, racer, asof, start_key) for racer in racers]
        filled += sum(value is not None for value in values)
        updates.append((*values, race_id))
    targets.clear()
    history.clear()
    print(f"  値が入った艇 {filled:,} 個"
          f"（{filled / (len(updates) * 6) * 100:.1f}%）", flush=True)

    if a.dry_run:
        print("  --dry-run のため書き込みませんでした。")
        return 0
    if a.limit:
        updates = updates[: a.limit]
        print(f"  --limit のため {len(updates):,} 行だけ書きます。", flush=True)

    sql = (
        "UPDATE asof_race_features SET "
        "b1_entry_change_rate=?, b2_entry_change_rate=?, b3_entry_change_rate=?, "
        "b4_entry_change_rate=?, b5_entry_change_rate=?, b6_entry_change_rate=?, "
        "schema_version=11 WHERE race_id=?"
    )
    write = sqlite3.connect(a.db, timeout=120)
    write.execute("PRAGMA synchronous=NORMAL")
    started = time.time()
    for offset in range(0, len(updates), BATCH):
        chunk = updates[offset : offset + BATCH]
        write.executemany(sql, chunk)
        write.commit()
        done = offset + len(chunk)
        rate = done / max(time.time() - started, 0.001)
        left = (len(updates) - done) / rate if rate else 0
        print(f"  {done:,} / {len(updates):,} 行  "
              f"({rate:,.0f} 行/秒, 残り {left / 60:.1f} 分)", flush=True)
    write.close()
    print()
    print(f"  {len(updates):,} 行を書きました。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
