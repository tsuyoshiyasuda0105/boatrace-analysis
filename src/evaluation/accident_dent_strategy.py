"""Leakage-safe adopted strategies based on accident-rate start dents.

Selection uses only information available before the race day. Results and
payouts are attached only after a race has matched a strategy.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable


ACCIDENT_DENT_CACHE_VERSION = "accident_dent_v1"


@dataclass(frozen=True)
class AccidentDentStrategy:
    key: str
    label: str
    venue: int
    dent_boat: int
    attack_boat: int
    combination: str
    recovery: float
    sample_size: int
    hits: int
    dent_st_min: float
    adjacent_gap_min: float
    attack_st_max: float
    round_min: int = 1
    dent_class_a_only: bool = False
    attack_national_top1_min: float | None = None
    attack_national_top2_min: float | None = None
    dent_national_top2_max: float | None = None
    dent_motor_top2_max: float | None = None

    @property
    def bet_type(self) -> str:
        return "exacta"

    @property
    def hit_rate(self) -> float:
        return self.hits / self.sample_size * 100.0


ACCIDENT_DENT_STRATEGIES = (
    AccidentDentStrategy(
        "toda_dent2_makuri4_41", "戸田 4-1 事故率へこみ型", 2, 2, 4,
        "4-1", 159.7, 38, 8, 0.15, 0.01, 0.16,
        attack_national_top2_min=30.0, dent_national_top2_max=30.0,
    ),
    AccidentDentStrategy(
        "toda_a_accident2_13_exa", "戸田 2号艇A級事故率へこみ型", 2, 2, 3,
        "1-3", 299.4, 17, 5, 0.16, 0.01, 0.15,
        dent_class_a_only=True, attack_national_top1_min=5.0,
    ),
    AccidentDentStrategy(
        "edogawa_late_dent2_makuri3_31", "江戸川 7-12R 3-1 事故率へこみ型", 3, 2, 3,
        "3-1", 151.0, 31, 8, 0.15, 0.02, 0.16, round_min=7,
        dent_national_top2_max=30.0,
    ),
    AccidentDentStrategy(
        "edogawa_a_accident4_12_exa", "江戸川 4号艇A級事故率へこみ型", 3, 4, 2,
        "1-2", 284.7, 15, 7, 0.15, 0.02, 0.16,
        dent_class_a_only=True, attack_national_top2_min=40.0,
    ),
    AccidentDentStrategy(
        "biwako_dent2_makuri3_31", "びわこ 3-1 事故率へこみ型", 11, 2, 3,
        "3-1", 166.8, 56, 12, 0.15, 0.01, 0.16,
        attack_national_top2_min=40.0,
    ),
    AccidentDentStrategy(
        "amagasaki_dent3_makuri4_41", "尼崎 4-1 事故率へこみ型", 13, 3, 4,
        "4-1", 168.8, 26, 6, 0.15, 0.04, 0.16,
        dent_motor_top2_max=35.0,
    ),
    AccidentDentStrategy(
        "shimonoseki_a_accident4_13_exa", "下関 4号艇A級事故率へこみ型", 19, 4, 3,
        "1-3", 201.2, 17, 6, 0.16, 0.02, 0.16,
        dent_class_a_only=True, dent_motor_top2_max=35.0,
    ),
)

ACCIDENT_DENT_BY_KEY = {strategy.key: strategy for strategy in ACCIDENT_DENT_STRATEGIES}


def assessment_start(date_iso: str) -> str:
    year, month = int(date_iso[:4]), int(date_iso[5:7])
    if 5 <= month <= 10:
        return f"{year:04d}-05-01"
    if month >= 11:
        return f"{year:04d}-11-01"
    return f"{year - 1:04d}-11-01"


def _number(ctx: dict[str, Any], key: str, default: float | None = None) -> float | None:
    try:
        value = ctx.get(key)
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def matches_strategy(strategy: AccidentDentStrategy, ctx: dict[str, Any]) -> bool:
    """Return True when a pre-race context matches the exact adopted rule."""
    if int(_number(ctx, "stadium", -1) or -1) != strategy.venue:
        return False
    if int(_number(ctx, "race_number", 0) or 0) < strategy.round_min:
        return False

    dent = strategy.dent_boat
    attack = strategy.attack_boat
    dent_rate = _number(ctx, f"boat{dent}_accident_rate", 0.0) or 0.0
    dent_starts = _number(ctx, f"boat{dent}_accident_starts", 0.0) or 0.0
    dent_avg = _number(ctx, f"boat{dent}_avg_st_180")
    dent_n = _number(ctx, f"boat{dent}_avg_st_count", 0.0) or 0.0
    attack_avg = _number(ctx, f"boat{attack}_avg_st_180")
    attack_n = _number(ctx, f"boat{attack}_avg_st_count", 0.0) or 0.0
    if dent_rate < 0.5 or dent_starts < 8 or dent_n < 30 or attack_n < 30:
        return False
    if dent_avg is None or attack_avg is None:
        return False
    if dent_avg < strategy.dent_st_min or attack_avg > strategy.attack_st_max:
        return False

    adjacent = [boat for boat in (dent - 1, dent + 1) if 1 <= boat <= 6]
    adjacent_st = [_number(ctx, f"boat{boat}_avg_st_180") for boat in adjacent]
    if any(value is None for value in adjacent_st):
        return False
    if dent_avg - min(adjacent_st) < strategy.adjacent_gap_min:
        return False
    if strategy.dent_class_a_only:
        dent_class = int(_number(ctx, f"boat{dent}_class", 9) or 9)
        if dent_class not in (1, 2):
            return False

    attack_n1 = _number(ctx, f"boat{attack}_national_top1", 0.0) or 0.0
    attack_n2 = _number(ctx, f"boat{attack}_national_top2", 0.0) or 0.0
    dent_n2 = _number(ctx, f"boat{dent}_national_top2", 0.0) or 0.0
    dent_motor = _number(ctx, f"boat{dent}_motor_top2", 0.0) or 0.0
    if strategy.attack_national_top1_min is not None and attack_n1 < strategy.attack_national_top1_min:
        return False
    if strategy.attack_national_top2_min is not None and attack_n2 < strategy.attack_national_top2_min:
        return False
    if strategy.dent_national_top2_max is not None and dent_n2 > strategy.dent_national_top2_max:
        return False
    if strategy.dent_motor_top2_max is not None and dent_motor > strategy.dent_motor_top2_max:
        return False
    return True


def live_matches(ctx: dict[str, Any] | None) -> list[AccidentDentStrategy]:
    if not ctx:
        return []
    return [strategy for strategy in ACCIDENT_DENT_STRATEGIES if matches_strategy(strategy, ctx)]


def iter_backtest_matches(conn, from_date: str, to_date: str) -> Iterable[dict[str, Any]]:
    """Yield historical matches with accident rate frozen before each race day."""
    warmup = assessment_start(from_date)
    rows = conn.execute(
        """
        SELECT r.race_id, r.race_date, r.stadium_number, r.race_number,
               e.boat_number, e.racer_number, e.class_number,
               ds.derived_avg_start_timing_180d, ds.derived_start_count_180d,
               e.national_top_1_percent, e.national_top_2_percent,
               e.assigned_motor_top_2_percent
          FROM races r
          JOIN race_entries e ON e.race_id = r.race_id
          LEFT JOIN derived_start_stats ds
            ON ds.race_id = e.race_id AND ds.boat_number = e.boat_number
         WHERE r.race_date BETWEEN ? AND ?
         ORDER BY r.race_date, r.race_id, e.boat_number
        """,
        (warmup, to_date),
    ).fetchall()
    events = conn.execute(
        """
        SELECT racer_number, race_date, accident_points, event_code, is_yusho
          FROM racer_accident_events
         WHERE race_date BETWEEN ? AND ?
         ORDER BY race_date
        """,
        (warmup, to_date),
    ).fetchall()
    payouts = conn.execute(
        """
        SELECT p.race_id, p.combination, MAX(p.payout)
          FROM race_payouts p
          JOIN races r ON r.race_id = p.race_id
         WHERE r.race_date BETWEEN ? AND ?
           AND p.bet_type = 'exacta'
           AND p.combination IN ('1-2','1-3','3-1','4-1')
         GROUP BY p.race_id, p.combination
        """,
        (from_date, to_date),
    ).fetchall()
    payout_by_race = {(str(race_id), str(combo)): int(pay or 0) for race_id, combo, pay in payouts}

    events_by_date: dict[str, list[tuple[int, int, str, int]]] = defaultdict(list)
    for racer, race_date, points, event_code, is_yusho in events:
        events_by_date[str(race_date)].append((int(racer), int(points or 0), str(event_code or ""), int(is_yusho or 0)))
    races_by_date: dict[str, dict[str, list[Any]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        races_by_date[str(row[1])][str(row[0])].append(row)

    starts: dict[tuple[int, str], int] = defaultdict(int)
    points: dict[tuple[int, str], int] = defaultdict(int)
    fl_counts: dict[tuple[int, str], int] = defaultdict(int)
    for race_date in sorted(races_by_date):
        period = assessment_start(race_date)
        day_start_counts: dict[int, int] = defaultdict(int)
        for race_id, entries in races_by_date[race_date].items():
            first = entries[0]
            ctx: dict[str, Any] = {
                "race_id": race_id,
                "race_date": race_date,
                "stadium": int(first[2]),
                "race_number": int(first[3]),
            }
            for entry in entries:
                boat, racer = int(entry[4]), int(entry[5])
                key = (racer, period)
                day_start_counts[racer] += 1
                ctx[f"boat{boat}_class"] = entry[6]
                ctx[f"boat{boat}_avg_st_180"] = entry[7]
                ctx[f"boat{boat}_avg_st_count"] = entry[8]
                ctx[f"boat{boat}_national_top1"] = entry[9]
                ctx[f"boat{boat}_national_top2"] = entry[10]
                ctx[f"boat{boat}_motor_top2"] = entry[11]
                ctx[f"boat{boat}_accident_starts"] = starts[key]
                ctx[f"boat{boat}_accident_rate"] = points[key] / starts[key] if starts[key] else 0.0
            if race_date >= from_date:
                for strategy in live_matches(ctx):
                    payout = payout_by_race.get((race_id, strategy.combination), 0)
                    yield {
                        "race_id": race_id,
                        "race_date": race_date,
                        "strategy": strategy,
                        "payout": payout,
                        "hit": payout > 0,
                    }
        for racer, start_count in day_start_counts.items():
            starts[(racer, period)] += start_count
        for racer, value, event_code, is_yusho in events_by_date.get(race_date, []):
            key = (racer, period)
            if event_code == "FL" and fl_counts[key] >= 1:
                value += 20 if is_yusho else 10
            points[key] += value
            if event_code == "FL":
                fl_counts[key] += 1
