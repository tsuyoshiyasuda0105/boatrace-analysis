"""Daily incremental refresh of the kachisuji backtest dataset.

For a range of *completed* race days this:

1. builds leakage-safe as-of rows into ``data/kachisuji_search.db``
   (``asof_builder.build_features`` -- idempotent, skips race_ids already
   present).  Wind direction is classified here by the course-relative
   ``relative_wind_direction`` and needs no extra step.
2. appends the new rows into the served slim DB (``data/kachisuji_slim.db``)
   with ``INSERT OR IGNORE`` so a full 500MB+ re-export is never needed.
3. optionally writes a tiny delta DB (only the new as-of rows + any new
   racers) for shipping to production.

Because as-of rows also carry the ACTUAL race result and the ACTUAL race-day
wind, only fully-finished days should be built -- the default target is
yesterday (JST-naive local clock).  The 365-day racer aggregates still use
only ``[asof_date-364, asof_date)``; no future information leaks in.

    python scripts/refresh_kachisuji_daily.py                 # yesterday
    python scripts/refresh_kachisuji_daily.py --date 2026-08-18
    python scripts/refresh_kachisuji_daily.py --date-from 2026-08-10 --date-to 2026-08-18
    python scripts/refresh_kachisuji_daily.py --date 2026-08-18 --emit-delta data/kachisuji_delta_20260818.db
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from src.db.connection import connect  # noqa: E402
from src.features.asof_builder import build_features  # noqa: E402

SEARCH_DB = ROOT / "data" / "kachisuji_search.db"
SLIM_DB = ROOT / "data" / "kachisuji_slim.db"
SLIM_TABLES = ("asof_race_features", "racers")


def _readonly_uri(path: Path) -> str:
    return path.resolve().as_uri() + "?mode=ro"


def _append_to_slim(search_db: Path, slim_db: Path, date_from: str, date_to: str) -> dict[str, int]:
    """Copy new rows for the date range into the served slim DB, idempotently."""
    # ``uri=True`` on the main connection is required for SQLite to honor the
    # ``mode=ro`` URI on the subsequently attached source (notably on Windows).
    conn = sqlite3.connect(slim_db, uri=True)
    try:
        conn.execute("ATTACH DATABASE ? AS src", (_readonly_uri(search_db),))
        before_asof = conn.execute("SELECT COUNT(*) FROM asof_race_features").fetchone()[0]
        before_racers = conn.execute("SELECT COUNT(*) FROM racers").fetchone()[0]
        conn.execute(
            "INSERT OR IGNORE INTO asof_race_features "
            "SELECT * FROM src.asof_race_features WHERE race_date BETWEEN ? AND ?",
            (date_from, date_to),
        )
        # New racers can debut on any day; a full-table OR IGNORE is cheap.
        conn.execute("INSERT OR IGNORE INTO racers SELECT * FROM src.racers")
        conn.commit()
        after_asof = conn.execute("SELECT COUNT(*) FROM asof_race_features").fetchone()[0]
        after_racers = conn.execute("SELECT COUNT(*) FROM racers").fetchone()[0]
        conn.execute("DETACH DATABASE src")
    finally:
        conn.close()
    return {
        "asof_added": after_asof - before_asof,
        "racers_added": after_racers - before_racers,
    }


def _emit_delta(search_db: Path, delta_db: Path, date_from: str, date_to: str) -> int:
    if delta_db.exists():
        raise FileExistsError(f"delta already exists: {delta_db}")
    conn = sqlite3.connect(str(delta_db), uri=True)
    try:
        conn.execute("ATTACH DATABASE ? AS src", (_readonly_uri(search_db),))
        for table in SLIM_TABLES:
            create_sql = conn.execute(
                "SELECT sql FROM src.sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()[0]
            conn.execute(create_sql)
        conn.execute(
            "INSERT INTO asof_race_features "
            "SELECT * FROM src.asof_race_features WHERE race_date BETWEEN ? AND ?",
            (date_from, date_to),
        )
        conn.execute("INSERT INTO racers SELECT * FROM src.racers")
        count = conn.execute("SELECT COUNT(*) FROM asof_race_features").fetchone()[0]
        conn.commit()
        conn.execute("DETACH DATABASE src")
    finally:
        conn.close()
    return int(count)


def _wind_distribution(slim_db: Path, date_from: str, date_to: str) -> list[tuple[str, int]]:
    conn = sqlite3.connect(_readonly_uri(slim_db), uri=True)
    try:
        return conn.execute(
            "SELECT COALESCE(wind_dir,'(none)'), COUNT(*) FROM asof_race_features "
            "WHERE race_date BETWEEN ? AND ? GROUP BY 1 ORDER BY 2 DESC",
            (date_from, date_to),
        ).fetchall()
    finally:
        conn.close()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    parser.add_argument("--date", help="Single completed race day (YYYY-MM-DD).")
    parser.add_argument("--date-from", help="Range start (with --date-to).")
    parser.add_argument("--date-to", help="Range end (with --date-from).")
    parser.add_argument("--emit-delta", type=Path, help="Also write a small delta DB for production.")
    parser.add_argument("--skip-slim", action="store_true", help="Build search DB only; do not touch the slim DB.")
    parser.set_defaults(_yesterday=yesterday)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.date_from or args.date_to:
        if not (args.date_from and args.date_to):
            print("error: --date-from requires --date-to", file=sys.stderr)
            return 2
        date_from, date_to = args.date_from, args.date_to
    else:
        target = args.date or args._yesterday
        date_from = date_to = target

    print(f"[info] target range: {date_from} .. {date_to}")

    source = connect(str(config.DB_PATH))
    try:
        source.execute("PRAGMA query_only=ON")
        result = build_features(source, SEARCH_DB, date_from, date_to)
    finally:
        source.close()
    print("[build] " + " ".join(f"{k}={v}" for k, v in result.items()))

    if not args.skip_slim:
        added = _append_to_slim(SEARCH_DB, SLIM_DB, date_from, date_to)
        print(f"[slim] asof_added={added['asof_added']} racers_added={added['racers_added']}")
        print("[wind] distribution for range in slim DB:")
        for label, count in _wind_distribution(SLIM_DB, date_from, date_to):
            print(f"    {label}: {count}")

    if args.emit_delta:
        count = _emit_delta(SEARCH_DB, args.emit_delta, date_from, date_to)
        print(f"[delta] wrote {args.emit_delta} ({count} asof rows)")

    print("[done]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
