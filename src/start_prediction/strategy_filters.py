"""Use start predictions as leak-safe filters for adopted strategies.

This module intentionally keeps the filter decision separate from payout and
result evaluation.  A decision may only inspect immutable start prediction
snapshots that were created before the race; actual results are joined later by
report scripts.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable


COMBO_RE = re.compile(r"\b([1-6]-[1-6](?:-[1-6])?)\b")


@dataclass(frozen=True)
class StrategyBet:
    bet_type: str
    combination: str

    @property
    def head_boat(self) -> int:
        return int(self.combination.split("-", 1)[0])

    @property
    def is_trifecta(self) -> bool:
        return self.bet_type == "trifecta"


@dataclass(frozen=True)
class StrategyCandidate:
    race_id: str
    race_date: str
    strategy_key: str
    label: str
    bets: tuple[StrategyBet, ...]


@dataclass(frozen=True)
class FilterResult:
    filter_key: str
    passed: bool
    reason: str


def parse_strategy_bets(text: str) -> tuple[StrategyBet, ...]:
    """Parse visible bet combinations from a market-signal label/bet string."""
    bets: list[StrategyBet] = []
    seen: set[tuple[str, str]] = set()
    for combo in COMBO_RE.findall(text or ""):
        parts = combo.split("-")
        bet_type = "trifecta" if len(parts) == 3 else "exacta"
        key = (bet_type, combo)
        if key in seen:
            continue
        seen.add(key)
        bets.append(StrategyBet(bet_type, combo))
    return tuple(bets)


def _boat(prediction: dict[str, Any], boat_number: int) -> dict[str, Any]:
    for row in prediction.get("boats") or []:
        try:
            if int(row.get("boat_number")) == int(boat_number):
                return row
        except (TypeError, ValueError):
            continue
    return {}


def _scenario_rank(prediction: dict[str, Any], combo: str) -> int | None:
    for row in prediction.get("trifectas") or []:
        if str(row.get("scenario_key") or row.get("combination") or "") == combo:
            try:
                return int(row.get("rank"))
            except (TypeError, ValueError):
                return None
    return None


def _head_first_probability(prediction: dict[str, Any], bet: StrategyBet) -> float | None:
    row = _boat(prediction, bet.head_boat)
    try:
        return float(row.get("first_probability"))
    except (TypeError, ValueError):
        return None


def _head_start_top_probability(prediction: dict[str, Any], bet: StrategyBet) -> float | None:
    row = _boat(prediction, bet.head_boat)
    try:
        return float(row.get("start_top_probability"))
    except (TypeError, ValueError):
        return None


def _prediction_confidence(prediction: dict[str, Any]) -> float | None:
    try:
        return float(prediction.get("confidence"))
    except (TypeError, ValueError):
        return None


def _best_bet(candidate: StrategyCandidate) -> StrategyBet | None:
    return candidate.bets[0] if candidate.bets else None


def pass_head_first_probability(
    candidate: StrategyCandidate,
    prediction: dict[str, Any],
    *,
    minimum: float,
) -> FilterResult:
    bet = _best_bet(candidate)
    if bet is None:
        return FilterResult(f"head_first_ge_{int(minimum * 100)}", False, "no bet")
    value = _head_first_probability(prediction, bet)
    passed = value is not None and value >= minimum
    return FilterResult(
        f"head_first_ge_{int(minimum * 100)}",
        passed,
        f"head_first={value:.3f}" if value is not None else "head_first=missing",
    )


def pass_head_start_top_probability(
    candidate: StrategyCandidate,
    prediction: dict[str, Any],
    *,
    minimum: float,
) -> FilterResult:
    bet = _best_bet(candidate)
    if bet is None:
        return FilterResult(f"head_start_top_ge_{int(minimum * 100)}", False, "no bet")
    value = _head_start_top_probability(prediction, bet)
    passed = value is not None and value >= minimum
    return FilterResult(
        f"head_start_top_ge_{int(minimum * 100)}",
        passed,
        f"head_start_top={value:.3f}" if value is not None else "head_start_top=missing",
    )


def pass_first_mark_matches_head(candidate: StrategyCandidate, prediction: dict[str, Any]) -> FilterResult:
    bet = _best_bet(candidate)
    if bet is None:
        return FilterResult("first_mark_head", False, "no bet")
    try:
        first_mark = int(prediction.get("first_mark_boat"))
    except (TypeError, ValueError):
        first_mark = 0
    passed = first_mark == bet.head_boat
    return FilterResult("first_mark_head", passed, f"first_mark={first_mark or 'missing'}")


def pass_combo_in_trifecta_top(
    candidate: StrategyCandidate,
    prediction: dict[str, Any],
    *,
    top_n: int,
) -> FilterResult:
    if not candidate.bets:
        return FilterResult(f"combo_top{top_n}", False, "no bet")
    ranks = [
        _scenario_rank(prediction, bet.combination)
        for bet in candidate.bets
        if bet.is_trifecta
    ]
    best_rank = min([rank for rank in ranks if rank is not None], default=None)
    passed = best_rank is not None and best_rank <= top_n
    return FilterResult(
        f"combo_top{top_n}",
        passed,
        f"best_rank={best_rank}" if best_rank is not None else "best_rank=missing",
    )


def pass_confidence(candidate: StrategyCandidate, prediction: dict[str, Any], *, minimum: float) -> FilterResult:
    value = _prediction_confidence(prediction)
    passed = value is not None and value >= minimum
    return FilterResult(
        f"confidence_ge_{int(minimum * 100)}",
        passed,
        f"confidence={value:.3f}" if value is not None else "confidence=missing",
    )


def pass_post_improves_head_probability(
    candidate: StrategyCandidate,
    pre_prediction: dict[str, Any] | None,
    post_prediction: dict[str, Any] | None,
) -> FilterResult:
    bet = _best_bet(candidate)
    if bet is None or not pre_prediction or not post_prediction:
        return FilterResult("post_head_prob_up", False, "missing")
    pre = _head_first_probability(pre_prediction, bet)
    post = _head_first_probability(post_prediction, bet)
    passed = pre is not None and post is not None and post >= pre
    reason = "missing" if pre is None or post is None else f"pre={pre:.3f},post={post:.3f}"
    return FilterResult("post_head_prob_up", passed, reason)


def pass_post_confidence_improves(
    candidate: StrategyCandidate,
    pre_prediction: dict[str, Any] | None,
    post_prediction: dict[str, Any] | None,
) -> FilterResult:
    if not pre_prediction or not post_prediction:
        return FilterResult("post_confidence_up", False, "missing")
    pre = _prediction_confidence(pre_prediction)
    post = _prediction_confidence(post_prediction)
    passed = pre is not None and post is not None and post >= pre
    reason = "missing" if pre is None or post is None else f"pre={pre:.3f},post={post:.3f}"
    return FilterResult("post_confidence_up", passed, reason)


FilterCallable = Callable[[StrategyCandidate, dict[str, Any]], FilterResult]


def candidate_filter_suite(stage: str) -> dict[str, FilterCallable]:
    """Return a compact, explainable filter suite for search reports."""
    prefix = "pre" if stage == "pre_exhibition" else "post"
    return {
        f"{prefix}_head_first_ge_45": lambda c, p: pass_head_first_probability(c, p, minimum=0.45),
        f"{prefix}_head_first_ge_55": lambda c, p: pass_head_first_probability(c, p, minimum=0.55),
        f"{prefix}_head_start_top_ge_30": lambda c, p: pass_head_start_top_probability(c, p, minimum=0.30),
        f"{prefix}_first_mark_head": pass_first_mark_matches_head,
        f"{prefix}_confidence_ge_55": lambda c, p: pass_confidence(c, p, minimum=0.55),
        f"{prefix}_confidence_ge_65": lambda c, p: pass_confidence(c, p, minimum=0.65),
        f"{prefix}_combo_top5": lambda c, p: pass_combo_in_trifecta_top(c, p, top_n=5),
        f"{prefix}_combo_top10": lambda c, p: pass_combo_in_trifecta_top(c, p, top_n=10),
    }
