"""Export the two read-only tables required by kachisuji search."""

from __future__ import annotations

import argparse
from pathlib import Path
import sqlite3
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = PROJECT_ROOT / "data" / "kachisuji_search.db"
TABLES = ("asof_race_features", "racers")


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _readonly_uri(path: Path) -> str:
    return path.resolve().as_uri() + "?mode=ro"


def _table_objects(
    connection: sqlite3.Connection, table: str
) -> tuple[str, list[str]]:
    row = connection.execute(
        "SELECT sql FROM source_db.sqlite_master "
        "WHERE type='table' AND name=? AND sql IS NOT NULL",
        (table,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"source table is missing: {table}")
    indexes = [
        str(item[0])
        for item in connection.execute(
            "SELECT sql FROM source_db.sqlite_master "
            "WHERE type='index' AND tbl_name=? AND sql IS NOT NULL "
            "ORDER BY name",
            (table,),
        )
    ]
    return str(row[0]), indexes


def verify_export(source: Path, output: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    with sqlite3.connect(_readonly_uri(source), uri=True) as source_connection:
        source_connection.execute("PRAGMA query_only = ON")
        with sqlite3.connect(_readonly_uri(output), uri=True) as output_connection:
            output_connection.execute("PRAGMA query_only = ON")
            for table in TABLES:
                quoted = _quote_identifier(table)
                source_count = int(
                    source_connection.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0]
                )
                output_count = int(
                    output_connection.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0]
                )
                if source_count != output_count:
                    raise RuntimeError(
                        f"row count mismatch for {table}: "
                        f"source={source_count} output={output_count}"
                    )
                counts[table] = output_count
            quick_check = str(output_connection.execute("PRAGMA quick_check").fetchone()[0])
            if quick_check != "ok":
                raise RuntimeError(f"output quick_check failed: {quick_check}")
    return counts


def export_slim_db(source: Path, output: Path, *, verify: bool = False) -> dict[str, int]:
    source = source.resolve()
    output = output.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"source database not found: {source}")
    if source == output:
        raise ValueError("output database must differ from source database")
    if output.exists():
        raise FileExistsError(f"output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)

    # ``uri=True`` is required for SQLite to honor ``mode=ro`` on the
    # subsequently attached source URI (notably on Windows).
    connection = sqlite3.connect(output, uri=True)
    try:
        connection.execute("ATTACH DATABASE ? AS source_db", (_readonly_uri(source),))
        all_indexes: list[str] = []
        for table in TABLES:
            table_sql, indexes = _table_objects(connection, table)
            connection.execute(table_sql)
            quoted = _quote_identifier(table)
            connection.execute(
                f"INSERT INTO {quoted} SELECT * FROM source_db.{quoted}"
            )
            all_indexes.extend(indexes)
        for index_sql in all_indexes:
            connection.execute(index_sql)
        connection.commit()
        connection.execute("DETACH DATABASE source_db")
        connection.execute("VACUUM")
    except Exception:
        connection.close()
        if output.exists():
            output.unlink()
        raise
    else:
        connection.close()

    counts = verify_export(source, output) if verify else {}
    size_mib = output.stat().st_size / (1024 * 1024)
    print(f"created {output} ({size_mib:.1f} MiB)")
    if verify:
        for table in TABLES:
            print(f"verified {table}: {counts[table]:,} rows")
        print("verified PRAGMA quick_check: ok")
    return counts


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--verify", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        export_slim_db(args.source, args.out, verify=args.verify)
    except (FileNotFoundError, FileExistsError, RuntimeError, sqlite3.Error, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
