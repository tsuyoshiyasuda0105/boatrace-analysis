"""Copy the local racer master into the standalone kachisuji search database."""

from __future__ import annotations

import argparse
from pathlib import Path
import sqlite3
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = PROJECT_ROOT / "data" / "boatrace.db"
DEFAULT_DESTINATION = PROJECT_ROOT / "data" / "kachisuji_search.db"
RACERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS racers (
  racer_number INTEGER PRIMARY KEY,
  name TEXT,
  name_kana TEXT
);
"""


def _readonly_connection(path: str | Path) -> sqlite3.Connection:
    resolved = Path(path).resolve()
    connection = sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True)
    connection.execute("PRAGMA query_only = ON")
    return connection


def sync_racers(source_path: str | Path, destination_path: str | Path) -> dict[str, Any]:
    """Replace only ``destination.racers`` from a read-only source snapshot."""

    source = Path(source_path).resolve()
    destination = Path(destination_path).resolve()
    if source == destination:
        raise ValueError("source and destination databases must differ")

    with _readonly_connection(source) as source_connection:
        rows = source_connection.execute(
            "SELECT racer_number, name, name_kana FROM racers ORDER BY racer_number"
        ).fetchall()

    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(destination) as destination_connection:
        destination_connection.executescript(RACERS_SCHEMA)
        destination_connection.execute("DELETE FROM racers")
        destination_connection.executemany(
            "INSERT INTO racers (racer_number, name, name_kana) VALUES (?, ?, ?)", rows
        )
        copied = destination_connection.execute("SELECT COUNT(*) FROM racers").fetchone()[0]

    if copied != len(rows):
        raise RuntimeError(f"racer copy count mismatch: source={len(rows)} destination={copied}")
    return {"source": str(source), "destination": str(destination), "copied": copied}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    result.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    return result


def main() -> int:
    args = parser().parse_args()
    result = sync_racers(args.source, args.destination)
    print(f"copied {result['copied']} racers to {result['destination']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
