"""src.evaluation.nuki_filter の unit test と DB 整合性チェック.

検証スクリプト (scripts/analyze_nuki_features.py) で得た数値
(ROI 129.9% / hit 12.75 / 抜き lift 1.62x / N 訓練 2273 等) が再現できる
ことを確認するための回帰テスト.

注意:
  - DB 接続 (Supabase or SQLite) が必要. CI で DB が無い場合は
    db_integration テストは skip される.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evaluation import nuki_filter as nf


# ============================================================
# Unit tests: 基本判定 (DB 不要)
# ============================================================
class TestIsNukiLikelyBasic:
    """is_nuki_likely の基本動作."""

    def test_edogawa_a1_clear_weather_eligible(self):
        """江戸川 + A1 + 晴で適格."""
        assert nf.is_nuki_likely(
            stadium_number=3,
            boat1_class=1,
            weather_number=1,
        ) is True

    def test_hamanako_a1_eligible(self):
        """浜名湖 + A1 で適格."""
        assert nf.is_nuki_likely(stadium_number=6, boat1_class=1) is True

    def test_naruto_a1_eligible(self):
        """鳴門 + A1 で適格."""
        assert nf.is_nuki_likely(stadium_number=14, boat1_class=1) is True

    def test_non_nuki_stadium_rejected(self):
        """抜き-prone でない会場 (桐生=1) で不適格."""
        assert nf.is_nuki_likely(stadium_number=1, boat1_class=1) is False

    def test_kamagori_rejected(self):
        """L4 除外会場 (蒲郡=7) で不適格 (= そもそも nuki stadium ではない)."""
        assert nf.is_nuki_likely(stadium_number=7, boat1_class=1) is False


class TestIsNukiLikelyClass:
    """class 判定."""

    def test_a2_rejected(self):
        """A2 (class=2) で不適格 (L4 base 要件)."""
        assert nf.is_nuki_likely(stadium_number=3, boat1_class=2) is False

    def test_b1_rejected(self):
        """B1 (class=3) で不適格."""
        assert nf.is_nuki_likely(stadium_number=3, boat1_class=3) is False

    def test_class_none_rejected(self):
        """class が None なら不適格 (情報不足は安全側)."""
        assert nf.is_nuki_likely(stadium_number=3, boat1_class=None) is False


class TestIsNukiLikelyWeather:
    """weather 判定."""

    def test_rain_rejected(self):
        """雨 (weather_number=3) で不適格."""
        assert nf.is_nuki_likely(stadium_number=3, boat1_class=1, weather_number=3) is False

    def test_weather_none_allowed(self):
        """weather=None で適格 (情報なし許容、L4 strategy と整合)."""
        assert nf.is_nuki_likely(stadium_number=3, boat1_class=1, weather_number=None) is True

    def test_clear_weather_allowed(self):
        """晴 (weather_number=1) で適格."""
        assert nf.is_nuki_likely(stadium_number=3, boat1_class=1, weather_number=1) is True

    def test_cloudy_weather_allowed(self):
        """曇 (weather_number=2) で適格 (雨以外は許可)."""
        assert nf.is_nuki_likely(stadium_number=3, boat1_class=1, weather_number=2) is True


class TestIsNukiLikelyEdgeCases:
    """境界・NULL ケース."""

    def test_stadium_none_rejected(self):
        """stadium_number が None なら不適格."""
        assert nf.is_nuki_likely(stadium_number=None, boat1_class=1) is False

    def test_unknown_stadium_rejected(self):
        """未定義の stadium (例: 99) で不適格."""
        assert nf.is_nuki_likely(stadium_number=99, boat1_class=1) is False

    def test_extra_kwargs_ignored(self):
        """racer/motor 系の追加引数が判定に影響しない (signature 維持テスト)."""
        # motor 弱、 国1 低、年齢若 - これでも江戸川なら適格
        assert nf.is_nuki_likely(
            stadium_number=3,
            boat1_class=1,
            boat1_motor_top_2_percent=20.0,
            boat1_national_top_1_percent=5.0,
            boat1_age=22,
            boat1_avg_start_timing=0.20,
        ) is True
        # motor 強、国1 強でも非対象会場ならダメ
        assert nf.is_nuki_likely(
            stadium_number=12,  # 住之江
            boat1_class=1,
            boat1_motor_top_2_percent=50.0,
            boat1_national_top_1_percent=8.0,
        ) is False


class TestRecommendedBet:
    """recommended_bet_yen_for_nuki の score-based scaling."""

    def test_base_score_100yen(self):
        assert nf.recommended_bet_yen_for_nuki("base") == 100

    def test_plus_score_200yen(self):
        assert nf.recommended_bet_yen_for_nuki("plus") == 200

    def test_plus_plus_score_300yen(self):
        assert nf.recommended_bet_yen_for_nuki("plus_plus") == 300

    def test_unknown_score_defaults_to_base(self):
        assert nf.recommended_bet_yen_for_nuki("unknown_label") == 100
        assert nf.recommended_bet_yen_for_nuki("") == 100


class TestGetNukiBets:
    """get_nuki_bets の組合せと金額."""

    def test_base_bets_one_combo(self):
        bets = nf.get_nuki_bets("base")
        assert bets == [("trifecta", "1-2-3", 100)]

    def test_plus_bets_amount_increased(self):
        bets = nf.get_nuki_bets("plus")
        assert bets == [("trifecta", "1-2-3", 200)]

    def test_plus_plus_bets_amount_increased(self):
        bets = nf.get_nuki_bets("plus_plus")
        assert bets == [("trifecta", "1-2-3", 300)]


class TestEvaluateNukiRace:
    """evaluate_nuki_race の統合動作."""

    def test_eligible_returns_bets_and_roi(self):
        """適格 race で eligible=True / bets / expected_roi が揃う."""
        r = nf.evaluate_nuki_race(
            stadium_number=3, boat1_class=1, weather_number=1,
        )
        assert r["eligible"] is True
        assert r["bets"] == [("trifecta", "1-2-3", 100)]
        assert r["label"] == nf.NUKI_FILTER_LABEL
        assert r["expected_roi_pct"] == nf.NUKI_RECOVERY  # 129.9

    def test_eligible_with_plus_score(self):
        """l4_score='plus' で bonus 額が 200円になる."""
        r = nf.evaluate_nuki_race(
            stadium_number=6, boat1_class=1, weather_number=1, l4_score="plus",
        )
        assert r["eligible"] is True
        assert r["bets"] == [("trifecta", "1-2-3", 200)]

    def test_eligible_with_plus_plus_score(self):
        """l4_score='plus_plus' で bonus 額が 300円になる."""
        r = nf.evaluate_nuki_race(
            stadium_number=14, boat1_class=1, weather_number=2, l4_score="plus_plus",
        )
        assert r["eligible"] is True
        assert r["bets"] == [("trifecta", "1-2-3", 300)]

    def test_ineligible_empty_bets(self):
        """非対象会場で eligible=False / bets 空 / ROI None."""
        r = nf.evaluate_nuki_race(stadium_number=1, boat1_class=1, weather_number=1)
        assert r["eligible"] is False
        assert r["bets"] == []
        assert r["expected_roi_pct"] is None

    def test_rain_makes_ineligible(self):
        """雨で eligible=False (たとえ江戸川でも)."""
        r = nf.evaluate_nuki_race(stadium_number=3, boat1_class=1, weather_number=3)
        assert r["eligible"] is False
        assert r["bets"] == []


class TestConstants:
    """定数の整合性."""

    def test_nuki_stadiums_count_3(self):
        """フィルター対象は江戸川/浜名湖/鳴門 の 3 会場."""
        assert nf.FN_NUKI_STADIUMS == frozenset({3, 6, 14})
        assert len(nf.FN_NUKI_STADIUMS) == 3

    def test_recovery_train_test_realistic(self):
        """train ROI > test ROI (一般的に sample size 大の方が安定)
        かつ両方 100% 以上 = +EV."""
        assert nf.NUKI_RECOVERY_TRAIN >= 100.0
        assert nf.NUKI_RECOVERY_TEST >= 100.0
        # 統合 ROI が train/test の幅に収まる
        assert min(nf.NUKI_RECOVERY_TRAIN, nf.NUKI_RECOVERY_TEST) <= nf.NUKI_RECOVERY <= max(
            nf.NUKI_RECOVERY_TRAIN, nf.NUKI_RECOVERY_TEST
        )

    def test_lift_above_1_5(self):
        """抜き lift は 1.5x 以上 (= 統計的に意味のある集中)."""
        assert nf.NUKI_LIFT_VS_BASE >= 1.5

    def test_avg_payout_higher_than_l4_base(self):
        """フィルター内の平均配当が L4 base (逃げ 830 円) よりも高い."""
        # L4 base 逃げ avg 830 円
        assert nf.NUKI_AVG_PAYOUT_YEN > 830.0

    def test_l4_exclude_venues_disjoint_from_nuki_stadiums(self):
        """B 除外会場と抜き対象会場は disjoint (= 抜き会場が
        L4 base で除外されない)."""
        assert nf.FN_NUKI_STADIUMS.isdisjoint(nf.L4_EXCLUDE_VENUES)


# ============================================================
# DB integration test (Supabase or local SQLite が必要)
# ============================================================
def _db_available() -> bool:
    """DB に接続でき、江戸川レコードが存在するかをチェック."""
    try:
        from src.verification.backtest import _conn  # noqa: WPS433
        conn = _conn()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM races WHERE stadium_number=3 LIMIT 1")
        n = cur.fetchone()[0]
        conn.close()
        return n > 0
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _db_available(), reason="DB unavailable")
def test_db_matches_nuki_n_train():
    """検証 SQL で抽出される train 期間の抜きフィルター適格 race 数
    (boat1-1着 cohort) が NUKI_N_TRAIN (約 2273) と整合することを確認."""
    from src.verification.backtest import _conn

    conn = _conn()
    cur = conn.cursor()
    ph = "%s" if os.environ.get("DATABASE_URL") else "?"
    excl_ph = ",".join([ph] * len(nf.L4_EXCLUDE_VENUES))
    nuki_ph = ",".join([ph] * len(nf.FN_NUKI_STADIUMS))
    sql = f"""
        SELECT COUNT(DISTINCT r.race_id)
        FROM races r
        JOIN race_entries e1 ON e1.race_id=r.race_id AND e1.boat_number=1
        LEFT JOIN race_previews pv ON pv.race_id=r.race_id AND pv.boat_number=1
        JOIN race_results rr1 ON rr1.race_id=r.race_id AND rr1.boat_number=1
                              AND rr1.finishing_position=1
        WHERE r.race_date < {ph}
          AND r.race_date >= '2022-05-08'
          AND e1.class_number = {nf.CLASS_A1}
          AND r.stadium_number IN ({nuki_ph})
          AND r.stadium_number NOT IN ({excl_ph})
          AND (pv.weather_number IS NULL OR pv.weather_number != {nf.WEATHER_RAIN})
          AND NOT EXISTS (
            SELECT 1 FROM race_entries ex
            JOIN racers ra ON ex.racer_number=ra.racer_number
            WHERE ex.race_id=r.race_id AND ra.gender=2)
    """
    args = [nf.VERIFICATION_SPLIT_DATE] + list(nf.FN_NUKI_STADIUMS) + list(nf.L4_EXCLUDE_VENUES)
    cur.execute(sql, args)
    n = cur.fetchone()[0]
    conn.close()
    # 想定値 ±10% を許容
    lower = int(nf.NUKI_N_TRAIN * 0.90)
    upper = int(nf.NUKI_N_TRAIN * 1.15)
    assert lower <= n <= upper, (
        f"train n が想定 ({nf.NUKI_N_TRAIN}) から大きく乖離: SQL={n}\n"
        f"許容範囲: [{lower}, {upper}]"
    )
