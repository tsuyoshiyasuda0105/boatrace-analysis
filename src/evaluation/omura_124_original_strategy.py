"""Omura 1-2-4 strategy using original exhibition and exact T-5 odds."""

from __future__ import annotations

from typing import Mapping


STRATEGY_KEY = "omura_124_original_t5_tri"
RECOVERY_RATE = 294.4
SAMPLE_SIZE = 45
HIT_RATE = 24.4


def rank_times_by_boat(times: Mapping[int, float | None]) -> dict[int, int]:
    """Return competition ranks where a smaller exhibition time is better."""
    valid = {
        int(boat): float(value)
        for boat, value in times.items()
        if value is not None and float(value) > 0
    }
    return {
        boat: 1 + sum(1 for other in valid.values() if other < value)
        for boat, value in valid.items()
    }


def matches_omura_124_original(
    *,
    stadium_number: int | None,
    boat4_straight_rank: int | None,
    boat4_lap_rank: int | None,
    wind_speed: float | None,
    t5_payout: int | None,
) -> bool:
    """Evaluate only values that were observable five minutes before closing."""
    if stadium_number != 24:
        return False
    if boat4_straight_rank is None or boat4_straight_rank > 2:
        return False
    if boat4_lap_rank is None or boat4_lap_rank > 3:
        return False
    if wind_speed is None or float(wind_speed) > 3.0:
        return False
    return t5_payout is not None and 1000 <= int(t5_payout) < 2000
