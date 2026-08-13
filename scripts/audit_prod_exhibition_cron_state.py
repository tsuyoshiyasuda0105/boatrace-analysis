from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path("C:/boat_project/boatrace-analysis")
sys.path.insert(0, str(REPO))

from src.db.connection import connect as pg_connect


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    args = parser.parse_args()
    with pg_connect() as conn:
        print("# task_runs recent")
        try:
            cur = conn.execute(
                """
                SELECT task_name, status, started_at, finished_at, detail
                  FROM task_runs
                 WHERE task_name ILIKE %s
                    OR task_name ILIKE %s
                    OR task_name ILIKE %s
                    OR trigger ILIKE %s
                 ORDER BY started_at DESC
                 LIMIT 20
                """,
                ("%exhibition%", "%beforeinfo%", "%detail%", "%exhibition%"),
            )
            for row in cur.fetchall():
                print(row)
        except Exception as exc:
            print(f"task_runs query failed: {type(exc).__name__}: {exc}")
            try:
                conn.rollback()
            except Exception:
                pass

        print("\n# table columns")
        for table in ("task_runs", "race_previews", "race_original_exhibitions"):
            cur = conn.execute(
                """
                SELECT column_name
                  FROM information_schema.columns
                 WHERE table_schema='public' AND table_name=%s
                 ORDER BY ordinal_position
                """,
                (table,),
            )
            print(table, [r[0] for r in cur.fetchall()])

        print("\n# preview row counts by date")
        cur = conn.execute(
            """
            SELECT r.race_date, COUNT(*) AS rows,
                   COUNT(DISTINCT r.race_id) AS races,
                   MAX(p.live_updated_at) AS max_live_updated_at
              FROM races r
              JOIN race_previews p ON p.race_id = r.race_id
             WHERE r.race_date::date BETWEEN %s::date - INTERVAL '3 day' AND %s::date
             GROUP BY r.race_date
             ORDER BY r.race_date
            """,
            (args.date, args.date),
        )
        for row in cur.fetchall():
            print(row)

        print("\n# original exhibition row counts by date")
        cur = conn.execute(
            """
            SELECT r.race_date, COUNT(*) AS rows,
                   COUNT(DISTINCT r.race_id) AS races,
                   MAX(o.collected_at) AS max_collected_at
              FROM races r
              JOIN race_original_exhibitions o ON o.race_id = r.race_id
             WHERE r.race_date::date BETWEEN %s::date - INTERVAL '3 day' AND %s::date
             GROUP BY r.race_date
             ORDER BY r.race_date
            """,
            (args.date, args.date),
        )
        for row in cur.fetchall():
            print(row)

        print("\n# today's preview sample")
        try:
            cur = conn.execute(
                """
                SELECT r.race_id, r.stadium_number, r.race_number,
                       COUNT(p.race_id) AS preview_rows,
                       COUNT(o.race_id) AS original_join_rows,
                       MAX(p.live_updated_at) AS preview_updated_at,
                       MAX(o.collected_at) AS original_updated_at
                  FROM races r
                  LEFT JOIN race_previews p ON p.race_id = r.race_id
                  LEFT JOIN race_original_exhibitions o ON o.race_id = r.race_id
                 WHERE r.race_date = %s
                 GROUP BY r.race_id, r.stadium_number, r.race_number
                 ORDER BY r.stadium_number, r.race_number
                 LIMIT 40
                """,
                (args.date,),
            )
            for row in cur.fetchall():
                print(row)
        except Exception as exc:
            print(f"today sample failed: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
