"""Leakage-safe evaluator for the adopted course-fit win strategies.

Exhibition ranks describe dash/stretch. Racer-course history is calculated
strictly from races before the race being evaluated. Results and payouts are
labels only and never participate in candidate selection.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Iterator, Mapping, MutableMapping, Sequence


BASELINE_DATE = "2024-11-24"
TARGET_STADIUMS = (8, 11, 19)
ATTACK_KIMARITE = {
    "\u5dee\u3057",
    "\u307e\u304f\u308a",
    "\u307e\u304f\u308a\u5dee\u3057",
    "\u629c\u304d",
}
COURSE_WIN_PRIOR = {1: 0.55, 2: 0.15, 3: 0.12, 4: 0.10, 5: 0.06, 6: 0.03}
COURSE_ATTACK_PRIOR = {1: 0.55, 2: 0.12, 3: 0.09, 4: 0.08, 5: 0.04, 6: 0.02}


@dataclass(frozen=True)
class CourseFitStrategy:
    key: str
    code: str
    label: str
    stadium: int
    target_boat: int
    rounds: tuple[int, int] | None
    general_only: bool
    condition: str
    recovery: float
    n: int
    hits: int
    hit_rate: float
    priority: int

    @property
    def bet_label(self) -> str:
        return f"\u5358\u52dd {self.target_boat}\u53f7\u8247"


COURSE_FIT_STRATEGIES: tuple[CourseFitStrategy, ...] = (
    CourseFitStrategy(
        "tokoname_coursefit_boat2_win", "C1",
        "\u5e38\u6ed1 \u30b3\u30fc\u30b9\u9069\u5408 2\u53f7\u8247\u5358\u52dd",
        8, 2, (4, 9), False, "balanced_head55_m40", 149.1, 58, 28, 48.3, 10,
    ),
    CourseFitStrategy(
        "tokoname_coursefit_boat3_general_win", "C3",
        "\u5e38\u6ed1 \u30b3\u30fc\u30b9\u9069\u5408 3\u53f7\u8247\u5358\u52dd",
        8, 3, None, True, "balanced_head55_m40", 131.0, 62, 21, 33.9, 20,
    ),
    CourseFitStrategy(
        "biwako_coursefit_boat4_gap10_general_win", "C4",
        "\u3073\u308f\u3053 \u30b3\u30fc\u30b9\u9069\u5408 4\u53f7\u8247\u5358\u52dd \u53b3\u9078",
        11, 4, None, True, "fit65_hr1_gap10_m40", 156.0, 45, 16, 35.6, 40,
    ),
    CourseFitStrategy(
        "shimonoseki_coursefit_boat2_win", "C5",
        "\u4e0b\u95a2 \u30b3\u30fc\u30b9\u9069\u5408 2\u53f7\u8247\u5358\u52dd",
        19, 2, (1, 6), False, "balanced_head55_m35", 139.4, 82, 25, 30.5, 30,
    ),
    CourseFitStrategy(
        "biwako_coursefit_boat4_gap5_general_win", "C6",
        "\u3073\u308f\u3053 \u30b3\u30fc\u30b9\u9069\u5408 4\u53f7\u8247\u5358\u52dd Gap5",
        11, 4, None, True, "fit65_hr1_gap5_m40", 152.6, 46, 16, 34.8, 50,
    ),
    CourseFitStrategy(
        "biwako_coursefit_boat4_rank1_general_win", "C7",
        "\u3073\u308f\u3053 \u30b3\u30fc\u30b9\u9069\u5408 4\u53f7\u8247\u5358\u52dd Rank1",
        11, 4, None, True, "fit65_hr1_gap0_m40", 132.5, 56, 18, 32.1, 60,
    ),
    CourseFitStrategy(
        "biwako_coursefit_boat4_gap10_all_win", "C8",
        "\u3073\u308f\u3053 \u30b3\u30fc\u30b9\u9069\u5408 4\u53f7\u8247\u5358\u52dd \u5168\u7af6\u8d70",
        11, 4, None, False, "fit65_hr1_gap10_m40", 143.3, 49, 16, 32.7, 70,
    ),
)
COURSE_FIT_STRATEGY_BY_KEY = {strategy.key: strategy for strategy in COURSE_FIT_STRATEGIES}
ADOPTED_CODES = tuple(strategy.code for strategy in COURSE_FIT_STRATEGIES)


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _rank(values: Mapping[int, float], *, ascending: bool) -> dict[int, int]:
    result: dict[int, int] = {}
    for boat, value in values.items():
        if ascending:
            result[boat] = 1 + sum(1 for other in values.values() if other < value)
        else:
            result[boat] = 1 + sum(1 for other in values.values() if other > value)
    return result


def _history_scores(
    history: Mapping[tuple[int, int], Sequence[int]], racer: int, course: int
) -> tuple[float, float]:
    n, wins, attacks = history.get((racer, course), (0, 0, 0))
    win_score = 100.0 * (wins + 20.0 * COURSE_WIN_PRIOR.get(course, 0.05)) / (n + 20.0)
    attack_score = 100.0 * (attacks + 20.0 * COURSE_ATTACK_PRIOR.get(course, 0.03)) / (n + 20.0)
    return win_score, attack_score


def score_race(
    rows: Sequence[Mapping[str, Any]],
    history: Mapping[tuple[int, int], Sequence[int]],
) -> dict[int, dict[str, float]]:
    """Score a race using only information available before its result."""
    if len(rows) != 6:
        return {}
    by_boat = {_as_int(row.get("boat_number")): row for row in rows}
    if set(by_boat) != {1, 2, 3, 4, 5, 6}:
        return {}
    exhibition: dict[int, float] = {}
    exhibition_st: dict[int, float] = {}
    for boat, row in by_boat.items():
        ex_time = _as_float(row.get("exhibition_time"))
        ex_st = _as_float(row.get("exhibition_st"))
        racer = _as_int(row.get("racer_number"))
        if ex_time is None or not 5.5 <= ex_time <= 8.5:
            return {}
        if ex_st is None or not -0.5 <= ex_st <= 1.0 or racer is None:
            return {}
        exhibition[boat] = ex_time
        exhibition_st[boat] = ex_st

    ex_rank = _rank(exhibition, ascending=True)
    exst_rank = _rank(exhibition_st, ascending=True)
    scored: dict[int, dict[str, float]] = {}
    for boat, row in by_boat.items():
        course = _as_int(row.get("course_number")) or boat
        racer = _as_int(row.get("racer_number"))
        if racer is None:
            return {}
        stretch = 100.0 * (6.0 - ex_rank[boat]) / 5.0
        dash = 100.0 * (6.0 - exst_rank[boat]) / 5.0
        win_score, attack_score = _history_scores(history, racer, course)
        turn = 0.65 * win_score + 0.35 * attack_score
        if course == 1:
            fit = 0.55 * dash + 0.45 * stretch
        elif course == 2:
            fit = 0.35 * dash + 0.25 * stretch + 0.40 * turn
        elif course == 3:
            fit = 0.20 * dash + 0.35 * stretch + 0.45 * turn
        elif course == 4:
            fit = 0.30 * dash + 0.50 * stretch + 0.20 * turn
        else:
            fit = 0.25 * dash + 0.50 * stretch + 0.25 * turn
        scored[boat] = {
            "course": float(course),
            "dash": dash,
            "stretch": stretch,
            "turn": turn,
            "fit": fit,
            "motor": _as_float(row.get("motor_top2")) or 0.0,
        }
    fit_ranks = _rank({boat: score["fit"] for boat, score in scored.items()}, ascending=False)
    for boat, fit_rank in fit_ranks.items():
        scored[boat]["fit_rank"] = float(fit_rank)
    return scored


def _condition_matches(
    condition: str, target: int, scores: Mapping[int, Mapping[str, float]]
) -> bool:
    score = scores[target]
    other_max = max(value["fit"] for boat, value in scores.items() if boat != target)
    if condition == "balanced_head55_m40":
        return bool(score["fit"] >= 55 and score["fit_rank"] <= 2 and score["dash"] >= 60 and score["stretch"] >= 60 and score["motor"] >= 40)
    if condition == "balanced_head55_m35":
        return bool(score["fit"] >= 55 and score["fit_rank"] <= 2 and score["dash"] >= 60 and score["stretch"] >= 60 and score["motor"] >= 35)
    gap = {
        "fit65_hr1_gap10_m40": 10.0,
        "fit65_hr1_gap5_m40": 5.0,
        "fit65_hr1_gap0_m40": 0.0,
    }.get(condition)
    return bool(gap is not None and score["fit"] >= 65 and score["fit_rank"] <= 1 and score["fit"] - other_max >= gap and score["motor"] >= 40)


def evaluate_race(
    rows: Sequence[Mapping[str, Any]],
    history: Mapping[tuple[int, int], Sequence[int]],
) -> list[dict[str, Any]]:
    if not rows:
        return []
    first = rows[0]
    stadium = _as_int(first.get("stadium_number"))
    race_number = _as_int(first.get("race_number"))
    grade = _as_int(first.get("race_grade_number"))
    scores = score_race(rows, history)
    if not scores or stadium is None or race_number is None:
        return []
    matches: list[dict[str, Any]] = []
    for strategy in COURSE_FIT_STRATEGIES:
        if stadium != strategy.stadium:
            continue
        if strategy.rounds and not strategy.rounds[0] <= race_number <= strategy.rounds[1]:
            continue
        if strategy.general_only and grade != 5:
            continue
        if not _condition_matches(strategy.condition, strategy.target_boat, scores):
            continue
        matches.append({"strategy": strategy, "score": scores[strategy.target_boat], "scores": scores})
    return matches


def representative_match(matches: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """Choose one visible signal while retaining every matched ROI key."""
    return min(matches, key=lambda item: item["strategy"].priority) if matches else None


def _rows_from_cursor(cursor: Any) -> Iterator[dict[str, Any]]:
    names = [column[0] for column in cursor.description]
    for row in cursor:
        yield dict(zip(names, tuple(row)))


def _group_races(rows: Iterable[Mapping[str, Any]]) -> Iterator[list[Mapping[str, Any]]]:
    group: list[Mapping[str, Any]] = []
    race_id: str | None = None
    for row in rows:
        current = str(row.get("race_id") or "")
        if race_id is not None and current != race_id:
            yield group
            group = []
        group.append(row)
        race_id = current
    if group:
        yield group


def _current_rows(conn: Any, target_date: str) -> list[dict[str, Any]]:
    placeholders = ",".join("?" for _ in TARGET_STADIUMS)
    cursor = conn.execute(f"""
        SELECT r.race_id, r.race_date, r.stadium_number, r.race_number,
               r.race_grade_number, p.boat_number,
               COALESCE(NULLIF(p.course_number, 0), p.boat_number) AS course_number,
               p.exhibition_time, p.start_timing_exhibition AS exhibition_st,
               e.racer_number, e.assigned_motor_top_2_percent AS motor_top2,
               rr.finishing_position, rr.kimarite,
               wp.combination AS win_result, wp.payout AS win_payout
          FROM races r
          JOIN race_previews p ON p.race_id = r.race_id
          JOIN race_entries e ON e.race_id = r.race_id AND e.boat_number = p.boat_number
          LEFT JOIN race_results rr ON rr.race_id = r.race_id AND rr.boat_number = p.boat_number
          LEFT JOIN race_payouts wp ON wp.race_id = r.race_id AND wp.bet_type = 'win'
         WHERE r.race_date = ? AND r.stadium_number IN ({placeholders})
         ORDER BY r.race_id, p.boat_number
    """, (target_date, *TARGET_STADIUMS))
    return list(_rows_from_cursor(cursor))


def _prior_history(
    conn: Any, target_date: str, racer_numbers: Sequence[int]
) -> dict[tuple[int, int], list[int]]:
    if not racer_numbers:
        return {}
    placeholders = ",".join("?" for _ in racer_numbers)
    cursor = conn.execute(f"""
        SELECT e.racer_number,
               COALESCE(NULLIF(p.course_number, 0), rr.course_number, p.boat_number) AS course_number,
               rr.finishing_position, rr.kimarite
          FROM races r
          JOIN race_previews p ON p.race_id = r.race_id
          JOIN race_entries e ON e.race_id = r.race_id AND e.boat_number = p.boat_number
          JOIN race_results rr ON rr.race_id = r.race_id AND rr.boat_number = p.boat_number
         WHERE r.race_date >= ? AND r.race_date < ?
           AND e.racer_number IN ({placeholders})
           AND p.exhibition_time BETWEEN 5.5 AND 8.5
           AND p.start_timing_exhibition BETWEEN -0.5 AND 1.0
    """, (BASELINE_DATE, target_date, *racer_numbers))
    history: dict[tuple[int, int], list[int]] = {}
    for row in _rows_from_cursor(cursor):
        racer = _as_int(row.get("racer_number"))
        course = _as_int(row.get("course_number"))
        if racer is None or course is None:
            continue
        state = history.setdefault((racer, course), [0, 0, 0])
        state[0] += 1
        if _as_int(row.get("finishing_position")) == 1:
            state[1] += 1
            if str(row.get("kimarite") or "") in ATTACK_KIMARITE:
                state[2] += 1
    return history


def load_live_matches(conn: Any, target_date: str) -> list[dict[str, Any]]:
    rows = _current_rows(conn, target_date)
    racers = sorted({_as_int(row.get("racer_number")) for row in rows} - {None})
    history = _prior_history(conn, target_date, racers)
    groups: list[dict[str, Any]] = []
    for race_rows in _group_races(rows):
        matches = evaluate_race(race_rows, history)
        if matches:
            first = race_rows[0]
            groups.append({
                "race_id": first["race_id"],
                "race_date": first["race_date"],
                "stadium_number": first["stadium_number"],
                "race_number": first["race_number"],
                "matches": matches,
            })
    return groups


def iter_backtest_matches(
    conn: Any, evaluate_from: str, to_date: str
) -> Iterator[dict[str, Any]]:
    """Evaluate each race before adding that race's result to history."""
    cursor = conn.execute("""
        SELECT r.race_id, r.race_date, r.stadium_number, r.race_number,
               r.race_grade_number, p.boat_number,
               COALESCE(NULLIF(p.course_number, 0), p.boat_number) AS course_number,
               p.exhibition_time, p.start_timing_exhibition AS exhibition_st,
               e.racer_number, e.assigned_motor_top_2_percent AS motor_top2,
               rr.finishing_position, rr.kimarite,
               wp.combination AS win_result, wp.payout AS win_payout
          FROM races r
          JOIN race_previews p ON p.race_id = r.race_id
          JOIN race_entries e ON e.race_id = r.race_id AND e.boat_number = p.boat_number
          JOIN race_results rr ON rr.race_id = r.race_id AND rr.boat_number = p.boat_number
          LEFT JOIN race_payouts wp ON wp.race_id = r.race_id AND wp.bet_type = 'win'
         WHERE r.race_date BETWEEN ? AND ?
           AND p.exhibition_time BETWEEN 5.5 AND 8.5
           AND p.start_timing_exhibition BETWEEN -0.5 AND 1.0
         ORDER BY r.race_date, r.race_id, p.boat_number
    """, (BASELINE_DATE, to_date))
    history: MutableMapping[tuple[int, int], list[int]] = {}
    for race_rows in _group_races(_rows_from_cursor(cursor)):
        first = race_rows[0]
        race_date = str(first.get("race_date") or "")
        matches = evaluate_race(race_rows, history) if race_date >= evaluate_from else []
        win_result = str(first.get("win_result") or "")
        win_payout = _as_int(first.get("win_payout")) or 0
        for match in matches:
            strategy: CourseFitStrategy = match["strategy"]
            hit = win_result == str(strategy.target_boat)
            yield {
                "race_id": str(first.get("race_id") or ""),
                "date": race_date,
                "strategy": strategy,
                "hit": hit,
                "pay": win_payout if hit else 0,
            }
        for row in race_rows:
            racer = _as_int(row.get("racer_number"))
            course = _as_int(row.get("course_number"))
            if racer is None or course is None:
                continue
            state = history.setdefault((racer, course), [0, 0, 0])
            state[0] += 1
            if _as_int(row.get("finishing_position")) == 1:
                state[1] += 1
                if str(row.get("kimarite") or "") in ATTACK_KIMARITE:
                    state[2] += 1
