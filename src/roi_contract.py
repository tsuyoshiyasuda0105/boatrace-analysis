"""Shared cache contract for operational ROI data.

Keep cache versions and the adopted-strategy document signature here so the
web process and every scheduler validate the same contract.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


ROI_DAILY_CACHE_VERSION = "adopted_daily_select_v36"
MARKET_SIGNALS_CACHE_VERSION = "v27"
STRATEGY_PAGE_CACHE_VERSION = "strategy-roi-v17"


def strategy_definition_signature(repo_root: Path | None = None) -> str:
    """Return the deployed adopted-strategy definition signature."""
    root = repo_root or Path(__file__).resolve().parents[1]
    try:
        return hashlib.sha1((root / "adopted_strategies.md").read_bytes()).hexdigest()[:10]
    except OSError:
        return "nosig"
