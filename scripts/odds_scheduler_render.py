"""Render-safe odds scheduler entrypoint.

This keeps the existing odds scheduler logic, but narrows the active snapshot
labels for production so the daytime cron does less work:
  - normal races: T-5min
  - major races: T-1d / T-5min
"""
from __future__ import annotations

import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts import odds_scheduler as base  # noqa: E402
from src.deploy_info import log_deploy_revision


RENDER_SNAPSHOT_RULES = [
    ("T-5min", 5, 0.5),
]


def main() -> None:
    log_deploy_revision("boatrace-odds-cron")
    base.SNAPSHOT_RULES = list(RENDER_SNAPSHOT_RULES)
    base.BIG_SNAPSHOT_RULES = [("T-1d", 24 * 60, 5), *base.SNAPSHOT_RULES]
    base.main()


if __name__ == "__main__":
    main()
