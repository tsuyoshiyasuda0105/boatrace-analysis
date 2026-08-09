"""Render-safe odds scheduler entrypoint.

This keeps the existing odds scheduler logic, but narrows the active snapshot
labels for production so the minute cron does less work:
  - normal races: T-5min / T-1min
  - major races: T-1d / T-5min / T-1min
"""
from __future__ import annotations

from scripts import odds_scheduler as base


RENDER_SNAPSHOT_RULES = [
    ("T-5min", 5, 0.5),
    ("T-1min", 1, 0.5),
]


def main() -> None:
    base.SNAPSHOT_RULES = list(RENDER_SNAPSHOT_RULES)
    base.BIG_SNAPSHOT_RULES = [("T-1d", 24 * 60, 5), *base.SNAPSHOT_RULES]
    base.main()


if __name__ == "__main__":
    main()
