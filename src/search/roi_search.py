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
DEFAULT_BET = {"type": "sanrentan", "first": 1, "second": 2, "third": 3}
SUPPORTED_SCHEMA_VERSIONS = (2, 3)
READABLE_SCHEMA_VERSIONS = (*SUPPORTED_SCHEMA_VERSIONS, 4)


@dataclass(frozen=True)
class _Bet:
    kind: str
    result_column: str
    payout_column: str
    expected: int | str


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


def _parse_bet(value: Any) -> _Bet:
    # The example ticket is the deterministic fallback when the optional bet
    # object is omitted.  Supplying a partial bet object is still an error.
    raw = DEFAULT_BET if value is None else _mapping(value, "bet")
    allowed = frozenset({"type", "first", "second", "third"})
    _known_keys(raw, allowed, "bet")
    kind = raw.get("type")
    if kind not in BET_LEGS:
        raise ValueError("bet.type must be tansho, nirentan, or sanrentan")
    leg_names = ("first", "second", "third")[: BET_LEGS[kind]]
    required = {"type", *leg_names}
    missing = sorted(key for key in required if raw.get(key) is None)
    if missing:
        raise ValueError(f"missing bet key(s): {', '.join(missing)}")
    surplus = sorted(key for key in ("first", "second", "third") if key not in leg_names and raw.get(key) is not None)
    if surplus:
        raise ValueError(f"unused bet key(s) for {kind}: {', '.join(surplus)}")
    legs = [_integer(raw[key], f"bet.{key}") for key in leg_names]
    if any(leg < 1 or leg > 6 for leg in legs) or len(set(legs)) != len(legs):
        raise ValueError("買い目は1〜6号艇から、着順ごとに異なる艇番を選んでください")
    expected: int | str = legs[0] if kind == "tansho" else "-".join(map(str, legs))
    return _Bet(kind, f"result_{kind}", f"payout_{kind}", expected)


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


def _compile_conditions(conditions: Mapping[str, Any]) -> tuple[str, list[Any], list[str], _Bet]:
    """Compile validated conditions into parameterized SQL.

    A comparison always evaluates the signed difference between two boats:
    ``value(boat) - value(other) >= margin`` for ``op='ge'`` and
    ``value(boat) - value(other) <= -margin`` for ``op='le'``.  ``margin``
    must be non-negative.  Metric-derived column names come only from the
    fixed whitelist above; every margin remains a bound SQL parameter.
    """

    _known_keys(conditions, TOP_LEVEL_KEYS, "condition")
    bet = _parse_bet(conditions.get("bet"))
    filters: list[str] = ["schema_version IN (?, ?, ?)"]
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
            value = _integer(value, key)
            if not 1 <= value <= 24:
                raise ValueError("venue must be from 1 through 24")
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
                    history_condition = history_condition or (key == "accident_rate" and active)
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
            missing = sorted(key for key in COMPARE_KEYS if raw.get(key) is None)
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
            margin = _number(raw["margin"], f"{label}.margin")
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

    date_from = conditions.get("date_from")
    date_to = conditions.get("date_to")
    start = _iso_date(date_from, "date_from") if date_from is not None else None
    end = _iso_date(date_to, "date_to") if date_to is not None else None
    if history_condition and (start is None or start < HISTORY_CUTOFF):
        start = HISTORY_CUTOFF
    if start is not None:
        filters.append("race_date >= ?")
        params.append(start)
    if end is not None:
        filters.append("race_date <= ?")
        params.append(end)
    if start is not None and end is not None and start > end:
        raise ValueError("date_from must not be after date_to")

    where = " AND ".join(filters) if filters else "1=1"
    return where, params, sorted(null_columns), bet


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


def search_roi(
    db_path: str | Path,
    conditions: Mapping[str, Any] | None = None,
    *,
    fast: bool = False,
    seed: int = 42,
    bootstrap_iterations: int = 1000,
) -> dict[str, Any]:
    """Search a Step 1 SQLite snapshot without ever opening it writable.

    Payout values are JPY returned per JPY 100 ticket, as preserved by Step 1.
    The function executes one joined-free SELECT; bootstrap work is in Python.
    """

    if conditions is None:
        conditions = {}
    if not isinstance(conditions, Mapping):
        raise ValueError("conditions must be an object")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    if isinstance(bootstrap_iterations, bool) or not isinstance(bootstrap_iterations, int) or bootstrap_iterations <= 0:
        raise ValueError("bootstrap_iterations must be a positive integer")
    where, params, null_columns, bet = _compile_conditions(conditions)
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
            f"FROM asof_race_features WHERE {where}"
        )
        rows = conn.execute(sql, params).fetchall()

    excluded_condition = 0
    excluded_result = 0
    included: list[tuple[str, bool, float]] = []
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
            expected = str(bet.expected)
            hit = expected in winning_values
            included.append(
                (str(race_date), hit, payout_values[expected] if hit else 0.0)
            )
        else:
            if result is None or payout is None:
                excluded_result += 1
                continue
            hit = result == bet.expected
            included.append((str(race_date), hit, float(payout) if hit else 0.0))

    returns = [item[2] for item in included]
    n = len(included)
    hits = sum(item[1] for item in included)
    roi = float(np.mean(returns)) if returns else 0.0
    if not returns:
        ci_low = ci_high = 0.0
    elif fast:
        ci_low, ci_high = _normal_ci(returns)
    else:
        ci_low, ci_high = _bootstrap_ci(returns, seed, bootstrap_iterations)

    yearly: list[dict[str, Any]] = []
    for year in sorted({int(item[0][:4]) for item in included}):
        year_rows = [item for item in included if int(item[0][:4]) == year]
        yearly.append(
            {
                "year": year,
                "n": len(year_rows),
                "hits": sum(item[1] for item in year_rows),
                "roi": round(sum(item[2] for item in year_rows) / len(year_rows), 1),
            }
        )
    warnings: list[str] = []
    if n < 30:
        warnings.append("n<30: 偶然の可能性が高い")
    elif n < 100:
        warnings.append("n<100: 上振れの可能性")
    effective_range: list[str | None] = [
        min((item[0] for item in included), default=None),
        max((item[0] for item in included), default=None),
    ]
    return {
        "n": n,
        "hits": hits,
        "hit_rate": round(hits * 100.0 / n, 1) if n else 0.0,
        "roi": round(roi, 1),
        "roi_ci_low": round(ci_low, 1),
        "roi_ci_high": round(ci_high, 1),
        "excluded": {"result_missing": excluded_result, "condition_null": excluded_condition},
        "yearly": yearly,
        "warnings": warnings,
        "effective_date_range": effective_range,
    }


__all__ = ["search_roi"]
