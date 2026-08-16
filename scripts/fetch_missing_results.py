"""Scan for or sequentially fetch missing official K result archives.

Codex verification is limited to ``--scan``.  Download mode is intended for
the operator who controls the traffic volume and runtime.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from src.collectors import official_dl


LOGGER = logging.getLogger("fetch_missing_results")
DEFAULT_FROM = date(2016, 6, 1)


def iter_dates(start: date, end: date) -> Iterable[date]:
    """Yield the inclusive date range in ascending order."""
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def result_file_exists(results_dir: Path, target_date: date) -> bool:
    """Return whether either the K TXT or LZH for a date already exists."""
    stamp = target_date.strftime("%y%m%d")
    expected = {f"k{stamp}.lzh", f"k{stamp}.txt"}
    try:
        names = {path.name.lower() for path in results_dir.iterdir() if path.is_file()}
    except FileNotFoundError:
        return False
    return bool(expected & names)


def find_missing_dates(results_dir: Path, start: date, end: date) -> list[date]:
    """Find dates with neither a K TXT nor LZH, in ascending order."""
    if start > end:
        raise ValueError("from date must be on or before to date")

    try:
        names = {path.name.lower() for path in results_dir.iterdir() if path.is_file()}
    except FileNotFoundError:
        names = set()

    missing: list[date] = []
    for target_date in iter_dates(start, end):
        stamp = target_date.strftime("%y%m%d")
        if f"k{stamp}.lzh" not in names and f"k{stamp}.txt" not in names:
            missing.append(target_date)
    return missing


def monthly_counts(dates: Iterable[date]) -> dict[str, int]:
    """Count dates by YYYY-MM while preserving chronological order."""
    counts = Counter(target_date.strftime("%Y-%m") for target_date in dates)
    return {month: counts[month] for month in sorted(counts)}


def log_scan(missing: Sequence[date], start: date, end: date) -> None:
    LOGGER.info("scan range: %s .. %s", start, end)
    for target_date in missing:
        LOGGER.info("missing: %s", target_date)
    LOGGER.info("missing days: %d", len(missing))
    LOGGER.info("monthly summary:")
    for month, count in monthly_counts(missing).items():
        LOGGER.info("  %s: %d", month, count)


def fetch_dates(
    target_dates: Sequence[date],
    results_dir: Path,
    *,
    limit: int | None = None,
) -> dict[str, object]:
    """Fetch dates sequentially, calling ``download_lzh`` at most once each."""
    if limit is not None and limit <= 0:
        raise ValueError("limit must be greater than zero")

    selected = sorted(target_dates)
    if limit is not None:
        selected = selected[:limit]

    counts: Counter[str] = Counter()
    error_reasons: Counter[str] = Counter()
    results: list[dict[str, str]] = []
    started_at = time.monotonic()

    for index, target_date in enumerate(selected, start=1):
        if result_file_exists(results_dir, target_date):
            status = "skip_existing"
            reason = "local_file_exists"
        else:
            try:
                path = official_dl.download_lzh("K", target_date)
            except Exception as exc:  # noqa: BLE001 - continue after one bad date
                status = "error"
                reason = type(exc).__name__
                LOGGER.exception("error: %s", target_date)
            else:
                if path is None:
                    # The existing API intentionally maps 404 and other failed
                    # responses to None; do not bypass it to inspect/retry HTTP.
                    status = "not_found"
                    reason = "download_lzh_returned_none"
                else:
                    status = "ok"
                    reason = "downloaded"

        counts[status] += 1
        if status in {"not_found", "error"}:
            error_reasons[reason] += 1
        results.append(
            {"date": target_date.isoformat(), "status": status, "reason": reason}
        )
        LOGGER.info("%s: %s (%s)", target_date, status, reason)

        if index % 10 == 0:
            remaining = len(selected) - index
            elapsed = time.monotonic() - started_at
            average = elapsed / index
            eta_seconds = remaining * max(average, config.REQUEST_INTERVAL_SECONDS)
            LOGGER.info(
                "progress: %d/%d, remaining=%d, eta=%.0fs",
                index,
                len(selected),
                remaining,
                eta_seconds,
            )

    summary = {
        "requested": len(selected),
        "ok": counts["ok"],
        "skip_existing": counts["skip_existing"],
        "not_found": counts["not_found"],
        "error": counts["error"],
        "unavailable_reasons": dict(sorted(error_reasons.items())),
        "results": results,
    }
    LOGGER.info(
        "summary: requested=%d ok=%d skip_existing=%d not_found=%d error=%d",
        summary["requested"],
        summary["ok"],
        summary["skip_existing"],
        summary["not_found"],
        summary["error"],
    )
    for reason, count in summary["unavailable_reasons"].items():
        LOGGER.info("  unavailable[%s]=%d", reason, count)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scan",
        action="store_true",
        help="list missing dates without calling the downloader",
    )
    parser.add_argument("--from", dest="start", help="start date (YYYY-MM-DD)")
    parser.add_argument("--to", dest="end", help="end date (YYYY-MM-DD)")
    parser.add_argument("--limit", type=int, help="maximum missing dates to process")
    parser.add_argument("--log-file", help="also write UTF-8 logs to this path")
    return parser


def _parse_date(value: str, parser: argparse.ArgumentParser, option: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        parser.error(f"{option} must be YYYY-MM-DD")


def _configure_logging(log_file: str | None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers,
        force=True,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.scan:
        start = _parse_date(args.start, parser, "--from") if args.start else DEFAULT_FROM
        end = _parse_date(args.end, parser, "--to") if args.end else date.today()
    else:
        if not args.start or not args.end:
            parser.error("download mode requires --from and --to")
        start = _parse_date(args.start, parser, "--from")
        end = _parse_date(args.end, parser, "--to")

    if start > end:
        parser.error("--from must be on or before --to")
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be greater than zero")

    _configure_logging(args.log_file)
    results_dir = Path(config.OFFICIAL_RESULTS_DIR)
    missing = find_missing_dates(results_dir, start, end)

    if args.scan:
        log_scan(missing, start, end)
        return 0

    LOGGER.info("fetch range: %s .. %s; missing=%d", start, end, len(missing))
    fetch_dates(missing, results_dir, limit=args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
