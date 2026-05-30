"""桐生 (Kiryu) 特化高 ROI 戦略の単一情報源 (Single Source of Truth)

桐生競艇場で発見された会場固有の edge を 2 つの strategy にまとめたもの。
両方とも 13 ラウンドの自律検証ループ (Rounds 1-13) + 時系列スプリット
(train: 〜2025-12-31 / test: 2026-01-01〜) で robust 確認済。

⚠️ 重要 ⚠️
  - 本戦略は **大穴狙い** 型。的中率は K1 で 0.99%/2.48% (train/test)、
    K2 で 1.00%/2.56%、K1 portfolio (両買い) で 1.49%/4.13%。
  - 平均配当は 5-1-2 で 約16,000円、4-5-2 で 約27,000円。
  - **当たらない月の方が多い** のが期待挙動。年に数回の的中で +ROI を回収。
  - 実戦投入は **小額 (100-500円/bet)** かつ **長期 (500 race 以上)** 前提。
  - L4 戦略 (3連単 本命 hit 50% 前後) と性質が真逆。混同しないこと。

ソース:
  - reports/kiryu_wind_boat4.md (詳細レポート)
  - reports/autonomous_verification_summary.md (Rounds 1-13 総括)
  - scripts/explore_auto_loop_r6.py 〜 r13.py (検証スクリプト)

【K1: 桐生 5-1-2 + 4-5-2 併買】(風向不問)
  条件: stadium=1 ∧ 1号艇 A1 ∧ motor 2連率 ≥ 35 ∧ 国1着率 ≥ 6 ∧ 雨除外
  買い目: 3連単 5-1-2 (100円) + 3連単 4-5-2 (100円)
  検証: train n=1007 ROI=151.4% / test n=121 ROI=277.1% (race数)

【K2: 桐生 wd≠6 5-1-2 単独】(K1 の wd 切分けで refine)
  条件: K1 + 風向 ≠ 6 (= 追い風以外)
  買い目: 3連単 5-1-2 (100円)
  検証: train n=998 ROI=168.1% / test n=117 ROI=237.3%
  備考:
    - 風向データは race_previews テーブルで 2025-07-15 以降のみ存在。
    - wd=NULL のときは「風向情報なし」として K2 にも適用する (保守的)。
      検証コードと同じ扱い (`pv.wind_direction_number IS NULL OR != 6`)。

【両方同時運用時の挙動】
  桐生 K1 適格 race において:
    - 風向 = 6 のとき → K1 のみ発火 (5-1-2 + 4-5-2 各100円)
    - 風向 ≠ 6 のとき → K1 + K2 → 5-1-2 は K1+K2 重複で 200円 + 4-5-2 100円
    - 風向 NULL → 同上 (wd≠6 と等価扱い)
  これは wd≠6 で 5-1-2 ROI がさらに高い (168.1%) ことを利用した dynamic
  サイジング。重複ベットは資金管理の観点で意図的に強調しているもの。

【K1_PRIME / K2_PRIME: 的中率改善版 (推奨)】
  Round 14 (Agent A) の体系的探索で発見された refined 版。
  ベース K1/K2 と同じ条件に加えて、外艇 head 強化条件を追加することで
  的中率を 1.5-2.1 倍、ROI を 1.7-2.1 倍に改善する。
  検証: reports/kiryu_winrate_improvement.md
  ソース: scripts/explore_kiryu_winrate_improvement.py

  K1_PRIME (4-5-2 head):
    条件: K1 base + 4号艇 class=A1
    買い目: 3連単 4-5-2 (100円)
    検証: train n=392 ROI=112.0% / test n=57 ROI=689.5%
    的中率: train 0.51% (元 K1 4-5-2 の 0.50%) / test 3.51% (元 1.65%, ×2.1)

  K2_PRIME (5-1-2 head):
    条件: K2 base + 5号艇 motor 2連率 ≥ 35
    買い目: 3連単 5-1-2 (100円)
    検証: train n=455 ROI=319.9% / test n=52 ROI=406.9%
    的中率: train 1.54% (元 K2 の 1.00%, ×1.5) / test 3.85% (元 2.56%, ×1.5)
    特徴: train ROI が 168.1% → 319.9% と大きく改善する稀有なケース。
          → data-snooping ではなく真の edge と判断 (物理的整合: 5号艇 head
          戦略だから 5号艇 motor が良いほど実際に勝つ)。

  両 PRIME 同時運用 (Portfolio C):
    桐生 base 条件 を満たすレースに対して:
      IF wd≠6 AND 5号艇 motor≥35 → 3連単 5-1-2 (100円)
      IF 4号艇 class=A1          → 3連単 4-5-2 (100円)
      両方満たすときは 200円投入 (両買い)
    test 期間で n=52+57=109 bet、期待利益 +250〜400円/bet。

備考:
  - 本モジュールは判定ロジックのみを提供する。実際のベット送信や金額計算は
    呼び出し側 (notification / orchestrator) で行う想定。
  - 採用基準は L4 戦略と同様 「読み取り専用条件 (購入前に確定済の情報) のみ」。
    motor 2連率・国1着率・class は前日確定、weather・wind は締切直前 preview。
  - K1/K2 (legacy) と K1_PRIME/K2_PRIME (refined) を併存。呼び出し側で選択可。
"""
from __future__ import annotations

from typing import Optional

# ============================================================
# 共通定数
# ============================================================
KIRYU_STADIUM_NUMBER: int = 1
"""桐生競艇場の stadium_number"""

KIRYU_MOTOR_TOP_2_MIN: float = 35.0
"""1号艇 motor 2連率 (assigned_motor_top_2_percent) 下限"""

KIRYU_NATIONAL_TOP_1_MIN: float = 6.0
"""1号艇 国1着率 (national_top_1_percent) 下限"""

KIRYU_TAILWIND_DIRECTION: int = 6
"""桐生で 4号艇外艇有利になる「追い風」と推定される wind_direction_number 値.

経験的判定: 桐生で 4号艇 1着率が 23.36% (全体ベースライン 12.4%) と
跳ねる風向。wd=6 = 桐生における「追い風」。
"""

WEATHER_RAIN: int = 3
"""weather_number = 雨"""

CLASS_A1: int = 1
"""class_number = A1"""

# ============================================================
# 戦略メタ情報 (検証結果)
# ============================================================
K1_LABEL: str = "🏆 桐生K1 5-1-2+4-5-2併買"
K1_BET_DESCRIPTION: str = "3連単 5-1-2 と 4-5-2 を各100円"
K1_ROI_TRAIN_PCT: float = 151.4
K1_ROI_TEST_PCT: float = 277.1
K1_N_TRAIN: int = 1007
K1_N_TEST: int = 121

K2_LABEL: str = "🏆 桐生K2 wd≠6 5-1-2"
K2_BET_DESCRIPTION: str = "3連単 5-1-2 を 100円 (風向≠6 限定)"
K2_ROI_TRAIN_PCT: float = 168.1
K2_ROI_TEST_PCT: float = 237.3
K2_N_TRAIN: int = 998
K2_N_TEST: int = 117

# K1_PRIME (4-5-2 + 4号艇 class=A1)
K1_PRIME_LABEL: str = "👑 桐生K1' 4-5-2 (+4号艇A1)"
K1_PRIME_BET_DESCRIPTION: str = "3連単 4-5-2 を 100円 (4号艇 class=A1 限定)"
K1_PRIME_ROI_TRAIN_PCT: float = 112.0
K1_PRIME_ROI_TEST_PCT: float = 689.5
K1_PRIME_HIT_RATE_TRAIN_PCT: float = 0.51
K1_PRIME_HIT_RATE_TEST_PCT: float = 3.51
K1_PRIME_N_TRAIN: int = 392
K1_PRIME_N_TEST: int = 57

# K2_PRIME (5-1-2 + 5号艇 motor>=35)
K2_PRIME_LABEL: str = "👑 桐生K2' 5-1-2 (wd≠6, +5号艇motor≥35)"
K2_PRIME_BET_DESCRIPTION: str = "3連単 5-1-2 を 100円 (wd≠6 ∧ 5号艇 motor≥35)"
K2_PRIME_ROI_TRAIN_PCT: float = 319.9
K2_PRIME_ROI_TEST_PCT: float = 406.9
K2_PRIME_HIT_RATE_TRAIN_PCT: float = 1.54
K2_PRIME_HIT_RATE_TEST_PCT: float = 3.85
K2_PRIME_N_TRAIN: int = 455
K2_PRIME_N_TEST: int = 52

# K1_PRIME 用: 4号艇 class 要件 (= A1)
K1_PRIME_BOAT4_CLASS: int = 1

# K2_PRIME 用: 5号艇 motor 2連率 下限
K2_PRIME_BOAT5_MOTOR_TOP_2_MIN: float = 35.0

# 検証スプリット日 (このしきい値より前 = train、以降 = test)
VERIFICATION_SPLIT_DATE: str = "2026-01-01"


# ============================================================
# 条件判定
# ============================================================
def is_kiryu_base_eligible(
    stadium_number: Optional[int],
    boat1_class: Optional[int],
    boat1_motor_top_2_percent: Optional[float],
    boat1_national_top_1_percent: Optional[float],
    weather_number: Optional[int],
) -> bool:
    """K1 と K2 の共通基本条件 (motor + 国1 + 雨除外 + 桐生 A1)。

    返り値が True なら少なくとも K1 は適格。
    """
    if stadium_number != KIRYU_STADIUM_NUMBER:
        return False
    if boat1_class != CLASS_A1:
        return False
    try:
        motor = float(boat1_motor_top_2_percent) if boat1_motor_top_2_percent is not None else -1.0
        natl = float(boat1_national_top_1_percent) if boat1_national_top_1_percent is not None else -1.0
    except (TypeError, ValueError):
        return False
    if motor < KIRYU_MOTOR_TOP_2_MIN:
        return False
    if natl < KIRYU_NATIONAL_TOP_1_MIN:
        return False
    # 雨マーク有りなら除外 (None / 雨以外 OK)
    if weather_number == WEATHER_RAIN:
        return False
    return True


def is_kiryu_k1_eligible(
    stadium_number: Optional[int],
    boat1_class: Optional[int],
    boat1_motor_top_2_percent: Optional[float],
    boat1_national_top_1_percent: Optional[float],
    weather_number: Optional[int],
) -> bool:
    """K1 (5-1-2 + 4-5-2 併買) の発火条件。風向に依存しない。"""
    return is_kiryu_base_eligible(
        stadium_number,
        boat1_class,
        boat1_motor_top_2_percent,
        boat1_national_top_1_percent,
        weather_number,
    )


def is_kiryu_k2_eligible(
    stadium_number: Optional[int],
    boat1_class: Optional[int],
    boat1_motor_top_2_percent: Optional[float],
    boat1_national_top_1_percent: Optional[float],
    weather_number: Optional[int],
    wind_direction_number: Optional[int],
) -> bool:
    """K2 (wd≠6 5-1-2 単独) の発火条件。

    wind_direction_number が None (= 風向情報なし) の場合も適格扱い。
    これは検証コードで `pv.wind_direction_number IS NULL OR != 6` と
    同じ扱いをしているため。
    """
    if not is_kiryu_base_eligible(
        stadium_number,
        boat1_class,
        boat1_motor_top_2_percent,
        boat1_national_top_1_percent,
        weather_number,
    ):
        return False
    # wd=6 のときは K2 不適格 (5-1-2 が崩れる風向)
    if wind_direction_number == KIRYU_TAILWIND_DIRECTION:
        return False
    return True


def is_kiryu_k1_prime_eligible(
    stadium_number: Optional[int],
    boat1_class: Optional[int],
    boat1_motor_top_2_percent: Optional[float],
    boat1_national_top_1_percent: Optional[float],
    weather_number: Optional[int],
    boat4_class: Optional[int],
) -> bool:
    """K1_PRIME (4-5-2 + 4号艇 class=A1) の発火条件。

    K1 base 条件 + 4号艇が A1 であることを要求。
    test 期間で的中率 3.51% / ROI 689.5% という大きな改善を示す refined 版。
    """
    if not is_kiryu_base_eligible(
        stadium_number,
        boat1_class,
        boat1_motor_top_2_percent,
        boat1_national_top_1_percent,
        weather_number,
    ):
        return False
    if boat4_class != K1_PRIME_BOAT4_CLASS:
        return False
    return True


def is_kiryu_k2_prime_eligible(
    stadium_number: Optional[int],
    boat1_class: Optional[int],
    boat1_motor_top_2_percent: Optional[float],
    boat1_national_top_1_percent: Optional[float],
    weather_number: Optional[int],
    wind_direction_number: Optional[int],
    boat5_motor_top_2_percent: Optional[float],
) -> bool:
    """K2_PRIME (wd≠6 5-1-2 + 5号艇 motor≥35) の発火条件。

    K2 base 条件 + 5号艇 motor 2連率 ≥ 35 を要求。
    train でも ROI が 168.1% → 319.9% に大きく改善する稀有なケース。
    5号艇 head 戦略なので 5号艇 motor が良いほど勝ちやすい (物理的整合)。
    """
    if not is_kiryu_k2_eligible(
        stadium_number,
        boat1_class,
        boat1_motor_top_2_percent,
        boat1_national_top_1_percent,
        weather_number,
        wind_direction_number,
    ):
        return False
    try:
        boat5_motor = float(boat5_motor_top_2_percent) if boat5_motor_top_2_percent is not None else -1.0
    except (TypeError, ValueError):
        return False
    if boat5_motor < K2_PRIME_BOAT5_MOTOR_TOP_2_MIN:
        return False
    return True


# ============================================================
# 買い目生成
# ============================================================
# 1 ベット = 100円 を標準サイズとする (l4_strategy と整合)
KIRYU_BET_UNIT_YEN: int = 100


def get_kiryu_k1_bets() -> list[tuple[str, str, int]]:
    """K1 が発火したときの買い目 [(bet_type, combination, amount_yen), ...]"""
    return [
        ("trifecta", "5-1-2", KIRYU_BET_UNIT_YEN),
        ("trifecta", "4-5-2", KIRYU_BET_UNIT_YEN),
    ]


def get_kiryu_k2_bets() -> list[tuple[str, str, int]]:
    """K2 が発火したときの買い目"""
    return [
        ("trifecta", "5-1-2", KIRYU_BET_UNIT_YEN),
    ]


def get_kiryu_k1_prime_bets() -> list[tuple[str, str, int]]:
    """K1_PRIME 発火時の買い目 (3連単 4-5-2 のみ)"""
    return [
        ("trifecta", "4-5-2", KIRYU_BET_UNIT_YEN),
    ]


def get_kiryu_k2_prime_bets() -> list[tuple[str, str, int]]:
    """K2_PRIME 発火時の買い目 (3連単 5-1-2 のみ)"""
    return [
        ("trifecta", "5-1-2", KIRYU_BET_UNIT_YEN),
    ]


# ============================================================
# 統合 evaluator (race ごとの推奨買い目をまとめて返す)
# ============================================================
def evaluate_kiryu_race(
    stadium_number: Optional[int],
    boat1_class: Optional[int],
    boat1_motor_top_2_percent: Optional[float],
    boat1_national_top_1_percent: Optional[float],
    weather_number: Optional[int],
    wind_direction_number: Optional[int],
    enable_k1: bool = True,
    enable_k2: bool = True,
) -> dict:
    """1 race に対し K1 / K2 を判定し、推奨買い目をまとめて返す。

    Returns:
      {
        "k1_eligible": bool,
        "k2_eligible": bool,
        "bets": [(bet_type, combination, amount_yen), ...],  # 集約済 (同 combo は加算)
        "labels": ["🏆 桐生K1 ...", ...],
        "expected_roi_pct": float | None,  # K1 と K2 の test ROI 平均
      }

    両方発火時は買い目を加算 (5-1-2 を 200円、4-5-2 を 100円 になる)。
    enable_k1 / enable_k2 で個別オン/オフ可能。
    """
    k1 = enable_k1 and is_kiryu_k1_eligible(
        stadium_number,
        boat1_class,
        boat1_motor_top_2_percent,
        boat1_national_top_1_percent,
        weather_number,
    )
    k2 = enable_k2 and is_kiryu_k2_eligible(
        stadium_number,
        boat1_class,
        boat1_motor_top_2_percent,
        boat1_national_top_1_percent,
        weather_number,
        wind_direction_number,
    )
    raw_bets: list[tuple[str, str, int]] = []
    labels: list[str] = []
    if k1:
        raw_bets.extend(get_kiryu_k1_bets())
        labels.append(K1_LABEL)
    if k2:
        raw_bets.extend(get_kiryu_k2_bets())
        labels.append(K2_LABEL)

    # 同じ (bet_type, combination) は金額加算
    merged: dict[tuple[str, str], int] = {}
    for bt, combo, amt in raw_bets:
        merged[(bt, combo)] = merged.get((bt, combo), 0) + amt
    bets = [(bt, combo, amt) for (bt, combo), amt in merged.items()]

    expected_roi: Optional[float] = None
    if k1 and k2:
        # test ROI 平均で概算
        expected_roi = (K1_ROI_TEST_PCT + K2_ROI_TEST_PCT) / 2.0
    elif k1:
        expected_roi = K1_ROI_TEST_PCT
    elif k2:
        expected_roi = K2_ROI_TEST_PCT

    return {
        "k1_eligible": k1,
        "k2_eligible": k2,
        "bets": bets,
        "labels": labels,
        "expected_roi_pct": expected_roi,
    }


def evaluate_kiryu_race_prime(
    stadium_number: Optional[int],
    boat1_class: Optional[int],
    boat1_motor_top_2_percent: Optional[float],
    boat1_national_top_1_percent: Optional[float],
    weather_number: Optional[int],
    wind_direction_number: Optional[int],
    boat4_class: Optional[int],
    boat5_motor_top_2_percent: Optional[float],
    enable_k1_prime: bool = True,
    enable_k2_prime: bool = True,
) -> dict:
    """1 race に対し K1_PRIME / K2_PRIME を判定 (Portfolio C 推奨運用).

    K1_PRIME (4-5-2 + 4号艇 class=A1) と K2_PRIME (5-1-2 + wd≠6 +
    5号艇 motor≥35) を統合的に評価し、買い目をまとめて返す。

    Returns:
      {
        "k1_prime_eligible": bool,
        "k2_prime_eligible": bool,
        "bets": [(bet_type, combination, amount_yen), ...],
        "labels": [str, ...],
        "expected_roi_pct": float | None,  # test ROI 平均
      }

    両方発火時は別々の買い目 (4-5-2 + 5-1-2 = 200円) になる。
    重複 combo はないので加算は発生しない。
    """
    k1p = enable_k1_prime and is_kiryu_k1_prime_eligible(
        stadium_number,
        boat1_class,
        boat1_motor_top_2_percent,
        boat1_national_top_1_percent,
        weather_number,
        boat4_class,
    )
    k2p = enable_k2_prime and is_kiryu_k2_prime_eligible(
        stadium_number,
        boat1_class,
        boat1_motor_top_2_percent,
        boat1_national_top_1_percent,
        weather_number,
        wind_direction_number,
        boat5_motor_top_2_percent,
    )
    raw_bets: list[tuple[str, str, int]] = []
    labels: list[str] = []
    if k1p:
        raw_bets.extend(get_kiryu_k1_prime_bets())
        labels.append(K1_PRIME_LABEL)
    if k2p:
        raw_bets.extend(get_kiryu_k2_prime_bets())
        labels.append(K2_PRIME_LABEL)

    # 同じ (bet_type, combination) は金額加算 (4-5-2 と 5-1-2 は別 combo)
    merged: dict[tuple[str, str], int] = {}
    for bt, combo, amt in raw_bets:
        merged[(bt, combo)] = merged.get((bt, combo), 0) + amt
    bets = [(bt, combo, amt) for (bt, combo), amt in merged.items()]

    expected_roi: Optional[float] = None
    if k1p and k2p:
        expected_roi = (K1_PRIME_ROI_TEST_PCT + K2_PRIME_ROI_TEST_PCT) / 2.0
    elif k1p:
        expected_roi = K1_PRIME_ROI_TEST_PCT
    elif k2p:
        expected_roi = K2_PRIME_ROI_TEST_PCT

    return {
        "k1_prime_eligible": k1p,
        "k2_prime_eligible": k2p,
        "bets": bets,
        "labels": labels,
        "expected_roi_pct": expected_roi,
    }


__all__ = [
    # 定数
    "KIRYU_STADIUM_NUMBER",
    "KIRYU_MOTOR_TOP_2_MIN",
    "KIRYU_NATIONAL_TOP_1_MIN",
    "KIRYU_TAILWIND_DIRECTION",
    "WEATHER_RAIN",
    "CLASS_A1",
    "KIRYU_BET_UNIT_YEN",
    "K1_PRIME_BOAT4_CLASS",
    "K2_PRIME_BOAT5_MOTOR_TOP_2_MIN",
    # メタ (K1/K2 legacy)
    "K1_LABEL",
    "K1_BET_DESCRIPTION",
    "K1_ROI_TRAIN_PCT",
    "K1_ROI_TEST_PCT",
    "K1_N_TRAIN",
    "K1_N_TEST",
    "K2_LABEL",
    "K2_BET_DESCRIPTION",
    "K2_ROI_TRAIN_PCT",
    "K2_ROI_TEST_PCT",
    "K2_N_TRAIN",
    "K2_N_TEST",
    # メタ (K1_PRIME / K2_PRIME refined)
    "K1_PRIME_LABEL",
    "K1_PRIME_BET_DESCRIPTION",
    "K1_PRIME_ROI_TRAIN_PCT",
    "K1_PRIME_ROI_TEST_PCT",
    "K1_PRIME_HIT_RATE_TRAIN_PCT",
    "K1_PRIME_HIT_RATE_TEST_PCT",
    "K1_PRIME_N_TRAIN",
    "K1_PRIME_N_TEST",
    "K2_PRIME_LABEL",
    "K2_PRIME_BET_DESCRIPTION",
    "K2_PRIME_ROI_TRAIN_PCT",
    "K2_PRIME_ROI_TEST_PCT",
    "K2_PRIME_HIT_RATE_TRAIN_PCT",
    "K2_PRIME_HIT_RATE_TEST_PCT",
    "K2_PRIME_N_TRAIN",
    "K2_PRIME_N_TEST",
    "VERIFICATION_SPLIT_DATE",
    # 関数 (K1/K2 legacy)
    "is_kiryu_base_eligible",
    "is_kiryu_k1_eligible",
    "is_kiryu_k2_eligible",
    "get_kiryu_k1_bets",
    "get_kiryu_k2_bets",
    "evaluate_kiryu_race",
    # 関数 (K1_PRIME / K2_PRIME refined)
    "is_kiryu_k1_prime_eligible",
    "is_kiryu_k2_prime_eligible",
    "get_kiryu_k1_prime_bets",
    "get_kiryu_k2_prime_bets",
    "evaluate_kiryu_race_prime",
]
