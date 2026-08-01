from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _date_range(from_date: date, to_date: date):
    current = from_date
    while current <= to_date:
        yield current
        current += timedelta(days=1)


def _run_py(args: list[str], *, env: dict[str, str]) -> None:
    cmd = [sys.executable, *args]
    subprocess.run(cmd, cwd=REPO, env=env, check=True)


def _today() -> date:
    return date.today()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Recompute date-scoped ROI signal snapshots and persist them to "
            "roi_race_history for past-only strategy validation."
        )
    )
    parser.add_argument("--from", dest="from_date", required=True)
    parser.add_argument("--to", dest="to_date", required=True)
    parser.add_argument(
        "--skip-recompute",
        action="store_true",
        help="Only import existing market-signals caches into roi_race_history.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    from_date = date.fromisoformat(args.from_date)
    to_date = date.fromisoformat(args.to_date)
    today = _today()
    if from_date > to_date:
        raise SystemExit("--from must be earlier than or equal to --to")
    if to_date >= today:
        raise SystemExit("ROI history backfill is past-only; --to must be before today")

    env = os.environ.copy()
    env.setdefault("BOATRACE_TASK_TRIGGER", "render-prewarm")

    for target in _date_range(from_date, to_date):
        target_s = target.isoformat()
        if not args.skip_recompute:
            _run_py(
                ["scripts/prewarm_strategy_pages.py", "--mode", "signals", "--date", target_s],
                env=env,
            )
        _run_py(
            ["scripts/backfill_roi_race_history.py", "--from", target_s, "--to", target_s],
            env=env,
        )
        print(f"[roi-history-range] completed {target_s}", flush=True)

    print(f"[roi-history-range] done range={from_date.isoformat()}..{to_date.isoformat()}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
