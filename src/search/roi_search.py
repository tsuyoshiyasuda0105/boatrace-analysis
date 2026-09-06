"""Read-only condition search and return-on-investment calculations.

The Step 1 payout columns store the amount returned for a JPY 100 stake.
Consequently, for one fixed JPY 100 bet per eligible race, ROI in percent is
the arithmetic mean of the per-race returns (zero for a miss).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
import math
from pathlib import Path
import sqlite3
from typing import Any, Mapping, Sequence

import numpy as np


TOP_LEVEL_KEYS = frozenset(
    {
        "venue",
        "bet",
        "weather",
        "wind_dir",
        "wind_speed",
        "tide_phase",
        "female_present",
        "class_mix",
        "day_index",
        "daypart",
        "race_no",
        "date_from",
        "date_to",
        "boats",
        "compare",
    }
)
BOAT_KEYS = frozenset(
    {
        "class",
        "racer_id",
        "age",
        "avg_st",
        "national_rate",
        "local_rate",
        "national_rate2",
        "local_rate2",
        "motor_rate2",
        "ex_rank",
        "ex_dev",
        "ex_st",
        "kimarite",
        "accident_rate",
        "accident_points",
        "accident_rate_365d",
        "accident_rate_period",
        "accident_count_period",
    }
)
RANGE_KEYS = frozenset({"min", "max"})
COMPARE_KEYS = frozenset({"metric", "boat", "op", "other", "margin"})
COMPARE_METRICS = frozenset(
    {
        "motor_rate2",
        "avg_st",
        "ex_time",
        "ex_st",
        "national_rate",
        "local_rate",
        "national_rate2",
        "age",
    }
)
EX_DEV_KEYS = frozenset({"faster_by", "slower_by"})
KIMARITE_KEYS = frozenset({"nige", "sashi", "makuri", "makurizashi", "nuki", "megumare"})
BET_LEGS = {"tansho": 1, "nirentan": 2, "sanrentan": 3}
HISTORY_CUTOFF = "2023-05-01"
RESTORED_ACCIDENT_CUTOFF = "2016-06-01"
DEFAULT_BET = {"type": "sanrentan", "first": 1, "second": 2, "third": 3}
# 1 レースあたりの点数上限。無制限にすると 1 リクエストで払戻表を何十点も
# 引き当てることになり、本番 (Render) の共有 DB を長く占有する。
MAX_BET_TICKETS = 20
# 1 点あたりの購入額 (円)。ROI は「払戻合計 ÷ 投資合計」で、投資合計は
# レース数 × 点数 × この額。1 点のときは従来の「100円1点」と完全に一致する。
STAKE_PER_TICKET = 100.0
SUPPORTED_SCHEMA_VERSIONS = (2, 3)
READABLE_SCHEMA_VERSIONS = (*SUPPORTED_SCHEMA_VERSIONS, 4, 5, 6, 7, 8, 9, 10)
RETIRED_ODDS_CONDITION_KEYS = frozenset({"odds", "t5_odds_favorite"})
ODDS_FILTER_REMOVED_MESSAGE = (
    "オッズによる絞り込みは廃止されました。"
    "回収率は条件に合う全レースを分母に計算します"
)


@dataclass(frozen=True)
class _Bet:
    kind: str
    result_column: str
    payout_column: str
    # 買い目は複数点を持てる。1 点でも必ずタプルに入れ、呼び出し側が単数・複数を
    # 場合分けしなくて済むようにする。券種は全点で共通 (SQL が券種ごとの列を引く
    # 作りのため、券種混在は扱わない)。
    expected: tuple[int | str, ...]

    @property
    def ticket_count(self) -> int:
        return len(self.expected)


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _known_keys(value: Mapping[str, Any], allowed: frozenset[str], label: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"unknown {label} key(s): {', '.join(unknown)}")


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite number")
    return result


def _integer(value: Any, label: str) -> int:
    number = _number(value, label)
    if not number.is_integer():
        raise ValueError(f"{label} must be an integer")
    return int(number)


def _iso_date(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an ISO date")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO date") from exc


def _sequence(value: Any, label: str) -> list[Any]:
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError(f"{label} must be a non-empty array")
    return list(value)


def _parse_ticket_legs(raw: Mapping[str, Any], kind: str, label: str) -> int | str:
    """1 点ぶんの着順指定を検証して、払戻テーブルの見出し文字列にする。"""
    leg_names = ("first", "second", "third")[: BET_LEGS[kind]]
    missing = sorted(key for key in leg_names if raw.get(key) is None)
    if missing:
        raise ValueError(f"missing bet key(s): {', '.join(missing)}")
    surplus = sorted(
        key
        for key in ("first", "second", "third")
        if key not in leg_names and raw.get(key) is not None
    )
    if surplus:
        raise ValueError(f"unused bet key(s) for {kind}: {', '.join(surplus)}")
    legs = [_integer(raw[key], f"{label}.{key}") for key in leg_names]
    if any(leg < 1 or leg > 6 for leg in legs) or len(set(legs)) != len(legs):
        raise ValueError("買い目は1〜6号艇から、着順ごとに異なる艇番を選んでください")
    return legs[0] if kind == "tansho" else "-".join(map(str, legs))


def _parse_bet(value: Any) -> _Bet:
    # The example ticket is the deterministic fallback when the optional bet
    # object is omitted.  Supplying a partial bet object is still an error.
    raw = DEFAULT_BET if value is None else _mapping(value, "bet")
    allowed = frozenset({"type", "first", "second", "third", "tickets"})
    _known_keys(raw, allowed, "bet")
    kind = raw.get("type")
    if kind not in BET_LEGS:
        raise ValueError("bet.type must be tansho, nirentan, or sanrentan")

    tickets_raw = raw.get("tickets")
    if tickets_raw is None:
        # 単数形式 (type + first/second/third)。保存済みの手法や外部から来る
        # 既存リクエストはこの形なので、そのまま受け続ける。
        expected = (_parse_ticket_legs(raw, kind, "bet"),)
        return _Bet(kind, f"result_{kind}", f"payout_{kind}", expected)

    # 複数形式。単数キーとの併用は、どちらが正か曖昧になるので許さない。
    conflicting = sorted(
        key for key in ("first", "second", "third") if raw.get(key) is not None
    )
    if conflicting:
        raise ValueError(
            f"bet.tickets と同時に指定できないキー: {', '.join(conflicting)}"
        )
    entries = _sequence(tickets_raw, "bet.tickets")
    if len(entries) > MAX_BET_TICKETS:
        raise ValueError(f"買い目は最大{MAX_BET_TICKETS}点までです")
    expected_list: list[int | str] = []
    for index, entry in enumerate(entries):
        label = f"bet.tickets[{index}]"
        ticket_raw = _mapping(entry, label)
        _known_keys(ticket_raw, frozenset({"first", "second", "third"}), label)
        expected_list.append(_parse_ticket_legs(ticket_raw, kind, label))
    if len(set(expected_list)) != len(expected_list):
        raise ValueError("同じ買い目が重複しています")
    return _Bet(kind, f"result_{kind}", f"payout_{kind}", tuple(expected_list))


def _add_predicate(
    filters: list[str],
    params: list[Any],
    null_columns: set[str],
    column: str,
    expression: str,
    values: Sequence[Any],
) -> None:
    filters.append(f"(({expression}) OR {column} IS NULL)")
    params.extend(values)
    null_columns.add(column)


def _add_range(
    filters: list[str],
    params: list[Any],
    null_columns: set[str],
    column: str,
    value: Any,
    label: str,
) -> bool:
    raw = _mapping(value, label)
    _known_keys(raw, RANGE_KEYS, label)
    comparisons: list[str] = []
    values: list[float] = []
    if raw.get("min") is not None:
        comparisons.append(f"{column} >= ?")
        values.append(_number(raw["min"], f"{label}.min"))
    if raw.get("max") is not None:
        comparisons.append(f"{column} <= ?")
        values.append(_number(raw["max"], f"{label}.max"))
    if not comparisons:
        return False
    if len(values) == 2 and values[0] > values[1]:
        raise ValueError(f"{label}.min must not exceed max")
    _add_predicate(filters, params, null_columns, column, " AND ".join(comparisons), values)
    return True


def _compile_conditions(
    conditions: Mapping[str, Any],
) -> tuple[str, list[Any], list[str], _Bet, None]:
    """Compile validated conditions into parameterized SQL.

    A comparison always evaluates the signed difference between two boats:
    ``value(boat) - value(other) >= margin`` for ``op='ge'`` and
    ``value(boat) - value(other) <= -margin`` for ``op='le'``.  ``margin``
    must be non-negative.  Metric-derived column names come only from the
    fixed whitelist above; every margin remains a bound SQL parameter.
    """

    if RETIRED_ODDS_CONDITION_KEYS.intersection(conditions):
        raise ValueError(ODDS_FILTER_REMOVED_MESSAGE)
    _known_keys(conditions, TOP_LEVEL_KEYS, "condition")
    bet = _parse_bet(conditions.get("bet"))
    filters: list[str] = [f"schema_version IN ({','.join('?' for _ in READABLE_SCHEMA_VERSIONS)})"]
    params: list[Any] = list(READABLE_SCHEMA_VERSIONS)
    null_columns: set[str] = set()

    scalar_columns = {
        "venue": "jcd",
        "tide_phase": "tide_phase",
        "female_present": "female_present",
        "class_mix": "class_mix",
        "day_index": "day_index",
        "daypart": "daypart",
    }
    for key, column in scalar_columns.items():
        value = conditions.get(key)
        if value is None:
            continue
        if key == "venue":
            raw_values = _sequence(value, key) if isinstance(value, (list, tuple)) else [value]
            if any(isinstance(item, bool) or not isinstance(item, int) for item in raw_values):
                raise ValueError("venue must be an integer or an array of integers")
            values = [_integer(item, f"{key}[{index}]") for index, item in enumerate(raw_values)]
            if any(not 1 <= item <= 24 for item in values):
                raise ValueError("venue must contain integers from 1 through 24")
            values = list(dict.fromkeys(values))
            placeholders = ",".join("?" for _ in values)
            _add_predicate(
                filters,
                params,
                null_columns,
                column,
                f"{column} IN ({placeholders})",
                values,
            )
            continue
        elif key == "female_present":
            value = _integer(value, key)
            if value not in (0, 1):
                raise ValueError("female_present must be 0 or 1")
        _add_predicate(filters, params, null_columns, column, f"{column} = ?", [value])

    weather = conditions.get("weather")
    if weather is not None:
        values = _sequence(weather, "weather")
        if any(not isinstance(item, str) for item in values):
            raise ValueError("weather values must be strings")
        placeholders = ",".join("?" for _ in values)
        _add_predicate(filters, params, null_columns, "weather", f"weather IN ({placeholders})", values)

    wind_dir = conditions.get("wind_dir")
    if wind_dir is not None:
        values = _sequence(wind_dir, "wind_dir")
        allowed_wind = {"追い風", "向かい風", "横風(右)", "横風(左)", "無風"}
        if any(not isinstance(item, str) or item not in allowed_wind for item in values):
            raise ValueError("wind_dir contains an invalid value")
        placeholders = ",".join("?" for _ in values)
        _add_predicate(filters, params, null_columns, "wind_dir", f"wind_dir IN ({placeholders})", values)

    if conditions.get("wind_speed") is not None:
        _add_range(filters, params, null_columns, "wind_speed", conditions["wind_speed"], "wind_speed")

    if conditions.get("race_no") is not None:
        label = "race_no"
        raw = _mapping(conditions["race_no"], label)
        _known_keys(raw, RANGE_KEYS, label)
        comparisons: list[str] = []
        values: list[int] = []
        if raw.get("min") is not None:
            minimum = _integer(raw["min"], "race_no.min")
            if not 1 <= minimum <= 12:
                raise ValueError("race_no.min must be from 1 through 12")
            comparisons.append("race_no >= ?")
            values.append(minimum)
        if raw.get("max") is not None:
            maximum = _integer(raw["max"], "race_no.max")
            if not 1 <= maximum <= 12:
                raise ValueError("race_no.max must be from 1 through 12")
            comparisons.append("race_no <= ?")
            values.append(maximum)
        if len(values) == 2 and values[0] > values[1]:
            raise ValueError("race_no.min must not exceed max")
        if comparisons:
            _add_predicate(filters, params, null_columns, "race_no", " AND ".join(comparisons), values)

    history_condition = False
    restored_accident_condition = False
    restored_avg_st_condition = False
    boats = conditions.get("boats")
    if boats is not None:
        boat_map = _mapping(boats, "boats")
        for boat_key, raw_boat in boat_map.items():
            if boat_key not in {str(number) for number in range(1, 7)}:
                raise ValueError(f"unknown boat key: {boat_key}")
            boat = _mapping(raw_boat, f"boats.{boat_key}")
            _known_keys(boat, BOAT_KEYS, f"boats.{boat_key}")
            prefix = f"b{boat_key}_"
            if boat.get("class") is not None:
                values = _sequence(boat["class"], f"boats.{boat_key}.class")
                if any(value not in {"A1", "A2", "B1", "B2"} for value in values):
                    raise ValueError(f"boats.{boat_key}.class has an invalid class")
                placeholders = ",".join("?" for _ in values)
                column = prefix + "class"
                _add_predicate(filters, params, null_columns, column, f"{column} IN ({placeholders})", values)
            if boat.get("racer_id") is not None:
                value = _integer(boat["racer_id"], f"boats.{boat_key}.racer_id")
                column = prefix + "racer_id"
                _add_predicate(filters, params, null_columns, column, f"{column} = ?", [value])
            for key in (
                "age",
                "avg_st",
                "national_rate",
                "local_rate",
                "national_rate2",
                "local_rate2",
                "motor_rate2",
                "ex_rank",
                "ex_st",
                "accident_rate",
                "accident_points",
                "accident_rate_365d",
                "accident_rate_period",
                "accident_count_period",
            ):
                if boat.get(key) is not None:
                    active = _add_range(
                        filters,
                        params,
                        null_columns,
                        prefix + key,
                        boat[key],
                        f"boats.{boat_key}.{key}",
                    )
                    history_condition = history_condition or (
                        key in {"accident_rate", "accident_points", "accident_rate_365d"}
                        and active
                    )
                    restored_accident_condition = restored_accident_condition or (
                        key in {"accident_rate_period", "accident_count_period"}
                        and active
                    )
                    restored_avg_st_condition = restored_avg_st_condition or (
                        key == "avg_st" and active
                    )
            if boat.get("ex_dev") is not None:
                label = f"boats.{boat_key}.ex_dev"
                raw = _mapping(boat["ex_dev"], label)
                _known_keys(raw, EX_DEV_KEYS, label)
                comparisons: list[str] = []
                values: list[float] = []
                column = prefix + "ex_dev"
                if raw.get("faster_by") is not None:
                    amount = _number(raw["faster_by"], f"{label}.faster_by")
                    if amount < 0:
                        raise ValueError(f"{label}.faster_by must be non-negative")
                    comparisons.append(f"{column} <= ?")
                    values.append(-amount)
                if raw.get("slower_by") is not None:
                    amount = _number(raw["slower_by"], f"{label}.slower_by")
                    if amount < 0:
                        raise ValueError(f"{label}.slower_by must be non-negative")
                    comparisons.append(f"{column} >= ?")
                    values.append(amount)
                if comparisons:
                    _add_predicate(filters, params, null_columns, column, " AND ".join(comparisons), values)
            if boat.get("kimarite") is not None:
                label = f"boats.{boat_key}.kimarite"
                raw = _mapping(boat["kimarite"], label)
                _known_keys(raw, frozenset({"name", "rate_min"}), label)
                name = raw.get("name")
                rate_min = raw.get("rate_min")
                if name is None and rate_min is None:
                    continue
                if name not in KIMARITE_KEYS or rate_min is None:
                    raise ValueError(f"{label} requires a valid name and rate_min")
                column = prefix + f"kimarite_rate_{name}"
                rate = _number(rate_min, f"{label}.rate_min")
                _add_predicate(filters, params, null_columns, column, f"{column} >= ?", [rate])
                history_condition = True

    comparisons = conditions.get("compare")
    if comparisons is not None:
        for index, item in enumerate(_sequence(comparisons, "compare")):
            label = f"compare.{index}"
            raw = _mapping(item, label)
            _known_keys(raw, COMPARE_KEYS, label)
            missing = sorted(
                key for key in COMPARE_KEYS - {"margin"} if raw.get(key) is None
            )
            if missing:
                raise ValueError(f"{label} missing key(s): {', '.join(missing)}")
            metric = raw["metric"]
            if not isinstance(metric, str) or metric not in COMPARE_METRICS:
                raise ValueError(f"{label}.metric is not supported")
            boat = _integer(raw["boat"], f"{label}.boat")
            other = _integer(raw["other"], f"{label}.other")
            if not 1 <= boat <= 6 or not 1 <= other <= 6:
                raise ValueError(f"{label} boats must be from 1 through 6")
            if boat == other:
                raise ValueError("艇間比較は異なる号艇同士で指定してください。同じ艇同士は比較できません")
            op = raw["op"]
            if not isinstance(op, str) or op not in {"ge", "le"}:
                raise ValueError(f"{label}.op must be ge or le")
            margin = _number(raw.get("margin", 0), f"{label}.margin")
            if margin < 0:
                raise ValueError(f"{label}.margin must be non-negative")
            left = f"b{boat}_{metric}"
            right = f"b{other}_{metric}"
            operator = ">=" if op == "ge" else "<="
            threshold = "?" if op == "ge" else "(0 - ?)"
            filters.append(
                f"((({left} - {right}) {operator} {threshold}) "
                f"OR {left} IS NULL OR {right} IS NULL)"
            )
            params.append(margin)
            null_columns.update((left, right))
            restored_avg_st_condition = restored_avg_st_condition or metric == "avg_st"

    date_from = conditions.get("date_from")
    date_to = conditions.get("date_to")
    start = _iso_date(date_from, "date_from") if date_from is not None else None
    end = _iso_date(date_to, "date_to") if date_to is not None else None
    required_cutoffs: list[str] = []
    if history_condition:
        required_cutoffs.append(HISTORY_CUTOFF)
    if restored_accident_condition or restored_avg_st_condition:
        required_cutoffs.append(RESTORED_ACCIDENT_CUTOFF)
    if required_cutoffs:
        cutoff = max(required_cutoffs)
        if start is None or start < cutoff:
            start = cutoff
    if start is not None:
        filters.append("race_date >= ?")
        params.append(start)
    if end is not None:
        filters.append("race_date <= ?")
        params.append(end)
    if start is not None and end is not None and start > end:
        raise ValueError("date_from must not be after date_to")

    where = " AND ".join(filters) if filters else "1=1"
    # Keep the final slot for internal callers that predate Step 12.  Retired
    # odds conditions are rejected above, so it is always empty.
    return where, params, sorted(null_columns), bet, None


def _percentile(values: np.ndarray, percentile: float) -> float:
    return float(np.percentile(values, percentile))


def _bootstrap_ci(returns: Sequence[float], seed: int, iterations: int) -> tuple[float, float]:
    values, counts = np.unique(np.asarray(returns, dtype=np.float64), return_counts=True)
    n = len(returns)
    point = float(np.mean(returns))
    rng = np.random.default_rng(seed)
    means = np.empty(iterations, dtype=np.float64)
    probabilities = counts.astype(np.float64) / n
    # Multinomial counts are exactly equivalent to row-wise resampling, while
    # batching bounds memory when a fixture has many distinct payout values.
    batch = max(1, min(iterations, 2_000_000 // max(1, len(values))))
    for offset in range(0, iterations, batch):
        size = min(batch, iterations - offset)
        sampled_counts = rng.multinomial(n, probabilities, size=size)
        means[offset : offset + size] = sampled_counts @ values / n
    low = min(_percentile(means, 2.5), point)
    high = max(_percentile(means, 97.5), point)
    return low, high


def _normal_ci(returns: Sequence[float]) -> tuple[float, float]:
    point = float(np.mean(returns))
    if len(returns) < 2:
        return point, point
    standard_error = float(np.std(returns, ddof=1)) / math.sqrt(len(returns))
    return max(0.0, point - 1.96 * standard_error), point + 1.96 * standard_error


def _downsample_curve(
    points: list[dict[str, Any]], max_points: int = 200
) -> list[dict[str, Any]]:
    """Evenly downsample a dated curve while retaining both endpoints."""

    if len(points) <= max_points:
        return points
    indexes = [round(index * (len(points) - 1) / (max_points - 1)) for index in range(max_points)]
    return [points[index] for index in indexes]


def search_roi(
    db_path: str | Path,
    conditions: Mapping[str, Any] | None = None,
    *,
    fast: bool = False,
    seed: int = 42,
    bootstrap_iterations: int = 1000,
    include_profit_curve: bool = False,
) -> dict[str, Any]:
    """Search a Step 1 SQLite snapshot without ever opening it writable.

    Payout values are JPY returned per JPY 100 ticket, as preserved by Step 1.
    Bootstrap work is in Python.  When requested, ``profit_curve`` is the
    date-ordered cumulative profit for one fixed JPY 100 ticket per race.
    """

    if conditions is None:
        conditions = {}
    if not isinstance(conditions, Mapping):
        raise ValueError("conditions must be an object")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    if isinstance(bootstrap_iterations, bool) or not isinstance(bootstrap_iterations, int) or bootstrap_iterations <= 0:
        raise ValueError("bootstrap_iterations must be a positive integer")
    where, params, null_columns, bet, _unused_odds = _compile_conditions(conditions)
    null_expression = " OR ".join(f"{column} IS NULL" for column in null_columns) or "0"
    resolved = Path(db_path).resolve()
    uri = resolved.as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(asof_race_features)")
        }
        result_json_column = f"{bet.result_column}_json"
        payout_json_column = f"{bet.payout_column}_json"
        result_json_sql = result_json_column if result_json_column in columns else "NULL"
        payout_json_sql = payout_json_column if payout_json_column in columns else "NULL"
        sql = (
            f"SELECT race_date, schema_version, {bet.result_column} AS result_value, "
            f"{bet.payout_column} AS payout_value, "
            f"{result_json_sql} AS result_values_json, "
            f"{payout_json_sql} AS payout_values_json, "
            f"CASE WHEN {null_expression} THEN 1 ELSE 0 END AS condition_null "
            f"FROM asof_race_features AS asof WHERE {where}"
        )
        rows = conn.execute(sql, params).fetchall()

    excluded_condition = 0
    excluded_result = 0
    included: list[tuple[str, bool, float]] = []
    # 点ごとの成績。合算だけ出すと「どの目が足を引っ張っているか」が見えない。
    ticket_hits: dict[int | str, int] = {ticket: 0 for ticket in bet.expected}
    ticket_payout: dict[int | str, float] = {ticket: 0.0 for ticket in bet.expected}
    for race_date, schema_version, result, payout, result_json, payout_json, condition_null in rows:
        if condition_null:
            excluded_condition += 1
            continue
        if int(schema_version) >= 4:
            try:
                winning_values = json.loads(result_json)
                payout_values = json.loads(payout_json)
                if (
                    not isinstance(winning_values, list)
                    or not winning_values
                    or not isinstance(payout_values, dict)
                    or any(not isinstance(value, str) for value in winning_values)
                    or any(value not in payout_values for value in winning_values)
                ):
                    raise ValueError("invalid winning-ticket payload")
                payout_values = {
                    key: float(value) for key, value in payout_values.items()
                }
            except (TypeError, ValueError, json.JSONDecodeError):
                excluded_result += 1
                continue
            race_payout = 0.0
            hit = False
            # 同着があると winning_values は複数入るので、指定した点のうち
            # 当たったものを全部足す。
            for ticket in bet.expected:
                key = str(ticket)
                if key in winning_values:
                    hit = True
                    amount = payout_values[key]
                    race_payout += amount
                    ticket_hits[ticket] += 1
                    ticket_payout[ticket] += amount
            included.append((str(race_date), hit, race_payout))
        else:
            if result is None or payout is None:
                excluded_result += 1
                continue
            # schema_version < 4 は代表 1 件しか持たないレガシー列。
            race_payout = 0.0
            hit = False
            for ticket in bet.expected:
                if result == ticket:
                    hit = True
                    race_payout += float(payout)
                    ticket_hits[ticket] += 1
                    ticket_payout[ticket] += float(payout)
            included.append((str(race_date), hit, race_payout))

    ticket_count = bet.ticket_count
    # returns を「1 点あたりに均した払戻」にしておくと、平均がそのまま ROI% に
    # なり、信頼区間の計算 (_bootstrap_ci / _normal_ci) を一切変えずに済む。
    # 点数 1 のときは従来と同じ値。
    returns = [item[2] / ticket_count for item in included]
    n = len(included)
    hits = sum(item[1] for item in included)
    roi = float(np.mean(returns)) if returns else 0.0
    if not returns:
        ci_low = ci_high = 0.0
    elif fast:
        ci_low, ci_high = _normal_ci(returns)
    else:
        ci_low, ci_high = _bootstrap_ci(returns, seed, bootstrap_iterations)

    yearly_totals: dict[int, list[float]] = {}
    monthly_totals: dict[tuple[int, int], list[float]] = {}
    daily_profit: dict[str, float] = {}
    for race_date, hit, payout in included:
        year = int(race_date[:4])
        month = int(race_date[5:7])
        for totals in (
            yearly_totals.setdefault(year, [0.0, 0.0, 0.0]),
            monthly_totals.setdefault((year, month), [0.0, 0.0, 0.0]),
        ):
            totals[0] += 1
            totals[1] += int(hit)
            totals[2] += payout
        # 1 レースの投資額は 100 円 × 点数。
        stake = STAKE_PER_TICKET * ticket_count
        daily_profit[race_date] = daily_profit.get(race_date, 0.0) + payout - stake

    # ROI% = 払戻合計 ÷ (レース数 × 点数)。点数 1 なら従来の式と一致する。
    yearly = [
        {
            "year": year,
            "n": int(totals[0]),
            "hits": int(totals[1]),
            "roi": round(totals[2] / (totals[0] * ticket_count), 1),
        }
        for year, totals in sorted(yearly_totals.items())
    ]
    monthly = [
        {
            "year": year,
            "month": month,
            "n": int(totals[0]),
            "hits": int(totals[1]),
            "roi": round(totals[2] / (totals[0] * ticket_count), 1),
        }
        for (year, month), totals in sorted(monthly_totals.items())
    ]
    warnings: list[str] = []
    if n < 30:
        warnings.append("n<30: 偶然の可能性が高い")
    elif n < 100:
        warnings.append("n<100: 上振れの可能性")
    effective_range: list[str | None] = [
        min((item[0] for item in included), default=None),
        max((item[0] for item in included), default=None),
    ]
    # 点ごとの内訳。各点は毎レース 100 円ずつなので、その点の ROI は
    # 「その点の払戻合計 ÷ レース数」。内訳 ROI の平均が合算 ROI に一致する。
    ticket_breakdown = [
        {
            "ticket": str(ticket),
            "hits": ticket_hits[ticket],
            "hit_rate": round(ticket_hits[ticket] * 100.0 / n, 1) if n else 0.0,
            "roi": round(ticket_payout[ticket] / n, 1) if n else 0.0,
        }
        for ticket in bet.expected
    ]
    result = {
        "n": n,
        "hits": hits,
        # 的中率はレース単位。複数点のうち 1 点でも当たれば的中として数える。
        "hit_rate": round(hits * 100.0 / n, 1) if n else 0.0,
        "roi": round(roi, 1),
        "ticket_count": ticket_count,
        "stake_total": int(n * ticket_count * STAKE_PER_TICKET),
        "tickets": [str(ticket) for ticket in bet.expected],
        "ticket_breakdown": ticket_breakdown,
        "roi_ci_low": round(ci_low, 1),
        "roi_ci_high": round(ci_high, 1),
        "excluded": {"result_missing": excluded_result, "condition_null": excluded_condition},
        "yearly": yearly,
        "monthly": monthly,
        "warnings": warnings,
        "effective_date_range": effective_range,
    }
    if include_profit_curve:
        cumulative = 0.0
        curve: list[dict[str, Any]] = []
        for race_date in sorted(daily_profit):
            cumulative += daily_profit[race_date]
            rounded = round(cumulative, 1)
            curve.append(
                {
                    "date": race_date,
                    "cumulative": int(rounded) if rounded.is_integer() else rounded,
                }
            )
        result["profit_curve"] = _downsample_curve(curve)
    return result


__all__ = ["search_roi"]
