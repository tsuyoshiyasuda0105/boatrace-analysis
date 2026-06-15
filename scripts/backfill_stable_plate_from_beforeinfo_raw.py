"""Backfill race_previews.stable_plate from saved beforeinfo HTML.

This script intentionally writes to local SQLite only. It does not use
DATABASE_URL, so it cannot accidentally update production Postgres.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["DATABASE_URL"] = ""

import config  # noqa: E402
from src.parsers.beforeinfo import parse_beforeinfo  # noqa: E402


def ensure_column(conn: sqlite3.Connection) -> None:
    cols = [row[1] for row in conn.execute("PRAGMA table_info(race_previews)")]
    if "stable_plate" not in cols:
        conn.execute("ALTER TABLE race_previews ADD COLUMN stable_plate INTEGER")
        conn.commit()


def race_id_from_file(path: Path) -> str | None:
    try:
        date_part = path.parent.name.replace("-", "")
        stadium_part, race_part = path.stem.split("_", 1)
        stadium = int(stadium_part)
        race_no = int(race_part)
    except (ValueError, IndexError):
        return None
    if len(date_part) != 8:
        return None
    return f"{date_part}-{stadium:02d}-{race_no:02d}"


def backfill(raw_dir: Path, db_path: Path) -> dict[str, int]:
    summary = {
        "html_files": 0,
        "parsed": 0,
        "stable_races": 0,
        "updated_races": 0,
        "updated_rows": 0,
        "no_preview_rows": 0,
        "parse_errors": 0,
    }
    conn = sqlite3.connect(db_path)
    try:
        ensure_column(conn)
        for html_path in sorted(raw_dir.glob("*/*.html")):
            rid = race_id_from_file(html_path)
            if not rid:
                continue
            summary["html_files"] += 1
            try:
                html = html_path.read_text(encoding="utf-8", errors="replace")
                page = parse_beforeinfo(html)
                stable_plate = page.get("stable_plate")
            except Exception:
                summary["parse_errors"] += 1
                continue
            if stable_plate is None:
                continue
            summary["parsed"] += 1
            if stable_plate:
                summary["stable_races"] += 1
            cur = conn.execute(
                "UPDATE race_previews SET stable_plate = ? WHERE race_id = ?",
                (stable_plate, rid),
            )
            rowcount = cur.rowcount or 0
            if rowcount:
                summary["updated_races"] += 1
                summary["updated_rows"] += rowcount
            else:
                summary["no_preview_rows"] += 1
        conn.commit()
        return summary
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-dir", default=str(config.BEFOREINFO_DIR))
    parser.add_argument("--db", default=str(config.DB_PATH))
    args = parser.parse_args()

    summary = backfill(Path(args.raw_dir), Path(args.db))
    for key, value in summary.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
