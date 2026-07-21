from src.evaluation.accident_dent_strategy import (
    ACCIDENT_DENT_BY_KEY,
    ACCIDENT_DENT_STRATEGIES,
    live_matches,
    matches_strategy,
)


ADOPTED_KEYS = {
    "toda_dent2_makuri4_41",
    "toda_a_accident2_13_exa",
    "edogawa_late_dent2_makuri3_31",
    "edogawa_a_accident4_12_exa",
    "biwako_dent2_makuri3_31",
    "amagasaki_dent3_makuri4_41",
    "shimonoseki_a_accident4_13_exa",
}

EXCLUDED_KEYS = {
    "hamanako_dent2_makuri3_31",
    "karatsu_dent2_makuri3_31",
    "kojima_dent3_makuri4_41",
    "omura_dent2_makuri3_31",
    "tokoname_dent2_makuri3_31",
    "wakamatsu_dent3_makuri4_41",
}


def _matching_context(strategy):
    ctx = {"stadium": strategy.venue, "race_number": strategy.round_min}
    for boat in range(1, 7):
        ctx[f"boat{boat}_class"] = 1
        ctx[f"boat{boat}_avg_st_180"] = 0.10
        ctx[f"boat{boat}_avg_st_count"] = 30
        ctx[f"boat{boat}_national_top1"] = 10.0
        ctx[f"boat{boat}_national_top2"] = 50.0
        ctx[f"boat{boat}_motor_top2"] = 30.0
        ctx[f"boat{boat}_accident_starts"] = 0
        ctx[f"boat{boat}_accident_rate"] = 0.0

    dent = strategy.dent_boat
    attack = strategy.attack_boat
    ctx[f"boat{dent}_accident_starts"] = 8
    ctx[f"boat{dent}_accident_rate"] = 0.50
    ctx[f"boat{dent}_avg_st_180"] = max(
        strategy.dent_st_min,
        0.10 + strategy.adjacent_gap_min,
    ) + 0.001
    ctx[f"boat{attack}_avg_st_180"] = min(0.10, strategy.attack_st_max)
    ctx[f"boat{dent}_national_top2"] = 20.0
    ctx[f"boat{dent}_motor_top2"] = 30.0
    return ctx


def test_only_recent_five_month_survivors_are_adopted():
    assert len(ACCIDENT_DENT_STRATEGIES) == 7
    assert set(ACCIDENT_DENT_BY_KEY) == ADOPTED_KEYS
    assert not (set(ACCIDENT_DENT_BY_KEY) & EXCLUDED_KEYS)


def test_all_adopted_rules_match_their_exact_prerace_context():
    for strategy in ACCIDENT_DENT_STRATEGIES:
        ctx = _matching_context(strategy)
        assert matches_strategy(strategy, ctx), strategy.key
        assert strategy in live_matches(ctx)


def test_accident_rate_and_start_sample_are_hard_requirements():
    strategy = ACCIDENT_DENT_BY_KEY["toda_dent2_makuri4_41"]
    ctx = _matching_context(strategy)

    ctx["boat2_accident_rate"] = 0.499
    assert not matches_strategy(strategy, ctx)

    ctx = _matching_context(strategy)
    ctx["boat2_accident_starts"] = 7
    assert not matches_strategy(strategy, ctx)


def test_a_class_accident_rules_reject_b_class_dent_boat():
    for key in (
        "toda_a_accident2_13_exa",
        "edogawa_a_accident4_12_exa",
        "shimonoseki_a_accident4_13_exa",
    ):
        strategy = ACCIDENT_DENT_BY_KEY[key]
        ctx = _matching_context(strategy)
        ctx[f"boat{strategy.dent_boat}_class"] = 3
        assert not matches_strategy(strategy, ctx), key


def test_adopted_bets_and_published_metrics_are_fixed():
    expected = {
        "toda_dent2_makuri4_41": ("4-1", 38, 8, 159.7),
        "toda_a_accident2_13_exa": ("1-3", 17, 5, 299.4),
        "edogawa_late_dent2_makuri3_31": ("3-1", 31, 8, 151.0),
        "edogawa_a_accident4_12_exa": ("1-2", 15, 7, 284.7),
        "biwako_dent2_makuri3_31": ("3-1", 56, 12, 166.8),
        "amagasaki_dent3_makuri4_41": ("4-1", 26, 6, 168.8),
        "shimonoseki_a_accident4_13_exa": ("1-3", 17, 6, 201.2),
    }
    actual = {
        strategy.key: (
            strategy.combination,
            strategy.sample_size,
            strategy.hits,
            strategy.recovery,
        )
        for strategy in ACCIDENT_DENT_STRATEGIES
    }
    assert actual == expected
