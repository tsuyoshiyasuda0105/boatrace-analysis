"""Match saved Kachisuji strategies for one race date."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.search.strategies import (  # noqa: E402
    DEFAULT_SEARCH_DB,
    DEFAULT_STRATEGY_DB,
    match_all_strategies,
    match_races,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="保存済み手法を指定日のレースと照合します")
    parser.add_argument("--date", required=True, help="対象日 (YYYY-MM-DD)")
    parser.add_argument("--id", type=int, help="照合する手法ID。省略時は全有効手法")
    parser.add_argument(
        "--search-db",
        default=os.environ.get("KACHISUJI_DB") or str(DEFAULT_SEARCH_DB),
        help="検索DBパス",
    )
    parser.add_argument(
        "--strategies-db",
        default=os.environ.get("KACHISUJI_STRATEGY_DB") or str(DEFAULT_STRATEGY_DB),
        help="手法DBパス",
    )
    args = parser.parse_args()

    if args.id is None:
        result = match_all_strategies(args.date, args.search_db, args.strategies_db)
    else:
        result = match_races(args.id, args.date, args.search_db, args.strategies_db)
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
