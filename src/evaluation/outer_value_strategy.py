"""外枠本命 (head=6) 戦略の単一情報源 (Single Source of Truth)

俗に「1号艇本命」が過剰人気になる競艇では、モデルが最外艇 (6号艇) を
本命に推すレースは『一番無視される艇ほど旨い』= 枠バイアスの純粋なエッジになる。

戦略:
  トリガー = モデルの本命 (head = prob_first が最大の艇) == 6
            AND p1 (= その最大 prob_first) >= 0.35
  買い目  = 6号艇の単勝 (win) を 100円

検証 (reports/ev_head6_deepdive.md, 4年バックテスト ev_picks_4y.pkl):
  - 単勝 ROI 140% / 的中 53% / n=103 (test 745日 = 約4.1回/月)
  - 外枠勾配の単調性: head=4 (111%) < head=5 (107%) < head=6 (140%)
  - 年度別も全年プラス: 2024 143% / 2025 146% / 2026 120%
  - z=2.3 (p1>=.35)。低頻度・高分散のため『外枠本命ポートフォリオの一部』
    として小額運用が妥当 (1点100円)。

look-ahead bias 無し: head/p1 はレース前のモデル予測 (predictions.prob_first)
のみから決まり、オッズや結果は選択に使わない。
"""
from __future__ import annotations

from typing import Optional

# ============================================================
# トリガー条件
# ============================================================
OUTER6_HEAD_BOAT: int = 6
"""本命 (argmax prob_first) がこの艇番のときに発火 (最外艇)。"""

OUTER6_P1_THRESHOLD: float = 0.35
"""本命の prob_first (p1) がこの値以上で発火。検証で ROI/hit/z が最良の閾値。"""

# ============================================================
# 買い目 / 検証実績
# ============================================================
OUTER6_BET_TYPE: str = "win"
"""券種: 単勝。"""

OUTER6_BET_UNIT_YEN: int = 100
"""1点あたりの賭け金 (円)。低頻度・高分散なので小額固定。"""

OUTER6_RECOVERY: float = 140.0
"""検証 ROI % (単勝 head, test, n=103)。"""

OUTER6_HIT_RATE: float = 0.53
"""検証 的中率 (test, n=103)。"""

OUTER6_SAMPLE_N: int = 103
"""検証サンプル数 (test 745日)。低頻度 (約4.1回/月) ゆえ参考値として扱う。"""

OUTER6_LABEL: str = "🚤6号艇本命"
"""バッジ表示ラベル。"""


def is_outer6_eligible(head: Optional[int], p1: Optional[float]) -> bool:
    """外枠本命 (6号艇単勝) の発火条件を満たすか。

    Args:
      head: モデルの本命艇番 (prob_first が最大の boat_number)。
      p1:   その本命の prob_first (最大確率)。

    Returns:
      head == 6 かつ p1 >= 0.35 なら True。
    """
    if head is None or p1 is None:
        return False
    try:
        h = int(head)
        p = float(p1)
    except (TypeError, ValueError):
        return False
    return h == OUTER6_HEAD_BOAT and p >= OUTER6_P1_THRESHOLD


def get_outer6_bets(head: Optional[int], p1: Optional[float]) -> list[tuple[str, str, int]]:
    """発火時の買い目リストを返す。

    Returns:
      [(bet_type, combination, amount_yen), ...]。
      発火しなければ空リスト。発火時は 6号艇単勝を 100円 1点。
    """
    if not is_outer6_eligible(head, p1):
        return []
    return [(OUTER6_BET_TYPE, str(OUTER6_HEAD_BOAT), OUTER6_BET_UNIT_YEN)]


def evaluate_outer6_race(head: Optional[int], p1: Optional[float]) -> dict:
    """1レース分の外枠本命シグナルを評価して dict で返す。

    UI / API シグナル用。発火しない場合も eligible=False の dict を返す
    (呼び出し側で truthy 判定しやすいよう統一)。

    Returns:
      {
        "eligible": bool,
        "head": int|None,
        "p1": float|None,
        "label": str,         # 発火時のみ意味を持つ
        "bet_type": str,
        "combination": str,
        "amount_yen": int,
        "recovery": float,    # 検証 ROI %
        "hit_rate": float,
        "n": int,
      }
    """
    eligible = is_outer6_eligible(head, p1)
    return {
        "eligible": eligible,
        "head": int(head) if head is not None else None,
        "p1": float(p1) if p1 is not None else None,
        "label": OUTER6_LABEL,
        "bet_type": OUTER6_BET_TYPE,
        "combination": str(OUTER6_HEAD_BOAT),
        "amount_yen": OUTER6_BET_UNIT_YEN,
        "recovery": OUTER6_RECOVERY,
        "hit_rate": OUTER6_HIT_RATE,
        "n": OUTER6_SAMPLE_N,
    }


__all__ = [
    "OUTER6_HEAD_BOAT",
    "OUTER6_P1_THRESHOLD",
    "OUTER6_BET_TYPE",
    "OUTER6_BET_UNIT_YEN",
    "OUTER6_RECOVERY",
    "OUTER6_HIT_RATE",
    "OUTER6_SAMPLE_N",
    "OUTER6_LABEL",
    "is_outer6_eligible",
    "get_outer6_bets",
    "evaluate_outer6_race",
]
