"""Restore Step 13 accident history from local official K result files."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.features.accident_history import restore_accident_history, yearly_stats


DEFAULT_DB = ROOT / "data" / "kachisuji_search.db"
DEFAULT_RAW = ROOT / "data" / "raw" / "results"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="date_from")
    parser.add_argument("--to", dest="date_to")
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--stats", action="store_true")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.stats:
        if args.date_from or args.date_to or args.rebuild:
            raise SystemExit("--stats cannot be combined with --from/--to/--rebuild")
        rows = yearly_stats(args.db)
        if not rows:
            print("no restored accident history")
            return 0
        print("year starts events responsible_events")
        for row in rows:
            print(
                f"{row['year']} {row['starts']} {row['events']} "
                f"{row['responsible_events']}"
            )
        return 0
    if not args.date_from or not args.date_to:
        raise SystemExit("--from and --to are required unless --stats is used")
    summary = restore_accident_history(
        args.db,
        args.raw_dir,
        args.date_from,
        args.date_to,
        rebuild=args.rebuild,
    )
    print(
        "summary: "
        f"files={summary.files_parsed}/{summary.files_selected} "
        f"skipped_files={summary.files_skipped} rows={summary.rows_seen} "
        f"fallback_rows={summary.fallback_rows} skipped_rows={summary.rows_skipped} "
        f"incomplete_races={summary.incomplete_races} "
        f"starts={summary.starts_found} inserted_starts={summary.starts_inserted} "
        f"events={summary.events_found} inserted_events={summary.events_inserted} "
        f"responsible={summary.responsible_events}"
    )
    print("codes: " + ", ".join(f"{k}={v}" for k, v in sorted(summary.code_counts.items())))
    if summary.unknown_code_counts:
        print(
            "unknown_codes: "
            + ", ".join(
                f"{k}={v}" for k, v in sorted(summary.unknown_code_counts.items())
            )
        )
    return 0 if summary.files_skipped == 0 and summary.rows_skipped == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
