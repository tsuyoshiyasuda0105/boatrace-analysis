"""Optional venue/boat probability correction for web predictions.

The production web predictor imports this module unconditionally.  Keep the
default behaviour conservative: when no explicit correction table is bundled,
return the prediction frame unchanged.
"""
from __future__ import annotations

from typing import Any

import pandas as pd


def apply_venue_boat_correction(df: pd.DataFrame, _target_date: str | None = None) -> pd.DataFrame:
    """Apply optional venue/boat correction coefficients.

    This is intentionally a no-op unless a future model artifact wires in
    explicit coefficients.  Returning a copy would increase Render memory use,
    so we return the input frame unchanged.
    """
    if df is None:
        return df
    return df


__all__ = ["apply_venue_boat_correction"]
