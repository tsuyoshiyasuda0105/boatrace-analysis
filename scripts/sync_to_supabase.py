"""
Sync local SQLite tables into Supabase Postgres.

Examples:
  $env:DATABASE_URL = "postgresql://postgres.xxxxx:..."
  python scripts/sync_to_supabase.py --start 2026-01-01 --end 2026-05-12
  python scripts/sync_to_supabase.py --tables races,race_entries,race_results
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from src.db.connection import connect as db_connect


ACCIDENT_FULL_TABLES = {
    "racer_accident_point_rules",
}

ACCIDENT_DATE_TABLES = {
    "racer_accident_events": (
        "race_date >= ? AND race_date <= ?",
        lambda args: (args.start, args.end),
    ),
    "racer_accident_kraw_unmatched": (
        "race_date >= ? AND race_date <= ?",
        lambda args: (args.start, args.end),
    ),
    "racer_accident_external_snapshots": (
        "snapshot_date >= ? AND snapshot_date <= ?",
        lambda args: (args.start, args.end),
    ),
    "racer_accident_rank_snapshots": (
        "snapshot_date >= ? AND snapshot_date <= ?",
        lambda args: (args.start, args.end),
    ),
    "racer_accident_period_stats": (
        "period_start <= ? AND period_end >= ?",
        lambda args: (args.end, args.start),
    ),
    "racer_accident_period_adjustments": (
        "period_start <= ? AND period_end >= ?",
        lambda args: (args.end, args.start),
    ),
}


DEFAULT_TABLES = ",".join([
    "races",
    "race_entries",
    "race_previews",
    "race_results",
    "race_payouts",
    "racer_accident_point_rules",
    "racer_accident_events",
    "racer_accident_period_stats",
    "racer_accident_rank_snapshots",
])


def sync_table(src: sqlite3.Connection, dst, table: str, where: str = "1=1",
               params: tuple = (), batch_size: int = 500, verbose: bool = False) -> int:
    """Copy rows from one SQLite table into the destination database."""
    cur = src.execute(f"PRAGMA table_info({table})")
    cols = [row[1] for row in cur.fetchall()]
    if not cols:
        print(f"  [{table}] table not found")
        return 0

    col_list = ", ".join(cols)
    placeholders = ", ".join(["?"] * len(cols))
    cur = src.execute(f"SELECT {col_list} FROM {table} WHERE {where}", params)

    total = 0
    batch = []
    for row in cur:
        batch.append(tuple(row))
        if len(batch) >= batch_size:
            sql = f"INSERT OR REPLACE INTO {table} ({col_list}) VALUES ({placeholders})"
            dst.executemany(sql, batch)
            total += len(batch)
            batch.clear()
            if verbose:
                print(f"  [{table}] {total:,} rows synced")

    if batch:
        sql = f"INSERT OR REPLACE INTO {table} ({col_list}) VALUES ({placeholders})"
        dst.executemany(sql, batch)
        total += len(batch)

    if verbose:
        print(f"  [{table}] done {total:,} rows")
    return total


def sync_specs_for_table(table: str, args, race_ids: list[str]) -> list[tuple[str, tuple]]:
    if table == "races":
        return [(
            "race_date >= ? AND race_date <= ?",
            (args.start, args.end),
        )]

    if table in ACCIDENT_FULL_TABLES:
        return [("1=1", ())]

    if table in ACCIDENT_DATE_TABLES:
        where, param_builder = ACCIDENT_DATE_TABLES[table]
        return [(where, tuple(param_builder(args)))]

    specs = []
    chunk_size = 500
    for i in range(0, len(race_ids), chunk_size):
        chunk = race_ids[i:i + chunk_size]
        if not chunk:
            continue
        placeholders = ",".join(["?"] * len(chunk))
        specs.append((f"race_id IN ({placeholders})", tuple(chunk)))
    return specs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=str, default="2026-01-01")
    parser.add_argument("--end", type=str, default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument(
        "--tables",
        type=str,
        default=DEFAULT_TABLES,
        help="comma-separated table names",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    db_url = os.getenv("DATABASE_URL", "").strip()
    if not db_url or not db_url.startswith(("postgres://", "postgresql://")):
        print("ERROR: DATABASE_URL is not set to a Postgres URL")
        print("  Example: $env:DATABASE_URL = 'postgresql://postgres.xxx:pass@host:5432/postgres'")
        sys.exit(1)

    print("=== SQLite -> Supabase sync ===")
    print(f"  range: {args.start} .. {args.end}")
    print(f"  tables: {args.tables}")
    print()

    src = sqlite3.connect(config.DB_PATH)
    cur = src.execute(
        "SELECT race_id FROM races WHERE race_date >= ? AND race_date <= ?",
        (args.start, args.end),
    )
    race_ids = [row[0] for row in cur.fetchall()]
    print(f"  race count: {len(race_ids):,}")

    dst = db_connect()
    tables = [table.strip() for table in args.tables.split(",") if table.strip()]

    if "stadiums" not in tables:
        count = sync_table(src, dst, "stadiums", verbose=args.verbose)
        print(f"stadiums: {count:,} rows")

    for table in tables:
        print(f"\n[{table}]")
        specs = sync_specs_for_table(table, args, race_ids)
        if not specs:
            print("  no target rows")
            continue

        total = 0
        for where, params in specs:
            total += sync_table(src, dst, table, where, params, verbose=args.verbose)
        print(f"  => total {total:,} rows")

    src.close()
    dst.close()
    print("\n=== sync complete ===")


if __name__ == "__main__":
    main()
