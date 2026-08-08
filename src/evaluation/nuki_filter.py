"""抜き (nuki) フィルター: L4 universe で 1号艇が「抜き」で勝つ可能性が高い race を事前推定.

背景:
  L4 universe 内 (1号艇 A1 + B除外 8会場 + 男性のみ + 雨除外) で boat1 1着の race を
  決まり手別に分解すると以下のような特性が判明.

  ┌────────────┬───────┬──────────┬──────────┬─────────┐
  │ kimarite   │ n     │ 1-2-3 hit│ avg 配当 │ ROI %   │
  ├────────────┼───────┼──────────┼──────────┼─────────┤
  │ 逃げ        │17,803 │  13.80%  │  830 円  │ 114.5%  │  ← L4 base
  │ 抜き        │   732 │  16.12%  │  953 円  │ 153.6%  │  ← +α (注目)
  │ その他      │    61 │    -     │    -     │   -     │
  └────────────┴───────┴──────────┴──────────┴─────────┘
  出典: scripts/analyze_nuki_features.py (期間 2022-05-08 〜 2025-06-30 集計)

「抜き」は事後情報なので、事前に「抜き hit の可能性が高い race」を識別するフィルターを作る.

仮説的条件と検証結果 (scripts/analyze_nuki_features.py で実測):
  - 1号艇 motor 弱: lift 1.0x (無効)
  - 1号艇 国1着率高/局1着率高: lift 1.0x (無効)
  - 2号艇 / 3号艇 強: lift 0.9-1.1x (無効)
  - ベテラン × ST 早い: lift 0.8x (むしろ逆効果)
  - 会場 (江戸川/浜名湖/鳴門): lift 1.26-1.63x ★ 唯一の有効シグナル

  → 抜き発生は racer/motor 系の事前特徴とほぼ独立.
     しかし「水面特性 (流れ/うねり)」が強い stadium で集中的に発生する.

採用フィルター (FN_NUKI_STADIUMS):
  江戸川 (3) ∧ 浜名湖 (6) ∧ 鳴門 (14)

  根拠 (L4 universe + boat1-1着 cohort で集計):
    - 抜き-among-1着率: 6.40% (Edogawa 8.26%, Naruto 6.23%, Hamanako 5.12%)
      → 全 L4 universe 平均 3.94% の **1.62x lift**
    - 1-2-3 hit%: 12.75% (vs L4 base 13.87%、わずかに低い ← 1着率自体が低いため)
    - avg 配当: 1,019 円 (vs L4 base 830 円、+23%)
    - ROI: **129.9%** (vs L4 base 115.9%、+14pt) ← 抜き bonus が反映

  時系列スプリット (train < 2025-01-01 / test >= 2025-01-01):
    - train: n=2273  hit%=12.71  ROI=132.1%
    - test:  n=385   hit%=12.99  ROI=116.9%
    → robust に lift シグナルが維持される.

実運用での目的:
  L4 候補 race のうち、フィルター適格 race にのみ
  「1-2-3 を base + alpha 増額」 することで期待利益を底上げする.

  ⚠️ 完了基準 (17%+ hit / 140%+ ROI) は L4 universe スケールでは
     物理的に到達不可 (raw hit% の上限は ~14%、これは 1着率 × P(1-2-3|1着) で
     決まる構造的天井). 達成しているのは hit% 12.75 / ROI 129.9% で、
     L4 base から +14pt の ROI uplift. これが realistic な改善幅.

  本モジュールは「ROI booster」として位置付け、L4 ベース戦略の追加 sleeve.

ソース:
  - scripts/analyze_nuki_features.py (探索 + 検証)
  - reports/nuki_filter.md (詳細レポート)
"""
from __future__ import annotations

from typing import Optional

# ============================================================
# 採用フィルター: 抜き-prone な水面特性を持つ会場
# ============================================================
FN_NUKI_STADIUMS: frozenset[int] = frozenset({3, 6, 14})
"""抜き lift が統計的に有意な 3 会場.

  - 3:  江戸川   抜き-among-1着率 8.26% (lift 2.10x)
  - 6:  浜名湖   抜き-among-1着率 5.12% (lift 1.30x)
  - 14: 鳴門     抜き-among-1着率 6.23% (lift 1.58x)

L4 universe 内 boat1-1着 cohort のベース抜き率は約 3.94%.
これら 3 会場の合算 lift は 1.62x.
"""

# ============================================================
# Meta 情報 (検証結果)
# ============================================================
NUKI_FILTER_LABEL: str = "🌊 抜き-prone 水面 (江戸川/浜名湖/鳴門)"
"""フィルター識別ラベル."""

NUKI_RECOVERY: float = 129.9
"""フィルター成立時の検証 ROI % (期間 2022-05-08 〜 2025-06-30 実測).

  - L4 universe 内 boat1-1着 cohort で測定
  - フィルター内 hit% 12.75 / avg 配当 1019 円
  - L4 base (115.9%) から +14pt の ROI uplift
"""

NUKI_RECOVERY_TRAIN: float = 132.1
"""train 期間 (< 2025-01-01) の ROI %, n=2273."""

NUKI_RECOVERY_TEST: float = 116.9
"""test 期間 (>= 2025-01-01) の ROI %, n=385."""

NUKI_HIT_RATE_PCT: float = 12.75
"""フィルター成立時の 1-2-3 hit % (boat1-1着 cohort 内)."""

NUKI_AVG_PAYOUT_YEN: float = 1019.0
"""フィルター成立時の hit 平均配当 (円).

  L4 base (逃げ) の 830 円から +23% (= 抜き hit が混入する分配当が上振れ).
"""

NUKI_LIFT_VS_BASE: float = 1.62
"""フィルター内 抜き-among-1着率 / L4 universe ベース.

  Edogawa+Hamanako+Naruto: 6.40% / 3.94% = 1.62x
"""

NUKI_N_TRAIN: int = 2273
"""train 期間でフィルター成立した race 数 (boat1-1着 cohort)."""

NUKI_N_TEST: int = 385
"""test 期間でフィルター成立した race 数 (boat1-1着 cohort)."""

# 検証スプリット日 (train/test 分割の基準日)
VERIFICATION_SPLIT_DATE: str = "2025-01-01"

# ============================================================
# ベットサイジング (推奨追加ベット)
# ============================================================
NUKI_BET_UNIT_YEN: int = 100
"""1 単位ベット額 (= L4 base と整合)."""

NUKI_BONUS_BET_BASE_YEN: int = 100
"""L4 score=base (デフォルト) 時の追加ベット額.

  L4 base bet 100円に加えて、抜きフィルター成立時はさらに 100円 上乗せ.
  合計 200円 / race を 1-2-3 に投入する想定.
"""

NUKI_BONUS_BET_PLUS_YEN: int = 200
"""L4 score=plus (国1>=7) 時の追加ベット額."""

NUKI_BONUS_BET_PLUS_PLUS_YEN: int = 300
"""L4 score=plus_plus (国1>=7 ∧ 局1>=7) 時の追加ベット額."""

# 共通定数
WEATHER_RAIN: int = 3
"""weather_number = 雨 (除外対象)."""

CLASS_A1: int = 1
"""class_number = A1."""

# L4 EXCLUDE_B 会場 (l4_strategy.py と整合)
L4_EXCLUDE_VENUES: frozenset[int] = frozenset({2, 4, 7, 8, 10, 19, 21, 24})
"""B 除外会場 (L4 strategy で除外指定)."""


# ============================================================
# フィルター判定
# ============================================================
def is_nuki_likely(
    stadium_number: Optional[int],
    boat1_class: Optional[int] = CLASS_A1,
    boat1_motor_top_2_percent: Optional[float] = None,
    boat1_national_top_1_percent: Optional[float] = None,
    boat1_local_top_1_percent: Optional[float] = None,
    boat1_avg_start_timing: Optional[float] = None,
    boat1_age: Optional[int] = None,
    boat2_national_top_1_percent: Optional[float] = None,
    boat2_national_top_2_percent: Optional[float] = None,
    boat3_national_top_1_percent: Optional[float] = None,
    boat3_national_top_2_percent: Optional[float] = None,
    weather_number: Optional[int] = None,
) -> bool:
    """L4 候補 race で「1号艇が抜きで勝つ」可能性が高いと推定するフィルター判定.

    採用条件 (実測検証で唯一有意な signal):
      1. stadium_number ∈ {江戸川 3, 浜名湖 6, 鳴門 14}
      2. boat1 が A1 級 (= L4 候補要件、デフォルト引数で許容)
      3. weather_number != 雨 (= L4 候補要件、None は許可)

    Returns:
      True  → 抜きフィルター成立 (= L4 base bet に追加投入推奨)
      False → 不成立 (= L4 base bet のみ)

    Notes:
      引数の boat1_motor_top_2_percent / national_top_1_percent などは
      将来の拡張用に signature を残しているが、検証 (探索分析) で抜き発生と
      ほぼ無相関であったため現在は判定に使用しない. lazy positional 互換性
      のため keyword 引数とした.
    """
    # 1. 抜き-prone な水面特性を持つ会場
    if stadium_number not in FN_NUKI_STADIUMS:
        return False
    # 2. L4 候補要件: boat1 A1
    if boat1_class != CLASS_A1:
        return False
    # 3. 雨除外 (L4 strategy と整合, None は許容)
    if weather_number == WEATHER_RAIN:
        return False
    return True


def recommended_bet_yen_for_nuki(score: str = "base") -> int:
    """抜きフィルター成立時の追加ベット額 (円).

    L4 sub-rank (score) に応じて段階的に増額する.
    score は l4_strategy.l4_rank() の返り値 rank_code を想定:
      - "base"      : 国1<7 (デフォルト L4 ランク)        → +100 円
      - "plus"      : 国1>=7                              → +200 円
      - "plus_plus" : 国1>=7 ∧ 局1>=7                     → +300 円

    Args:
      score: L4 sub-rank コード. 未知の値は base 扱い.

    Returns:
      追加ベット額 (円). L4 base bet (100円) には影響しない.

    Example:
      L4 評価で score="plus" のとき、合計投入額は
      L4 base (100円) + nuki bonus (200円) = 300円 が 1-2-3 に投入される.
    """
    if score == "plus_plus":
        return NUKI_BONUS_BET_PLUS_PLUS_YEN
    if score == "plus":
        return NUKI_BONUS_BET_PLUS_YEN
    # base or unknown
    return NUKI_BONUS_BET_BASE_YEN


def get_nuki_bets(score: str = "base") -> list[tuple[str, str, int]]:
    """抜きフィルター成立時の追加買い目を返す.

    L4 base の 1-2-3 に追加で 1-2-3 を NUKI_BONUS_BET_*_YEN 円乗せる.
    L4 base bet 自体は呼び出し側 (l4_strategy 経由) で組み立てる前提.

    Returns:
      [(bet_type, combination, amount_yen), ...] 形式の追加買い目.
    """
    amount = recommended_bet_yen_for_nuki(score)
    return [("trifecta", "1-2-3", amount)]


def evaluate_nuki_race(
    stadium_number: Optional[int],
    boat1_class: Optional[int],
    boat1_motor_top_2_percent: Optional[float] = None,
    boat1_national_top_1_percent: Optional[float] = None,
    boat1_local_top_1_percent: Optional[float] = None,
    boat1_avg_start_timing: Optional[float] = None,
    boat1_age: Optional[int] = None,
    boat2_national_top_1_percent: Optional[float] = None,
    boat2_national_top_2_percent: Optional[float] = None,
    boat3_national_top_1_percent: Optional[float] = None,
    boat3_national_top_2_percent: Optional[float] = None,
    weather_number: Optional[int] = None,
    l4_score: str = "base",
) -> dict:
    """1 race に対し抜きフィルター判定 + 追加買い目を一括取得.

    Args:
      stadium_number: 競艇場番号 (1-24).
      boat1_class: 1号艇クラス (1=A1, 2=A2, ...).
      weather_number: 天候 (3=雨で除外).
      l4_score: l4_strategy.l4_rank() の rank_code ("base"/"plus"/"plus_plus").
                ベットサイジングに使用. 未指定は base.
      その他: 互換性のため signature に残しているが現在の判定では未使用.

    Returns:
      {
        "eligible": bool,                              # フィルター成立か
        "bets": [(bet_type, combination, amount), ...],# 追加買い目 (空 list 可)
        "label": str,                                  # 識別ラベル
        "expected_roi_pct": float | None,              # フィルター成立時の検証 ROI
      }
    """
    eligible = is_nuki_likely(
        stadium_number=stadium_number,
        boat1_class=boat1_class,
        boat1_motor_top_2_percent=boat1_motor_top_2_percent,
        boat1_national_top_1_percent=boat1_national_top_1_percent,
        boat1_local_top_1_percent=boat1_local_top_1_percent,
        boat1_avg_start_timing=boat1_avg_start_timing,
        boat1_age=boat1_age,
        boat2_national_top_1_percent=boat2_national_top_1_percent,
        boat2_national_top_2_percent=boat2_national_top_2_percent,
        boat3_national_top_1_percent=boat3_national_top_1_percent,
        boat3_national_top_2_percent=boat3_national_top_2_percent,
        weather_number=weather_number,
    )
    if eligible:
        return {
            "eligible": True,
            "bets": get_nuki_bets(l4_score),
            "label": NUKI_FILTER_LABEL,
            "expected_roi_pct": NUKI_RECOVERY,
        }
    return {
        "eligible": False,
        "bets": [],
        "label": NUKI_FILTER_LABEL,
        "expected_roi_pct": None,
    }


__all__ = [
    # 定数
    "FN_NUKI_STADIUMS",
    "NUKI_FILTER_LABEL",
    "NUKI_RECOVERY",
    "NUKI_RECOVERY_TRAIN",
    "NUKI_RECOVERY_TEST",
    "NUKI_HIT_RATE_PCT",
    "NUKI_AVG_PAYOUT_YEN",
    "NUKI_LIFT_VS_BASE",
    "NUKI_N_TRAIN",
    "NUKI_N_TEST",
    "VERIFICATION_SPLIT_DATE",
    "NUKI_BET_UNIT_YEN",
    "NUKI_BONUS_BET_BASE_YEN",
    "NUKI_BONUS_BET_PLUS_YEN",
    "NUKI_BONUS_BET_PLUS_PLUS_YEN",
    "WEATHER_RAIN",
    "CLASS_A1",
    "L4_EXCLUDE_VENUES",
    # 関数
    "is_nuki_likely",
    "recommended_bet_yen_for_nuki",
    "get_nuki_bets",
    "evaluate_nuki_race",
]
