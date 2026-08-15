"""Shared cache contract for operational ROI data.

Keep cache versions and the executable strategy signature here so the web
process and every scheduler validate the same contract.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path


ROI_DAILY_CACHE_VERSION = "adopted_daily_select_v36"
MARKET_SIGNALS_CACHE_VERSION = "v27"
STRATEGY_PAGE_CACHE_VERSION = "strategy-roi-v18"

STRATEGY_DEFINITION_SOURCE_PATHS = (
    "src/strategies/signals.py",
    "src/evaluation/l4_strategy.py",
    "src/evaluation/course_fit_strategy.py",
    "src/evaluation/accident_dent_strategy.py",
    "src/evaluation/omura_124_original_strategy.py",
)


def _add_signature_input(digest, label: str, value: bytes) -> None:
    """Add one unambiguous, order-sensitive input to the signature."""
    label_bytes = label.encode("utf-8")
    digest.update(len(label_bytes).to_bytes(4, "big"))
    digest.update(label_bytes)
    digest.update(len(value).to_bytes(8, "big"))
    digest.update(value)


@lru_cache(maxsize=8)
def _strategy_definition_signature_cached(
    root: Path,
    version_values: tuple[tuple[str, str], ...],
) -> str:
    digest = hashlib.sha1()
    _add_signature_input(digest, "signature_format", b"strategy-code-v1")

    for relative_path in STRATEGY_DEFINITION_SOURCE_PATHS:
        try:
            source = (root / relative_path).read_bytes()
        except OSError:
            continue
        _add_signature_input(digest, f"source:{relative_path}", source)

    for name, value in version_values:
        _add_signature_input(digest, f"version:{name}", value.encode("utf-8"))

    return digest.hexdigest()[:10]


def strategy_definition_signature(repo_root: Path | None = None) -> str:
    """Return a deterministic signature of deployed executable strategies."""
    root = (repo_root or Path(__file__).resolve().parents[1]).resolve()
    version_values = (
        ("ROI_DAILY_CACHE_VERSION", ROI_DAILY_CACHE_VERSION),
        ("MARKET_SIGNALS_CACHE_VERSION", MARKET_SIGNALS_CACHE_VERSION),
        ("STRATEGY_PAGE_CACHE_VERSION", STRATEGY_PAGE_CACHE_VERSION),
    )
    return _strategy_definition_signature_cached(root, version_values)
