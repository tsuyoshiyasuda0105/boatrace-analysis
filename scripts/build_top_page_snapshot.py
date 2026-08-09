from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.web import app as web_app


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the cached TOP page payload used by /races."
    )
    parser.add_argument("--date", required=True)
    parser.add_argument(
        "--lightweight",
        action="store_true",
        help="Skip expensive badge hydration; use already materialized badge caches only.",
    )
    parser.add_argument(
        "--environment-only",
        action="store_true",
        help="Refresh only race groups and venue environment while keeping prior market badges.",
    )
    args = parser.parse_args()

    payload = web_app._build_top_page_snapshot_payload(
        args.date,
        allow_expensive_badges=not args.lightweight,
        include_market_signals=not args.environment_only,
    )
    web_app._write_top_page_snapshot(args.date, payload)
    groups = payload.get("stadium_groups") or []
    races = sum(len((group.get("races") or [])) for group in groups if isinstance(group, dict))
    badges = (
        (payload.get("initial_market_signals") or {}).get("race_badges") or {}
        if isinstance(payload.get("initial_market_signals"), dict)
        else {}
    )
    print(
        "[top-snapshot] "
        f"date={args.date} stadiums={len(groups)} races={races} "
        f"badged_races={len(badges)} lightweight={bool(args.lightweight)}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
