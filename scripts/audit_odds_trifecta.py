"""
odds_trifecta / odds_fetch_status の非破壊監査。

用途:
  - P0: 取得済み / 欠損 / 再試行待ちの把握
  - P1: 重複 / 不完全データの一覧化

usage:
  python scripts/audit_odds_trifecta.py
  python scripts/audit_odds_trifecta.py --limit 100
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.db.connection import connect as db_connect


def _read_sql() -> list[str]:
    sql_path = Path(__file__).resolve().parent / "sql" / "odds_trifecta_integrity_audit.sql"
    lines: list[str] = []
    for line in sql_path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("--"):
            continue
        lines.append(line)
    return [stmt.strip() for stmt in "\n".join(lines).split(";") if stmt.strip()]


def _print_rows(title: str, rows: list[tuple], columns: list[str]) -> None:
    print(f"\n=== {title} ({len(rows)}) ===")
    if not rows:
        print("0 rows")
        return
    print(" | ".join(columns))
    for row in rows:
        print(" | ".join("" if v is None else str(v) for v in row))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()

    statements = _read_sql()
    with db_connect() as conn:
        try:
            status_summary = conn.execute(
                """
                SELECT state, detail_code, COUNT(*) AS rows
                  FROM odds_fetch_status
                 GROUP BY state, detail_code
                 ORDER BY state, detail_code
                """
            ).fetchall()
            _print_rows("odds_fetch_status summary", list(status_summary), ["state", "detail_code", "rows"])

            pending = conn.execute(
                """
                SELECT race_id, snapshot_label, state, detail_code, combination_count, checked_at
                  FROM odds_fetch_status
                 WHERE state IN ('missing', 'retry_waiting')
                 ORDER BY checked_at DESC
                 LIMIT ?
                """,
                (args.limit,),
            ).fetchall()
            _print_rows(
                "recent missing / retry_waiting",
                list(pending),
                ["race_id", "snapshot_label", "state", "detail_code", "combination_count", "checked_at"],
            )
        except Exception:
            print("\n=== odds_fetch_status summary ===")
            print("odds_fetch_status table not available")

        labels = [
            "duplicate combinations",
            "incomplete snapshots",
            "final odds missing races",
        ]
        columns = [
            ["race_id", "snapshot_label", "combination", "duplicate_rows", "first_recorded_at", "last_recorded_at"],
            ["race_id", "snapshot_label", "combination_count", "missing_combinations", "first_recorded_at", "last_recorded_at"],
            ["race_id", "race_date", "stadium_number", "race_number", "final_combination_count", "final_missing_combinations"],
        ]
        for title, stmt, cols in zip(labels, statements, columns):
            rows = conn.execute(f"{stmt} LIMIT ?", (args.limit,)).fetchall()
            _print_rows(title, list(rows), cols)


if __name__ == "__main__":
    main()
