"""Plan or execute a bounded original-exhibition recollection.

The default is read-only planning. Network/database writes require the explicit
``--execute`` flag after operator approval. Requests remain sequential and use
the collector's shared HTTP limiter and configured User-Agent.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, time
from pathlib import Path
from typing import Sequence
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from src.collectors import original_exhibition
from src.db.cron_runtime import find_missing_original_exhibition_races


JST = ZoneInfo("Asia/Tokyo")
DEFAULT_LIMIT = 12
MAX_LIMIT = 120
DEFAULT_PATTERN_LIMIT = 1
MAX_PATTERN_LIMIT = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="target date (YYYY-MM-DD)")
    parser.add_argument(
        "--stadiums",
        type=int,
        nargs="+",
        help="venue numbers; omitted means all verified capable venues",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"maximum races (default {DEFAULT_LIMIT}, max {MAX_LIMIT})",
    )
    parser.add_argument(
        "--pattern-limit",
        type=int,
        default=DEFAULT_PATTERN_LIMIT,
        help=f"maximum source URLs per race (default {DEFAULT_PATTERN_LIMIT}, max {MAX_PATTERN_LIMIT})",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="perform the approved sequential recollection; otherwise plan only",
    )
    return parser


def _capable_stadiums() -> set[int]:
    return set(original_exhibition.supported_stadiums())


def _validate_args(args, parser: argparse.ArgumentParser) -> tuple[date, set[int]]:
    try:
        target_date = date.fromisoformat(args.date)
    except ValueError:
        parser.error("--date must be YYYY-MM-DD")
    if not 1 <= args.limit <= MAX_LIMIT:
        parser.error(f"--limit must be between 1 and {MAX_LIMIT}")
    if not 1 <= args.pattern_limit <= MAX_PATTERN_LIMIT:
        parser.error(f"--pattern-limit must be between 1 and {MAX_PATTERN_LIMIT}")
    if config.REQUEST_INTERVAL_SECONDS < 2.0:
        parser.error("REQUEST_INTERVAL_SECONDS must be at least 2.0")

    capable = _capable_stadiums()
    requested = set(args.stadiums or capable)
    unsupported = requested - capable
    if unsupported:
        parser.error(
            "unsupported or unverified original-exhibition stadiums: "
            + ",".join(str(value) for value in sorted(unsupported))
        )
    return target_date, requested


def find_targets(target_date: date, stadiums: set[int], limit: int):
    """Find only true missing races, independent of their historical age."""
    noon = datetime.combine(target_date, time(12, 0), tzinfo=JST)
    return find_missing_original_exhibition_races(
        noon,
        target_date=target_date.isoformat(),
        past_min=24 * 60,
        future_min=24 * 60,
        limit=limit,
        stadiums=stadiums,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    target_date, stadiums = _validate_args(args, parser)
    due = find_targets(target_date, stadiums, args.limit)

    print(
        f"mode={'execute' if args.execute else 'plan'} date={target_date} "
        f"stadiums={','.join(map(str, sorted(stadiums)))} limit={args.limit} "
        f"due={len(due)} interval={config.REQUEST_INTERVAL_SECONDS:.1f}s",
        flush=True,
    )
    for race_id, stadium, race_no, _close in due:
        fields = ",".join(sorted(original_exhibition.expected_fields(stadium)))
        print(f"target race_id={race_id} stadium={stadium} race={race_no} fields={fields}")

    if not args.execute:
        print("plan only: no HTTP request or database write was performed")
        return 0
    if not due:
        return 0

    summary = original_exhibition.collect_for_races(
        target_date,
        [(race_id, stadium, race_no) for race_id, stadium, race_no, _close in due],
        force=False,
        save_html=True,
        stadiums=stadiums,
        pattern_limit=args.pattern_limit,
    )
    print(
        "completed once; unavailable pages will not be retried by this command: "
        f"targeted={summary['races_targeted']} fetched={summary['pages_fetched']} "
        f"found={summary['races_found']} rows={summary['rows_inserted']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
