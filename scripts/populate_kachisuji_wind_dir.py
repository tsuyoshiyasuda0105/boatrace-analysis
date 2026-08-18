"""One-shot migration: (re)populate asof_race_features.wind_dir.

The wind direction label is a pure function of ``wind_dir_raw`` (BOATRACE's
``is-wind`` 1-16, 17=calm) and ``wind_speed``, both already stored in the slim
DB.  It is therefore cheaper to recompute the label in-place than to re-export
and re-upload the 500MB+ database.  Run this once in the Render Shell after
deploying the course-relative wind classifier:

    python scripts/populate_kachisuji_wind_dir.py

The classification mirrors ``src.features.asof_builder.relative_wind_direction``
(course-relative frame: is-wind ~9 = tailwind, ~1 = headwind), verified on ~85k
races against 1-boat win rate.  Keep the two in sync if either changes.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

UPDATE_SQL = """
UPDATE asof_race_features SET wind_dir = CASE
  WHEN wind_speed IS NULL THEN NULL
  WHEN wind_speed = 0 OR wind_dir_raw = 17 THEN '無風'
  WHEN wind_dir_raw IN (7,8,9,10,11) THEN '追い風'
  WHEN wind_dir_raw IN (15,16,1,2,3) THEN '向かい風'
  WHEN wind_dir_raw IN (4,5,6) THEN '横風(右)'
  WHEN wind_dir_raw IN (12,13,14) THEN '横風(左)'
  ELSE NULL END
"""


def _resolve_db_path() -> Path:
    configured = os.environ.get("KACHISUJI_DB")
    if configured:
        return Path(configured).expanduser()
    slim = PROJECT_ROOT / "data" / "kachisuji_slim.db"
    if slim.is_file():
        return slim
    return PROJECT_ROOT / "data" / "kachisuji_search.db"


def main() -> int:
    db_path = _resolve_db_path()
    if not db_path.is_file():
        print(f"[ERROR] database not found: {db_path}", file=sys.stderr)
        return 1
    print(f"[info] target DB: {db_path}")
    conn = sqlite3.connect(str(db_path))
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(asof_race_features)")}
        for required in ("wind_dir", "wind_dir_raw", "wind_speed"):
            if required not in cols:
                print(f"[ERROR] column missing: {required}", file=sys.stderr)
                return 1
        cur = conn.execute(UPDATE_SQL)
        conn.commit()
        print(f"[info] updated {cur.rowcount} rows")
        dist = conn.execute(
            "SELECT COALESCE(wind_dir,'(none)'), COUNT(*) "
            "FROM asof_race_features GROUP BY 1 ORDER BY 2 DESC"
        ).fetchall()
        for label, count in dist:
            print(f"    {label}: {count}")
    finally:
        conn.close()
    print("[done] wind_dir populated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
