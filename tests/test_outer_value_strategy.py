"""src.evaluation.outer_value_strategy の unit test.

外枠本命 (head=6 & p1>=0.35 → 6号艇単勝100円) 戦略の発火条件・買い目・
評価 dict・定数整合性を回帰テストする (DB 不要、純ロジック)。

検証根拠: reports/ev_head6_deepdive.md (4年バックテスト)
  単勝 ROI 140% / 的中 53% / n=103 / 約4.1回/月。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evaluation import outer_value_strategy as ov


# ============================================================
# is_outer6_eligible: 発火条件
# ============================================================
class TestIsOuter6Eligible:
    """head==6 かつ p1>=0.35 のときだけ発火する。"""

    def test_head6_p1_above_threshold_eligible(self):
        assert ov.is_outer6_eligible(6, 0.40) is True

    def test_head6_p1_at_threshold_eligible(self):
        """境界 p1==0.35 は >= なので発火。"""
        assert ov.is_outer6_eligible(6, 0.35) is True

    def test_head6_p1_just_below_threshold_rejected(self):
        assert ov.is_outer6_eligible(6, 0.349) is False

    def test_head6_low_p1_rejected(self):
        assert ov.is_outer6_eligible(6, 0.20) is False

    def test_inner_head_high_p1_rejected(self):
        """1号艇本命は (たとえ p1 が高くても) 対象外。"""
        assert ov.is_outer6_eligible(1, 0.90) is False

    def test_head5_rejected(self):
        """5号艇本命は外枠だが本戦略 (6号艇単独) の対象外。"""
        assert ov.is_outer6_eligible(5, 0.50) is False

    def test_head4_rejected(self):
        assert ov.is_outer6_eligible(4, 0.50) is False


class TestIsOuter6EligibleEdgeCases:
    """NULL / 型ゆらぎの安全側判定。"""

    def test_head_none_rejected(self):
        assert ov.is_outer6_eligible(None, 0.40) is False

    def test_p1_none_rejected(self):
        assert ov.is_outer6_eligible(6, None) is False

    def test_both_none_rejected(self):
        assert ov.is_outer6_eligible(None, None) is False

    def test_string_inputs_coerced(self):
        """str で来ても int/float に変換して判定。"""
        assert ov.is_outer6_eligible("6", "0.40") is True

    def test_garbage_string_rejected(self):
        assert ov.is_outer6_eligible("six", "fast") is False


# ============================================================
# get_outer6_bets: 買い目
# ============================================================
class TestGetOuter6Bets:
    def test_eligible_single_win_bet(self):
        """発火時は 6号艇単勝を 100円 1点のみ。"""
        assert ov.get_outer6_bets(6, 0.40) == [("win", "6", 100)]

    def test_eligible_at_threshold(self):
        assert ov.get_outer6_bets(6, 0.35) == [("win", "6", 100)]

    def test_ineligible_empty(self):
        assert ov.get_outer6_bets(1, 0.90) == []
        assert ov.get_outer6_bets(6, 0.30) == []

    def test_none_empty(self):
        assert ov.get_outer6_bets(None, None) == []


# ============================================================
# evaluate_outer6_race: 評価 dict
# ============================================================
class TestEvaluateOuter6Race:
    def test_eligible_dict(self):
        r = ov.evaluate_outer6_race(6, 0.42)
        assert r["eligible"] is True
        assert r["head"] == 6
        assert r["p1"] == 0.42
        assert r["bet_type"] == "win"
        assert r["combination"] == "6"
        assert r["amount_yen"] == 100
        assert r["recovery"] == ov.OUTER6_RECOVERY
        assert r["hit_rate"] == ov.OUTER6_HIT_RATE
        assert r["n"] == ov.OUTER6_SAMPLE_N
        assert r["label"] == ov.OUTER6_LABEL

    def test_ineligible_dict(self):
        r = ov.evaluate_outer6_race(1, 0.90)
        assert r["eligible"] is False
        assert r["head"] == 1
        assert r["p1"] == 0.90

    def test_none_inputs_dict(self):
        r = ov.evaluate_outer6_race(None, None)
        assert r["eligible"] is False
        assert r["head"] is None
        assert r["p1"] is None


# ============================================================
# 定数の整合性
# ============================================================
class TestConstants:
    def test_head_boat_is_6(self):
        assert ov.OUTER6_HEAD_BOAT == 6

    def test_threshold_is_035(self):
        assert ov.OUTER6_P1_THRESHOLD == 0.35

    def test_bet_unit_100yen(self):
        assert ov.OUTER6_BET_UNIT_YEN == 100

    def test_bet_type_win(self):
        assert ov.OUTER6_BET_TYPE == "win"

    def test_recovery_positive_ev(self):
        """検証 ROI は 100% 超 (= 期待値プラス)。"""
        assert ov.OUTER6_RECOVERY > 100.0

    def test_hit_rate_realistic(self):
        assert 0.0 < ov.OUTER6_HIT_RATE < 1.0

    def test_sample_n_positive(self):
        assert ov.OUTER6_SAMPLE_N > 0

    def test_all_exports_present(self):
        for name in ov.__all__:
            assert hasattr(ov, name), f"__all__ に未定義の名前: {name}"
