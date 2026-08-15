"""P1-1 Phase A characterization tests for the current app.py strategy rules.

The strategy evaluators are local functions inside Flask route factories.  This
test-only loader executes one existing function definition at a time without
changing or publishing application code.  Exact dictionaries intentionally pin
today's behaviour; they are not claims that the rules are correct.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.strategies.signals import (
    _allow_market_signal_with_female,
    _compute_tetsuban,
    _detect_niche_signals,
    _evaluate_candidate_134_signal,
    _evaluate_g23_optb_signal,
    _evaluate_general_c_signal,
    _evaluate_l4_general_200,
    _pick_best_market_signal,
    _prefer_adopted_signal_over_general200,
)


def test_g23_optb_adopted_signal_exact_output():
    assert _evaluate_g23_optb_signal(
        stadium=16,
        grade=3,
        cls=1,
        min_payout=500,
        natl_1=7.0,
        local_1=6.0,
        avg_st=0.154,
        age=49,
        ex_st=0.179,
        boat2_motor_top2=40.0,
        weather=1,
        n_female=0,
    ) == {
        "level": "g23_optb_tri",
        "label": "G2/G3 1-2-3",
        "recovery": 204.0,
        "n": 1189,
        "bet": "3連単 1-2-3",
        "rank": "trifecta_niche",
        "rank_label": "3連単ニッチ",
        "rank_emoji": "🎯",
        "is_reference": False,
        "tetsuban_score": 4,
        "tetsuban_label": "G2/G3 1-2-3",
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("stadium", 24),
        ("min_payout", 1000),
        ("boat2_motor_top2", 40.1),
        ("weather", 3),
        ("n_female", 1),
    ],
)
def test_g23_optb_current_exclusion_boundaries(field, value):
    inputs = {
        "stadium": 16,
        "grade": 3,
        "cls": 1,
        "min_payout": 500,
        "natl_1": 7.0,
        "local_1": 6.0,
        "avg_st": 0.154,
        "age": 49,
        "ex_st": 0.179,
        "boat2_motor_top2": 40.0,
        "weather": 1,
        "n_female": 0,
    }
    inputs[field] = value

    assert _evaluate_g23_optb_signal(**inputs) is None


def test_candidate_134_overlap_prefers_last_matching_candidate():
    assert _evaluate_candidate_134_signal(
        stadium=5,
        grade=1,
        race_number=10,
        natl_1=7.8,
        age=45,
        course1=1,
        boat2_motor_top2=50.0,
        avg_st=0.159,
        avg_st_n=6,
        weather=1,
        n_female=0,
        target_date_iso="2026-06-15",
    ) == {
        "level": "cand4",
        "label": "候補4",
        "recovery": 293.3,
        "bet": "3連単 1-2-3",
        "n": 9,
        "rank": "cand4",
        "rank_label": "候補4",
        "natl_1": 7.8,
        "local_1": None,
        "is_reference": False,
        "candidate_keys": ["cand1", "cand3", "cand4"],
        "candidate_labels": ["候補1", "候補3", "候補4"],
        "tetsuban_score": 5,
        "tetsuban_label": "候補4",
    }


def test_general_c_adopted_signal_exact_output():
    assert _evaluate_general_c_signal(
        stadium=1,
        grade=5,
        cls=1,
        min_payout=500,
        l4_band_ok=False,
        natl_1=7.0,
        local_1=7.0,
        boat1_motor_top2=35.0,
        boat2_motor_top2=34.9,
        boat3_natl_1=5.0,
        weather=1,
        n_female=0,
        EXCLUDE_B_VENUES={2, 4, 7, 8, 10, 19, 21, 24},
    ) == {
        "level": "general_c_tri",
        "label": "1号艇強+2号艇M弱+3号艇強",
        "recovery": 240.8,
        "n": 62,
        "bet": "3連単 1-2-3",
        "rank": "trifecta_niche",
        "rank_label": "1-2-3採用",
        "rank_emoji": "採用",
        "natl_1": 7.0,
        "local_1": 7.0,
        "is_reference": False,
        "is_trifecta_niche": True,
        "trifecta_niche_name": "1号艇強+2号艇M弱+3号艇強 1-2-3",
        "trifecta_niche_tag": "一般戦 + 1号艇全国1着率>=7 + 当地1着率>=7 + 1号艇モーター>=35 + 2号艇モーター<35 + 3号艇全国1着率>=5",
        "trifecta_niche_hit_rate": 19.4,
        "trifecta_niche_recovery": 240.8,
        "tetsuban_score": 6,
        "tetsuban_label": "1-2-3採用",
    }


@pytest.mark.parametrize(
    ("stadium", "weather", "n_female"),
    [(2, 1, 0), (1, 3, 0), (1, 1, 1)],
)
def test_general_c_current_b_venue_rain_and_female_gates(stadium, weather, n_female):
    assert _evaluate_general_c_signal(
        stadium=stadium,
        grade=5,
        cls=1,
        min_payout=500,
        natl_1=7.0,
        local_1=7.0,
        boat1_motor_top2=35.0,
        boat2_motor_top2=34.9,
        boat3_natl_1=5.0,
        weather=weather,
        n_female=n_female,
        EXCLUDE_B_VENUES={2, 4, 7, 8, 10, 19, 21, 24},
    ) is None


def test_pick_best_prefers_adopted_level_over_higher_recovery_generic():
    generic = {"level": "generic_demo", "label": "generic", "bet": "単勝 6", "recovery": 999.0}
    adopted = {"level": "g23_optb_tri", "label": "adopted", "bet": "3連単 1-2-3", "recovery": 204.0}

    assert _pick_best_market_signal(
        generic,
        adopted,
        ACCIDENT_DENT_STRATEGIES=[SimpleNamespace(key="accident_demo")],
    ) == {
        **adopted,
        "matched_levels": ["generic_demo", "g23_optb_tri"],
        "matched_labels": ["generic", "adopted"],
        "matched_bets": ["単勝 6", "3連単 1-2-3"],
        "matched_recoveries": [999.0, 204.0],
    }


def test_female_gate_keeps_only_explicit_or_adopted_strategy_families():
    kwargs = {"ROI_STRATEGY_KEYS": ("g23_optb_tri",)}
    assert not _allow_market_signal_with_female({"level": "generic"}, 1, **kwargs)
    assert _allow_market_signal_with_female({"level": "generic", "is_exacta_niche": True}, 1, **kwargs)
    assert _allow_market_signal_with_female({"level": "g23_optb_tri"}, 1, **kwargs)
    assert _allow_market_signal_with_female({"level": "generic", "allow_female_market_signal": True}, 1, **kwargs)
    assert _allow_market_signal_with_female({"level": "generic"}, 0, **kwargs)


def test_general200_overlay_yields_to_adopted_and_preserves_overlay_metadata():
    selected = {
        "level": "l4_general_200",
        "label": "general200",
        "bet": "3連単 1-2-3",
        "recovery": 200.0,
        "matched_levels": ["l4_general_200"],
        "is_l4_general_200": True,
        "general200_hit_rate": 20.0,
        "general200_recovery": 200.0,
        "general200_n": 10,
        "general200_boat2_top2": 40.0,
        "general200_boat2_exhibition_time": 6.70,
        "general200_boat3_exhibition_time": 6.80,
        "general200_boat2_faster": True,
        "general200_ex_st": 0.10,
        "general200_ex_st_good": True,
    }
    adopted = {
        "level": "g23_optb_tri",
        "label": "adopted",
        "bet": "3連単 1-2-3",
        "recovery": 204.0,
    }

    assert _prefer_adopted_signal_over_general200(selected, adopted) == {
        **adopted,
        "matched_levels": ["l4_general_200", "g23_optb_tri"],
        "matched_labels": ["general200", "adopted"],
        "matched_bets": ["3連単 1-2-3"],
        "matched_recoveries": [200.0, 204.0],
        "is_l4_general_200": True,
        "general200_hit_rate": 20.0,
        "general200_recovery": 200.0,
        "general200_n": 10,
        "general200_boat2_top2": 40.0,
        "general200_boat2_exhibition_time": 6.70,
        "general200_boat3_exhibition_time": 6.80,
        "general200_boat2_faster": True,
        "general200_ex_st": 0.10,
        "general200_ex_st_good": True,
    }


def test_tetsuban_score_compresses_all_current_bonuses_to_five_stars():
    assert _compute_tetsuban(
        {
            "level": "SG",
            "is_f1": True,
            "is_1c80": True,
            "is_l4_pro": True,
            "rank": "plus_plus",
        },
        12,
    ) == (5, "鉄板 5★")


def test_retired_general200_evaluator_remains_a_noop():
    assert _evaluate_l4_general_200(1, 5, 1, 7.5, 45.0, 6.70, 6.80, 0.10) is None


def test_niche_signal_exact_ultra_output():
    assert _detect_niche_signals(
        [{"boat_number": 5, "class_number": 2}],
        {"boats": {"5": {"tilt_adjustment": 3.0}}},
    ) == [
        {
            "level": "ultra",
            "boat_number": 5,
            "tilt": 3.0,
            "class_label": "A2",
            "title": "🔥🔥🔥 ニッチ大穴チャンス",
            "desc": "艇5 + チルト3.0 + A2選手の組合せ。Backtest ROI +118.29% (n=41, CI [-13%, +290%], P(ROI>0)=95.0%)",
            "recommend": "三連単 5-X-Y 上位10通り買い推奨",
            "warning": "n=41 のサンプル。実運用は要慎重",
        }
    ]
