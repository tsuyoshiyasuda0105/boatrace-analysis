from __future__ import annotations

import os
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
os.environ.setdefault("BOATRACE_TASK_TRIGGER", "db-maintenance")

from src.db.connection import connect as db_connect  # noqa: E402


DDL: tuple[str, ...] = (
    """
    CREATE INDEX IF NOT EXISTS idx_races_date_stadium_rno
      ON races(race_date, stadium_number, race_number)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_entries_race_boat
      ON race_entries(race_id, boat_number)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_results_race_boat_finish
      ON race_results(race_id, boat_number, finishing_position)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_payouts_race_type_combination
      ON race_payouts(race_id, bet_type, combination)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_previews_race_boat
      ON race_previews(race_id, boat_number)
    """,
    """
    CREATE TABLE IF NOT EXISTS l4_daily_stats_cache (
      race_date  TEXT PRIMARY KEY,
      stats_json TEXT NOT NULL,
      cached_at  TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_l4_daily_stats_cache_date
      ON l4_daily_stats_cache(race_date)
    """,
)


def ensure_performance_indexes() -> int:
    with db_connect() as conn:
        for sql in DDL:
            conn.execute(sql)
        conn.commit()
    print(f"[indexes] ensured {len(DDL)} performance objects", flush=True)
    return len(DDL)


def main() -> int:
    ensure_performance_indexes()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
