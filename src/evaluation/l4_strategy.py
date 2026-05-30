"""L4 戦略の単一情報源 (Single Source of Truth)

L4 = 三連単本命 500-1000円 + B除外会場 + 1号艇A1 (一部 A2 派生) で
三連単 1-2-3 を 1点100円買う戦略。

このモジュールに以下の定義を集約:
  - B 除外会場の集合 EXCLUDE_VENUES
  - グレード/クラス別の検証回収率 GRADE_CLASS_RULES
  - 1号艇選手の国1%/局1% によるサブランク (L4 / L4+ / L4++)

監査指摘の DRY 違反 (app.py と send_l4_alerts.py の二重定義) を解消するため、
今後の参照はこのモジュールを単一情報源とする。
"""
from __future__ import annotations

from typing import Optional

# ============================================================
# B 除外会場 (10ヶ月実測で回収率が振るわなかった会場)
#   2 戸田, 4 平和島, 7 蒲郡, 8 常滑,
#   10 三国, 19 下関, 21 芦屋, 24 大村
# ============================================================
EXCLUDE_VENUES: set[int] = {2, 4, 7, 8, 10, 19, 21, 24}


# ============================================================
# グレード/クラス別の検証回収率 (10ヶ月実測)
# key: (grade, class)  value: dict
# ============================================================
GRADE_CLASS_RULES: dict[tuple[Optional[int], int], dict] = {
    (1, 1): {"level": "SG",       "label": "👑L4 SG×A1",     "recovery": 258.2, "n": 40,   "bet": "3連単 1-2-3"},
    (2, 1): {"level": "G1",       "label": "👑L4 G1×A1",     "recovery": 242.8, "n": 227,  "bet": "3連単 1-2-3"},
    (3, 1): {"level": "G2",       "label": "👑L4 G2×A1",     "recovery": 242.7, "n": 30,   "bet": "3連単 1-2-3"},
    (4, 1): {"level": "G3",       "label": "🎯L4 G3×A1",     "recovery": 149.2, "n": 195,  "bet": "3連単 1-2-3"},
    (5, 1): {"level": "general",  "label": "🎯L4 一般戦×A1",  "recovery": 147.7, "n": 1776, "bet": "3連単 1-2-3"},
    # A2 派生 (どのグレードでも共通検証値)
    (None, 2): {"level": "a2",    "label": "📈L4派生 A2",    "recovery": 134.0, "n": 1645, "bet": "3連単 1-2-3"},
}

# default (グレード不明 / cls=1)
L4_DEFAULT_A1 = {"level": "default", "label": "🎯L4 A1",
                 "recovery": 160.8, "n": 2210, "bet": "3連単 1-2-3"}


def lookup_rule(grade: Optional[int], cls: Optional[int]) -> Optional[dict]:
    """(grade, class) から L4 ルールを返す。該当なしなら None。"""
    if cls is None:
        return None
    if cls == 1:
        rule = GRADE_CLASS_RULES.get((grade, 1))
        if rule is not None:
            return dict(rule)
        return dict(L4_DEFAULT_A1)
    if cls == 2:
        return dict(GRADE_CLASS_RULES[(None, 2)])
    return None


# ============================================================
# F1 Prime 11-12R (ユーザー提案、 2026-05-30 追加)
#   条件: L4 G++ F1 (一般戦×1号艇A1×国1%≥7×2号モ2連率≥40)
#        AND race_number IN (11, 12)
#   買い目: 3連単 1-2-3 (L4 ベース、追加なし)
#
#   検証 ROI:
#     - F1 単体 (既存): 204.0% (n=1189, L4 帯 500-1000円)
#     - F1 Prime ROI: ローカル DB に odds_trifecta が 3 日分しか無いため、
#       T-5min snapshot に基づく厳密な L4 帯絞り版は本番 (Supabase)
#       環境での再検証が必要.
#     - 暫定: F1 単体 ROI 204.0% を継承 (Prime 絞りで test サンプル小)
#
#   詳細: reports/verify_f1_prime.md / scripts/verify_f1_prime.py
# ============================================================
F1_PRIME_RECOVERY: float = 204.0
"""F1 Prime 11-12R の検証 ROI % (暫定: F1 単体と同等扱い).

本番環境での再検証が完了したら更新する.
"""
F1_PRIME_RACE_NUMBERS: tuple[int, ...] = (11, 12)
"""F1 Prime に該当する race_number."""


# ============================================================
# L4 サブランク (1号艇選手の国1%/局1% で +EV 強化)
#   plus_plus: 国1%>=7.0 ∧ 局1%>=7.0  → 検証 190.3%
#   plus:      国1%>=7.0               → 検証 188.2%
#   base:      上記未満                 → グレード別検証値
# (out-of-sample バックテストで確認、TRAIN期で選手特性抽出 → TEST期で検証)
# ============================================================
RANK_PLUS_PLUS_RECOVERY = 190.3
RANK_PLUS_RECOVERY = 188.2
RANK_THRESHOLD_NATL = 7.0
RANK_THRESHOLD_LOCAL = 7.0


def l4_rank(natl_1: Optional[float], local_1: Optional[float]) -> tuple[str, str, str, Optional[float]]:
    """1号艇選手の国1%/局1% からサブランク判定。
    Returns:
      (rank_code, rank_label, rank_emoji, override_recovery)
      override_recovery が None なら base ルールの recovery を使う。
    """
    try:
        n = float(natl_1) if natl_1 is not None else 0.0
        l = float(local_1) if local_1 is not None else 0.0
    except (TypeError, ValueError):
        n = l = 0.0
    if n >= RANK_THRESHOLD_NATL and l >= RANK_THRESHOLD_LOCAL:
        return ("plus_plus", "L4++", "🥇", RANK_PLUS_PLUS_RECOVERY)
    if n >= RANK_THRESHOLD_NATL:
        return ("plus", "L4+", "🥈", RANK_PLUS_RECOVERY)
    return ("base", "L4", "⭐", None)


# ============================================================
# L4+1c80 (1コース1着率 80%以上 = 逃げ強い選手)
# 集計期間: 過去 6 ヶ月 (180 日)
# 最低サンプル: 20 戦以上
# 閾値: 0.80 (= 80%)
# 検証 ROI: 3連単 1-2-3 で 209-227% (n=218, 通常 L4 平均 190% より +20-30pt)
#   → 「オッズが範囲内かつ 1c80 該当」なら通常 L4 より資金多め推奨
# ============================================================
COURSE1_WINDOW_DAYS = 180
COURSE1_MIN_STARTS = 20
COURSE1_THRESHOLD = 0.80
L4_1C80_RECOVERY = 215.0  # 80%+ ゾーンの実測値 (209-227 の中央)


def is_1c80(course1_winrate: Optional[float], course1_starts: Optional[int]) -> bool:
    """1コース1着率が L4+1c80 ランクを満たすか"""
    if course1_winrate is None or course1_starts is None:
        return False
    if course1_starts < COURSE1_MIN_STARTS:
        return False
    return course1_winrate >= COURSE1_THRESHOLD


# ============================================================
# L4 PRO: ベテラン × スタート上手 × 展示好調 (検証 ROI 241.5%)
#   - 平均 ST < 0.16 (キャリアでスタート上手)
#   - 年齢 30-49 (ベテラン、衰え前)
#   - 展示 ST < 0.18 (当日も好調)
# 4年検証: n=247、ROI 241.5% (= 通常 L4 平均 193% から +49pt)
# = レースあたり +¥141 利益、通常 L4 +¥92 と比べて +53% アップ
# ============================================================
L4_PRO_AVG_ST_MAX = 0.16
L4_PRO_AGE_MIN = 30
L4_PRO_AGE_MAX = 49
L4_PRO_EX_ST_MAX = 0.18
L4_PRO_RECOVERY = 241.5


def is_l4_pro(avg_start_timing: Optional[float],
              age: Optional[int],
              start_timing_exhibition: Optional[float]) -> bool:
    """L4 PRO ランクを満たすか。
    確定判定: 平均ST + 年齢 + 展示ST 全部揃って閾値内ならフル PRO。
    展示 ST が NULL の場合は「朝予測 PRO 候補」とみなして展示ST 条件は緩和。
    """
    if avg_start_timing is None or age is None:
        return False
    try:
        ast = float(avg_start_timing)
        a = int(age)
    except (TypeError, ValueError):
        return False
    if ast >= L4_PRO_AVG_ST_MAX:
        return False
    if not (L4_PRO_AGE_MIN <= a <= L4_PRO_AGE_MAX):
        return False
    # 展示 ST が NULL なら 2 条件のみで「候補」、ある場合は閾値チェック
    if start_timing_exhibition is None:
        return True  # 朝予測候補 (展示前)
    try:
        ex = float(start_timing_exhibition)
    except (TypeError, ValueError):
        return True
    return ex < L4_PRO_EX_ST_MAX


def is_l4_payout_range(payout: Optional[float]) -> bool:
    """三連単本命の払戻が L4 のターゲット範囲 (500-1000円) 内か"""
    return payout is not None and 500 <= payout < 1000


def is_b_excluded(stadium_number: Optional[int]) -> bool:
    """B 除外会場かどうか (除外対象なら True)"""
    return stadium_number in EXCLUDE_VENUES
