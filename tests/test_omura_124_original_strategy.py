from src.evaluation.omura_124_original_strategy import (
    matches_omura_124_original,
    rank_times_by_boat,
)


def test_rank_times_uses_competition_ranking():
    assert rank_times_by_boat({1: 6.80, 2: 6.70, 3: 6.70, 4: 6.90}) == {
        1: 3,
        2: 1,
        3: 1,
        4: 4,
    }


def test_strategy_accepts_boundary_values():
    assert matches_omura_124_original(
        stadium_number=24,
        boat4_straight_rank=2,
        boat4_lap_rank=3,
        wind_speed=3.0,
        t5_payout=1000,
    )
    assert matches_omura_124_original(
        stadium_number=24,
        boat4_straight_rank=1,
        boat4_lap_rank=1,
        wind_speed=0,
        t5_payout=1999,
    )


def test_strategy_rejects_outside_or_missing_values():
    base = {
        "stadium_number": 24,
        "boat4_straight_rank": 2,
        "boat4_lap_rank": 3,
        "wind_speed": 3.0,
        "t5_payout": 1000,
    }
    for key, value in (
        ("stadium_number", 23),
        ("boat4_straight_rank", 3),
        ("boat4_lap_rank", 4),
        ("wind_speed", 3.1),
        ("t5_payout", 999),
        ("t5_payout", 2000),
        ("t5_payout", None),
    ):
        values = dict(base)
        values[key] = value
        assert not matches_omura_124_original(**values)
