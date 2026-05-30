"""src.evaluation.exhibition_bonus の unit test."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evaluation import exhibition_bonus as eb


class TestComputeBonusScore:
    """compute_bonus_score の境界条件."""

    def test_both_axes_active_score_2(self):
        """1号艇 最速 (best差=0) + 2号艇 < 3号艇 → score=2."""
        # 6艇分の展示タイム: 1号艇が最速、 2号艇 < 3号艇
        all_times = [6.70, 6.75, 6.80, 6.85, 6.90, 6.95]
        score, detail = eb.compute_bonus_score(
            boat1_ex_time=6.70, boat2_ex_time=6.75, boat3_ex_time=6.80,
            all_ex_times=all_times,
        )
        assert score == 2
        assert detail["axis_best_diff"] is True
        assert detail["axis_boat2_faster"] is True
        assert detail["incomplete"] is False
        assert detail["best_time"] == 6.70
        assert detail["boat1_best_diff"] == 0.0

    def test_only_best_diff_axis(self):
        """1号艇 ≤0.03 (軸A) のみ. 2号艇 > 3号艇 (軸B 不成立) → score=1."""
        all_times = [6.72, 6.85, 6.80, 6.75, 6.70, 6.78]  # 5号艇 最速
        score, detail = eb.compute_bonus_score(
            boat1_ex_time=6.72, boat2_ex_time=6.85, boat3_ex_time=6.80,
            all_ex_times=all_times,
        )
        assert score == 1
        assert detail["axis_best_diff"] is True   # 6.72 - 6.70 = 0.02 ≤ 0.03
        assert detail["axis_boat2_faster"] is False

    def test_only_boat2_faster_axis(self):
        """1号艇 が遅い (軸A 不成立) + 2号艇 < 3号艇 (軸B) → score=1."""
        all_times = [6.85, 6.75, 6.80, 6.72, 6.70, 6.78]
        score, detail = eb.compute_bonus_score(
            boat1_ex_time=6.85, boat2_ex_time=6.75, boat3_ex_time=6.80,
            all_ex_times=all_times,
        )
        assert score == 1
        assert detail["axis_best_diff"] is False  # 6.85 - 6.70 = 0.15 > 0.03
        assert detail["axis_boat2_faster"] is True

    def test_neither_axis(self):
        """両軸とも不成立 → score=0."""
        all_times = [6.85, 6.80, 6.75, 6.70, 6.72, 6.78]
        score, detail = eb.compute_bonus_score(
            boat1_ex_time=6.85, boat2_ex_time=6.80, boat3_ex_time=6.75,
            all_ex_times=all_times,
        )
        assert score == 0
        assert detail["axis_best_diff"] is False
        assert detail["axis_boat2_faster"] is False
        assert detail["incomplete"] is False

    def test_boundary_best_diff_just_003(self):
        """best差 = 0.03 ちょうど → 軸A 成立 (≤ 0.03)."""
        all_times = [6.73, 6.85, 6.90, 6.92, 6.70, 6.95]
        score, detail = eb.compute_bonus_score(
            boat1_ex_time=6.73, boat2_ex_time=6.85, boat3_ex_time=6.90,
            all_ex_times=all_times,
        )
        # 軸A: 6.73 - 6.70 = 0.03 (= しきい値 OK)
        assert detail["axis_best_diff"] is True
        # 軸B: 6.85 < 6.90 → 成立
        assert detail["axis_boat2_faster"] is True
        assert score == 2

    def test_boundary_best_diff_just_over_003(self):
        """best差 = 0.04 → 軸A 不成立."""
        all_times = [6.74, 6.85, 6.90, 6.92, 6.70, 6.95]
        score, detail = eb.compute_bonus_score(
            boat1_ex_time=6.74, boat2_ex_time=6.85, boat3_ex_time=6.90,
            all_ex_times=all_times,
        )
        assert detail["axis_best_diff"] is False  # 6.74 - 6.70 = 0.04 > 0.03

    def test_incomplete_when_only_5_times(self):
        """6艇分揃ってないとき → 補助点未集計 (score=0, incomplete=True)."""
        all_times = [6.70, 6.75, 6.80, None, 6.90, 6.95]  # 4号艇 欠損
        score, detail = eb.compute_bonus_score(
            boat1_ex_time=6.70, boat2_ex_time=6.75, boat3_ex_time=6.80,
            all_ex_times=all_times,
        )
        assert score == 0
        assert detail["incomplete"] is True

    def test_boat2_equal_to_boat3_not_faster(self):
        """boat2 == boat3 のときは軸B 不成立 (strict <)."""
        all_times = [6.70, 6.80, 6.80, 6.85, 6.90, 6.95]
        score, detail = eb.compute_bonus_score(
            boat1_ex_time=6.70, boat2_ex_time=6.80, boat3_ex_time=6.80,
            all_ex_times=all_times,
        )
        # 軸A: 1号艇 最速 → 成立
        assert detail["axis_best_diff"] is True
        # 軸B: 6.80 < 6.80 → 不成立
        assert detail["axis_boat2_faster"] is False
        assert score == 1


class TestRecommendedBetYen:
    def test_score_0_returns_100(self):
        assert eb.recommended_bet_yen(0) == 100

    def test_score_1_returns_150(self):
        assert eb.recommended_bet_yen(1) == 150

    def test_score_2_returns_200(self):
        assert eb.recommended_bet_yen(2) == 200

    def test_skip_zero_makes_score_0_zero_yen(self):
        assert eb.recommended_bet_yen(0, skip_zero=True) == 0
        # 1, 2 は通常通り
        assert eb.recommended_bet_yen(1, skip_zero=True) == 150
        assert eb.recommended_bet_yen(2, skip_zero=True) == 200


class TestScoreLabel:
    def test_score_0_label(self):
        assert "0" in eb.score_label(0)

    def test_score_1_label(self):
        assert "1" in eb.score_label(1) or "★" in eb.score_label(1)

    def test_score_2_label_has_double_star(self):
        assert "★★" in eb.score_label(2)


class TestEvaluateL4WithBonus:
    def test_score_2_full_result(self):
        all_times = [6.70, 6.75, 6.80, 6.85, 6.90, 6.95]
        r = eb.evaluate_l4_with_bonus(
            boat1_ex_time=6.70, boat2_ex_time=6.75, boat3_ex_time=6.80,
            all_ex_times=all_times,
        )
        assert r["score"] == 2
        assert r["axis_best_diff"] is True
        assert r["axis_boat2_faster"] is True
        assert r["incomplete"] is False
        assert r["recommended_bet_yen"] == 200
        assert r["expected_roi_pct"] == 180.8

    def test_score_0_full_result(self):
        all_times = [6.85, 6.80, 6.75, 6.70, 6.72, 6.78]
        r = eb.evaluate_l4_with_bonus(
            boat1_ex_time=6.85, boat2_ex_time=6.80, boat3_ex_time=6.75,
            all_ex_times=all_times,
        )
        assert r["score"] == 0
        assert r["recommended_bet_yen"] == 100  # skip_zero=False
        assert r["expected_roi_pct"] == 146.9

    def test_skip_zero_passes_through(self):
        all_times = [6.85, 6.80, 6.75, 6.70, 6.72, 6.78]
        r = eb.evaluate_l4_with_bonus(
            boat1_ex_time=6.85, boat2_ex_time=6.80, boat3_ex_time=6.75,
            all_ex_times=all_times,
            skip_zero=True,
        )
        assert r["score"] == 0
        assert r["recommended_bet_yen"] == 0  # 見送り

    def test_incomplete_returns_baseline_roi(self):
        """展示タイム不足のときは ROI 推定がベースライン (展示なし L4)"""
        all_times = [6.70, None, 6.80, 6.85, 6.90, 6.95]
        r = eb.evaluate_l4_with_bonus(
            boat1_ex_time=6.70, boat2_ex_time=None, boat3_ex_time=6.80,
            all_ex_times=all_times,
        )
        assert r["score"] == 0
        assert r["incomplete"] is True
        assert r["expected_roi_pct"] == eb.ROI_BASELINE_L4_PCT  # 164.4
