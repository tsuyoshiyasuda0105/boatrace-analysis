from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


DB = Path("C:/boat_project/boatrace-analysis/data/boatrace.db")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--month", required=True, help="YYYY-MM")
    args = parser.parse_args()
    prefix = f"market_signals:%:{args.month}-%"
    last_good = f"market_signals:last-good:{args.month}-%"

    with sqlite3.connect(DB) as conn:
        has = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='page_html_cache'"
        ).fetchone()[0]
        print(f"page_html_cache table: {has}")
        if not has:
            return
        count = conn.execute(
            """
            SELECT COUNT(*)
              FROM page_html_cache
             WHERE cache_key LIKE ?
                OR cache_key LIKE ?
            """,
            (prefix, last_good),
        ).fetchone()[0]
        print(f"market signal cache rows for {args.month}: {count}")
        rows = conn.execute(
            """
            SELECT cache_key, LENGTH(html), updated_at
              FROM page_html_cache
             WHERE cache_key LIKE ?
                OR cache_key LIKE ?
             ORDER BY cache_key
             LIMIT 40
            """,
            (prefix, last_good),
        ).fetchall()
        for row in rows:
            print(row)


if __name__ == "__main__":
    main()
