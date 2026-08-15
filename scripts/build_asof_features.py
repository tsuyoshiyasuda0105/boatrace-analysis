"""CLI for the Step 1 as-of feature snapshot builder."""

from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
from src.db.connection import connect
from src.features.asof_builder import build_features, coverage_rows, verify_features
from src.features.odds_sync import sync_odds


DEFAULT_OUTPUT = ROOT / "data" / "kachisuji_search.db"


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Build leakage-safe as-of race features")
    mode = result.add_mutually_exclusive_group(required=True)
    mode.add_argument("--backfill", action="store_true")
    mode.add_argument("--daily", action="store_true")
    mode.add_argument("--verify", action="store_true")
    mode.add_argument("--coverage", action="store_true")
    mode.add_argument("--sync-odds", action="store_true")
    result.add_argument("--date-from")
    result.add_argument("--date-to")
    result.add_argument("--rebuild", action="store_true")
    result.add_argument("--sample", type=int, default=20)
    result.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return result


def _source_connection():
    # Passing the explicit local path prevents DATABASE_URL from redirecting a
    # Step 1 run to Postgres.  The common helper is mandatory for boatrace.db.
    conn = connect(str(config.DB_PATH))
    conn.execute("PRAGMA query_only=ON")
    return conn


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.coverage:
        if not args.output.exists():
            print(f"output database not found: {args.output}", file=sys.stderr)
            return 1
        print("column\toldest_date\tpopulated/total\tcoverage_pct")
        for row in coverage_rows(args.output):
            print(
                f"{row['column']}\t{row['oldest_date'] or '-'}\t"
                f"{row['populated']}/{row['total']}\t{row['coverage_pct']:.2f}%"
            )
        return 0

    source = _source_connection()
    try:
        if args.sync_odds:
            if not args.date_from or not args.date_to:
                print("--sync-odds requires --date-from and --date-to", file=sys.stderr)
                return 2
            result = sync_odds(
                source,
                args.output,
                args.date_from,
                args.date_to,
                rebuild=args.rebuild,
            )
            print("sync-odds " + " ".join(f"{key}={value}" for key, value in result.items()))
            return 0

        if args.verify:
            result = verify_features(
                source,
                args.output,
                args.sample,
                date_from=args.date_from,
                date_to=args.date_to,
            )
            print(
                f"verify rows={result['rows']} sampled={result['sampled']} "
                f"chronology_errors={result['chronology_errors']} "
                f"mismatches={len(result['mismatches'])}"
            )
            if result["mismatches"]:
                for item in result["mismatches"][:20]:
                    print(f"mismatch: {item}", file=sys.stderr)
            return 0 if result["ok"] else 1

        if args.daily:
            target = (date.today() + timedelta(days=1)).isoformat()
            date_from = date_to = target
        else:
            if not args.date_from or not args.date_to:
                print("--backfill requires --date-from and --date-to", file=sys.stderr)
                return 2
            date_from, date_to = args.date_from, args.date_to
        result = build_features(
            source,
            args.output,
            date_from,
            date_to,
            rebuild=args.rebuild,
        )
        print(
            "build "
            + " ".join(f"{key}={value}" for key, value in result.items())
        )
        return 0 if result["warnings"] == 0 else 1
    finally:
        source.close()


if __name__ == "__main__":
    raise SystemExit(main())
