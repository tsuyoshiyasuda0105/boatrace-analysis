"""List and resolve entries in the shared incident ledger."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.notifications.incident_ledger import list_incidents, resolve_incident  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Shared incident ledger CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="list recent incidents")
    list_parser.add_argument("--status", choices=("open", "investigating", "resolved", "wontfix"))
    list_parser.add_argument("--app", help="application name (defaults to BOATRACE_INCIDENT_APP_NAME)")
    list_parser.add_argument("--limit", type=int, default=20)

    resolve_parser = subparsers.add_parser("resolve", help="record response history")
    resolve_parser.add_argument("incident", help="incident ID or active dedup key")
    resolve_parser.add_argument("--by", required=True, dest="handled_by")
    resolve_parser.add_argument("--note", required=True, dest="response_note")
    resolve_parser.add_argument(
        "--status",
        choices=("investigating", "resolved", "wontfix"),
        default="resolved",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "list":
        rows = list_incidents(
            app_name=args.app,
            status=args.status,
            limit=args.limit,
        )
        for row in rows:
            print(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str))
        return 0

    updated = resolve_incident(
        args.incident,
        handled_by=args.handled_by,
        response_note=args.response_note,
        status=args.status,
    )
    if updated:
        print(f"updated: {args.incident} ({args.status})")
        return 0
    print(f"incident not updated: {args.incident}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
