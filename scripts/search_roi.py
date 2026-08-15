"""Command-line interface for the read-only condition ROI search."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.search.roi_search import search_roi  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--conditions", type=Path, help="UTF-8 condition JSON file")
    source.add_argument("--stdin", action="store_true", help="read condition JSON from stdin")
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "kachisuji_search.db")
    parser.add_argument("--fast", action="store_true", help="use a normal-approximation CI")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--pretty", action="store_true")
    return parser


def _load_conditions(args: argparse.Namespace) -> Any:
    if args.stdin:
        return json.load(sys.stdin)
    with args.conditions.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    args = _parser().parse_args(argv)
    try:
        result = search_roi(args.db, _load_conditions(args), fast=args.fast, seed=args.seed)
    except (OSError, ValueError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2 if args.pretty else None)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
