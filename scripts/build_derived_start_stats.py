"""Build point-in-time derived start statistics for live strategy filters.

The accident-rate/ST dent strategies require each entry's historical average
start timing before the race day.  This script materializes those values into
``derived_start_stats`` so Render can evaluate the strategies without running
heavy per-race history scans.
"""
from __future__ import annotations

import argparse
import bisect
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.db.connection import connect as db_connect
from src.start_prediction.repository import StartPredictionRepository


@dataclass(frozen=True)
class TargetEntry:
    race_id: str
    race_date: date
    boat_number: int
    racer_number: int


@dataclass(frozen=True)
class HistoricalStart:
    race_date: date
    start_timing: float


def _parse_date(value: object) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _avg(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _target_entries(conn, from_date: str, to_date: str) -> list[TargetEntry]:
    rows = conn.execute(
        """
        SELECT r.race_id, r.race_date, e.boat_number, e.racer_number
          FROM races r
          JOIN race_entries e ON e.race_id = r.race_id
         WHERE r.race_date BETWEEN ? AND ?
         ORDER BY r.race_date, r.race_id, e.boat_number
        """,
        (from_date, to_date),
    ).fetchall()
    return [
        TargetEntry(
            race_id=str(race_id),
            race_date=_parse_date(race_date),
            boat_number=int(boat_number),
            racer_number=int(racer_number),
        )
        for race_id, race_date, boat_number, racer_number in rows
    ]


def _historical_starts(conn, warmup_from: str, to_date: str) -> dict[int, list[HistoricalStart]]:
    rows = conn.execute(
        """
        SELECT e.racer_number, r.race_date, rr.start_timing
          FROM races r
          JOIN race_entries e
            ON e.race_id = r.race_id
          JOIN race_results rr
            ON rr.race_id = e.race_id
           AND rr.boat_number = e.boat_number
         WHERE r.race_date BETWEEN ? AND ?
           AND rr.start_timing IS NOT NULL
         ORDER BY e.racer_number, r.race_date, r.race_id, e.boat_number
        """,
        (warmup_from, to_date),
    ).fetchall()
    by_racer: dict[int, list[HistoricalStart]] = defaultdict(list)
    for racer_number, race_date, start_timing in rows:
        try:
            by_racer[int(racer_number)].append(
                HistoricalStart(_parse_date(race_date), float(start_timing))
            )
        except (TypeError, ValueError):
            continue
    return dict(by_racer)


def build_rows(conn, from_date: str, to_date: str) -> list[tuple]:
    """Return derived_start_stats upsert rows for the inclusive date range."""
    targets = _target_entries(conn, from_date, to_date)
    if not targets:
        return []

    warmup_from = (date.fromisoformat(from_date) - timedelta(days=370)).isoformat()
    history = _historical_starts(conn, warmup_from, to_date)
    updated_at = datetime.now().isoformat(timespec="seconds")
    rows: list[tuple] = []

    dates_by_racer: dict[int, list[date]] = {
        racer: [item.race_date for item in starts]
        for racer, starts in history.items()
    }

    for target in targets:
        starts = history.get(target.racer_number, [])
        dates = dates_by_racer.get(target.racer_number, [])
        end_idx = bisect.bisect_left(dates, target.race_date)
        prior = starts[:end_idx]
        start_180 = target.race_date - timedelta(days=180)
        idx_180 = bisect.bisect_left(dates, start_180, 0, end_idx)
        values_180 = [item.start_timing for item in prior[idx_180:end_idx]]
        values_12 = [item.start_timing for item in prior[max(0, end_idx - 12):end_idx]]
        rows.append(
            (
                target.race_id,
                target.boat_number,
                _avg(values_180),
                len(values_180),
                _avg(values_12),
                len(values_12),
                updated_at,
            )
        )
    return rows


def _chunks(values: list[tuple], size: int) -> Iterable[list[tuple]]:
    for i in range(0, len(values), size):
        yield values[i:i + size]


def upsert_rows(conn, rows: list[tuple], *, dry_run: bool = False) -> int:
    if dry_run or not rows:
        return 0
    sql = """
        INSERT OR REPLACE INTO derived_start_stats (
          race_id,
          boat_number,
          derived_avg_start_timing_180d,
          derived_start_count_180d,
          derived_avg_start_timing_12,
          derived_start_count_12,
          updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
    """
    written = 0
    for batch in _chunks(rows, 1000):
        conn.executemany(sql, batch)
        written += len(batch)
    conn.commit()
    return written


def build(from_date: str, to_date: str, *, dry_run: bool = False) -> tuple[int, int]:
    with db_connect() as conn:
        StartPredictionRepository(conn).ensure_schema()
        rows = build_rows(conn, from_date, to_date)
        written = upsert_rows(conn, rows, dry_run=dry_run)
    return len(rows), written


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="from_date")
    parser.add_argument("--to", dest="to_date")
    parser.add_argument("--recent-days", type=int, default=400)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    to_date = args.to_date or date.today().isoformat()
    from_date = args.from_date or (
        date.fromisoformat(to_date) - timedelta(days=max(1, args.recent_days) - 1)
    ).isoformat()
    if date.fromisoformat(to_date) < date.fromisoformat(from_date):
        raise ValueError("--to must be on or after --from")
    rows, written = build(from_date, to_date, dry_run=args.dry_run)
    print(
        f"[derived-start-stats] from={from_date} to={to_date} "
        f"rows={rows} written={written} dry_run={args.dry_run}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
