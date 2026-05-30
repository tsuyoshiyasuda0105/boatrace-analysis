"""L4 戦略 補助点 (展示タイム強弱付け) の単一情報源 (Single Source of Truth).

ユーザー検証結果 (2026-05-30) に基づく実装:
  - 展示タイムは「除外フィルタ」としては弱いが、「補助点」として有効
  - L4 候補の中で優先順位を上げる/下げる補助材料として使用
  - 2 軸の補助点で 0-2 点を計算、ベットサイズ強弱付けに使う

【補助点の定義】
  +1点 A: **1号艇 best差 ≤ 0.03**
    レース内 boat1-6 の展示タイムのうち、1号艇が最速タイムから 0.03秒以内.
    つまり 1号艇の展示が「最速級」であることを示す.

  +1点 B: **2号艇 が 3号艇 より展示速い**
    boat2.exhibition_time < boat3.exhibition_time.
    1-2-3 着順成立時に 3着の 2号艇が「実際の展示で 3号艇より速い」ことを
    補強する条件.

  合計 0 / 1 / 2 点.

【検証結果 (ユーザー実測)】

  | 補助点 | n | ROI | 差分 (L4全体比) |
  |---|---:|---:|---:|
  | L4全体 (展示なし含む) | 7,234 | 164.4% | (基準) |
  | 展示データあり L4 | 1,430 | 166.0% | +1.6pt |
  | 補助点 ≥1 | 1,114 | 171.4% | +7.0pt |
  | 補助点 =2 | 388 | **180.8%** | **+16.4pt** |
  | 補助点 =0 | 316 | 146.9% | -17.5pt |

  年別 (2025 / 2026):
  - 展示データあり L4: 158.6% / 175.5%
  - 補助点 ≥1: 165.6% / 179.0%
  - 補助点 =2: **185.8%** / **173.7%**

  → **補助点 =2 は両年で安定して高 ROI** (180% 前後).
  → 補助点 =0 は ROI 低下 (146.9%) だが、完全除外は慎重 (年別では大幅変動).

【推奨ベットサイズ】

  ユーザー判定:
  - 通常 L4: 100 円
  - 補助点 1: **150 円** (1.5 倍)
  - 補助点 2: **200 円** (2 倍)
  - 補助点 0: 余力がなければ見送り候補 (完全除外はまだ慎重に)

  シミュレーション (ユーザー実測):
  - 全 L4 を 100円 → 利益 +466,060円
  - 補助点 0 を見送り、1点=100円, 2点=200円 → 利益 +110,890円 (ROI 173.8%)
  - 補助点に応じて 100/150/200円 → 利益 +521,505円 (ROI 165.3%)

【適用条件】

  - **L4 候補 (= _evaluate_l4 が None 以外を返した race) のみ** に適用
  - 展示タイム (start_timing_exhibition) が boat1-6 全員分揃っているとき
    のみ補助点を計算 (足りない場合は補助点なし = "未集計" 扱い)
  - 男性のみ / 雨除外 などの L4 既存フィルタはそのまま継承
"""
from __future__ import annotations

from typing import Optional, Sequence

# ============================================================
# 定数
# ============================================================
BEST_DIFF_THRESHOLD_SEC: float = 0.03
"""補助点 A: 1号艇の展示タイムが最速から何秒以内なら +1点とするか."""

BASE_BET_YEN: int = 100
"""L4 ベース投入額 (補助点 0 または非該当時の標準)."""

BET_BY_SCORE: dict[int, int] = {
    0: 100,
    1: 150,
    2: 200,
}
"""補助点ごとの推奨投入額 (円).

ユーザー判定: 100/150/200 円. 補助点 0 でも完全除外せず 100円維持.
余力なければ 0 点を見送る運用も可 (呼び出し側で判断).
"""

# 検証 ROI (ユーザー実測、2026-05-30 時点)
ROI_BASELINE_L4_PCT: float = 164.4
"""L4 全体 (展示データなしも含む) のベース ROI %."""

ROI_WITH_EXHIBITION_PCT: float = 166.0
"""展示データあり L4 の ROI %."""

ROI_BY_SCORE: dict[int, float] = {
    0: 146.9,
    1: 171.4,  # 補助点 ≥1 平均 (個別 1 点だけの値ではない)
    2: 180.8,
}
"""補助点別 ROI % (ユーザー実測、展示データあり race のみ)."""

# 表示用ラベル
SCORE_LABEL: dict[int, str] = {
    0: "★0",
    1: "★1",
    2: "★★ (補助点満点)",
}

# 補助点詳細ラベル (どの軸が立っているか分かる用)
AXIS_LABEL_BEST_DIFF: str = "1号艇 展示最速級 (best差≤0.03)"
AXIS_LABEL_BOAT2_FASTER: str = "2号艇 展示 > 3号艇"


# ============================================================
# 判定関数
# ============================================================
def compute_bonus_score(
    boat1_ex_time: Optional[float],
    boat2_ex_time: Optional[float],
    boat3_ex_time: Optional[float],
    all_ex_times: Sequence[Optional[float]],
) -> tuple[int, dict]:
    """1 race に対する補助点を計算して (score, detail) を返す.

    Args:
      boat1_ex_time: 1号艇の展示タイム (秒).
      boat2_ex_time: 2号艇の展示タイム.
      boat3_ex_time: 3号艇の展示タイム.
      all_ex_times: boat1-6 の展示タイム配列 (None 含み 6 要素).

    Returns:
      (score, detail) where:
        score: 0, 1, 2 のいずれか
        detail: {
          "axis_best_diff": bool,        # +1 軸 A (1号艇 best差≤0.03) が立ったか
          "axis_boat2_faster": bool,     # +1 軸 B (2号艇>3号艇) が立ったか
          "best_time": float | None,     # レース内最速展示タイム
          "boat1_best_diff": float | None,
          "incomplete": bool,            # 6艇分揃っていない → 補助点未集計
        }

    補助点を計算できない場合 (展示タイム不足):
      score=0, detail.incomplete=True
    """
    detail = {
        "axis_best_diff": False,
        "axis_boat2_faster": False,
        "best_time": None,
        "boat1_best_diff": None,
        "incomplete": False,
    }

    # 6 艇分揃っているか確認
    valid_times: list[float] = []
    for t in all_ex_times:
        if t is None:
            detail["incomplete"] = True
            continue
        try:
            valid_times.append(float(t))
        except (TypeError, ValueError):
            detail["incomplete"] = True

    if len(valid_times) < 6 or detail["incomplete"]:
        detail["incomplete"] = True
        return 0, detail

    # 最速展示タイム
    best_time = min(valid_times)
    detail["best_time"] = best_time

    # 軸 A: 1号艇 best差 ≤ 0.03
    try:
        b1 = float(boat1_ex_time) if boat1_ex_time is not None else None
    except (TypeError, ValueError):
        b1 = None
    if b1 is not None:
        diff = b1 - best_time
        detail["boat1_best_diff"] = round(diff, 3)
        if diff <= BEST_DIFF_THRESHOLD_SEC + 1e-9:  # 浮動小数誤差ガード
            detail["axis_best_diff"] = True

    # 軸 B: 2号艇 < 3号艇 (タイムは速いほど数値小さい)
    try:
        b2 = float(boat2_ex_time) if boat2_ex_time is not None else None
        b3 = float(boat3_ex_time) if boat3_ex_time is not None else None
    except (TypeError, ValueError):
        b2 = b3 = None
    if b2 is not None and b3 is not None and b2 < b3:
        detail["axis_boat2_faster"] = True

    score = int(detail["axis_best_diff"]) + int(detail["axis_boat2_faster"])
    return score, detail


def recommended_bet_yen(score: int, *, skip_zero: bool = False) -> int:
    """補助点に応じた推奨投入額 (円).

    Args:
      score: 0/1/2
      skip_zero: True なら補助点 0 のとき 0 円 (見送り) を返す.

    Returns:
      投入額 (円).
    """
    if skip_zero and score == 0:
        return 0
    return BET_BY_SCORE.get(score, BASE_BET_YEN)


def score_label(score: int) -> str:
    """補助点の表示用短ラベル (例: '★★', '★1')."""
    return SCORE_LABEL.get(score, f"★{score}")


def evaluate_l4_with_bonus(
    boat1_ex_time: Optional[float],
    boat2_ex_time: Optional[float],
    boat3_ex_time: Optional[float],
    all_ex_times: Sequence[Optional[float]],
    *,
    skip_zero: bool = False,
) -> dict:
    """L4 候補 race に対する補助点評価をまとめて返す.

    Args:
      boat1_ex_time, boat2_ex_time, boat3_ex_time: 各艇の展示タイム.
      all_ex_times: boat1-6 の展示タイム (None 含み 6 要素).
      skip_zero: True なら補助点 0 のときベットを見送り (0円).

    Returns:
      {
        "score": int,                  # 0, 1, 2
        "score_label": str,            # 表示用
        "axis_best_diff": bool,
        "axis_boat2_faster": bool,
        "incomplete": bool,            # 展示タイム不足
        "recommended_bet_yen": int,    # 100/150/200/0
        "expected_roi_pct": float | None,  # 検証 ROI (補助点別)
        "detail": dict,                # 内部詳細
      }
    """
    score, detail = compute_bonus_score(
        boat1_ex_time, boat2_ex_time, boat3_ex_time, all_ex_times
    )
    bet = recommended_bet_yen(score, skip_zero=skip_zero)
    # 補助点が計算できなければ ROI 推定はベース (展示なし L4) と同等扱い
    if detail["incomplete"]:
        exp_roi: Optional[float] = ROI_BASELINE_L4_PCT
    else:
        exp_roi = ROI_BY_SCORE.get(score)
    return {
        "score": score,
        "score_label": score_label(score),
        "axis_best_diff": detail["axis_best_diff"],
        "axis_boat2_faster": detail["axis_boat2_faster"],
        "incomplete": detail["incomplete"],
        "recommended_bet_yen": bet,
        "expected_roi_pct": exp_roi,
        "detail": detail,
    }


__all__ = [
    # 定数
    "BEST_DIFF_THRESHOLD_SEC",
    "BASE_BET_YEN",
    "BET_BY_SCORE",
    "ROI_BASELINE_L4_PCT",
    "ROI_WITH_EXHIBITION_PCT",
    "ROI_BY_SCORE",
    "SCORE_LABEL",
    "AXIS_LABEL_BEST_DIFF",
    "AXIS_LABEL_BOAT2_FASTER",
    # 関数
    "compute_bonus_score",
    "recommended_bet_yen",
    "score_label",
    "evaluate_l4_with_bonus",
]
