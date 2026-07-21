from scripts.backfill_accident_dent_daily_cache import patch_daily_stats
from src.evaluation.accident_dent_strategy import (
    ACCIDENT_DENT_CACHE_VERSION,
    ACCIDENT_DENT_STRATEGIES,
)


def test_patch_daily_stats_preserves_other_strategies_and_sets_all_adopted_keys():
    first = ACCIDENT_DENT_STRATEGIES[0]
    patched = patch_daily_stats(
        {"date": "2026-07-01", "other_strategy_bets": 9},
        {first.key: {"bets": 2, "hits": 1, "pay": 350}},
    )

    assert patched["other_strategy_bets"] == 9
    assert patched[f"{first.key}_bets"] == 2
    assert patched[f"{first.key}_hits"] == 1
    assert patched[f"{first.key}_pay"] == 350
    assert patched[f"{first.key}_recovery"] == 175.0
    assert patched[f"{first.key}_profit"] == 150
    assert patched[f"{first.key}_roi"] == 75.0
    assert patched["_accident_dent_version"] == ACCIDENT_DENT_CACHE_VERSION

    for strategy in ACCIDENT_DENT_STRATEGIES[1:]:
        assert patched[f"{strategy.key}_bets"] == 0
        assert patched[f"{strategy.key}_hits"] == 0
        assert patched[f"{strategy.key}_pay"] == 0
        assert patched[f"{strategy.key}_recovery"] is None
        assert patched[f"{strategy.key}_profit"] == 0
