"""Compare official B-program parser output with an Open API programs payload.

This module is intentionally read-only.  It accepts already-fetched JSON data,
normalizes both source formats, and reports differences before either source is
written to the database.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


JST = timezone(timedelta(hours=9))
EXPECTED_BOATS = set(range(1, 7))


class AuditInputError(ValueError):
    """Raised when a fixture does not contain a supported program structure."""


def _first_value(item: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in item:
            return item[key]
    return None


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_deadline(value: Any) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    raw = str(value).strip()
    candidate = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return raw
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(JST).replace(tzinfo=None)
    return parsed.isoformat(sep=" ", timespec="seconds")


def _extract_official_races(payload: Any) -> Sequence[Mapping[str, Any]]:
    if isinstance(payload, list):
        races = payload
    elif isinstance(payload, Mapping):
        races = payload.get("races")
        if races is None:
            races = payload.get("programs")
    else:
        races = None
    if not isinstance(races, list):
        raise AuditInputError("official fixture must be a race list or contain 'races'")
    if not all(isinstance(race, Mapping) for race in races):
        raise AuditInputError("official fixture contains a non-object race")
    return races


def _extract_openapi_races(payload: Any) -> Sequence[Mapping[str, Any]]:
    if not isinstance(payload, Mapping):
        raise AuditInputError("Open API fixture must be a JSON object")
    races = payload.get("programs")
    if races is None:
        today = payload.get("today")
        races = today.get("programs") if isinstance(today, Mapping) else None
    if races is None:
        data = payload.get("data")
        races = data.get("programs") if isinstance(data, Mapping) else None
    if not isinstance(races, list):
        raise AuditInputError("Open API fixture does not contain a programs list")
    if not all(isinstance(race, Mapping) for race in races):
        raise AuditInputError("Open API fixture contains a non-object race")
    return races


def _extract_expected_items(payload: Any) -> Sequence[Mapping[str, Any]]:
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, Mapping):
        if "stadium_number" in payload and "race_numbers" in payload:
            items = [payload]
        else:
            items = payload.get("programs")
            if items is None:
                items = payload.get("races")
            if items is None:
                items = payload.get("stadiums")
    else:
        items = None
    if not isinstance(items, list):
        raise AuditInputError(
            "expected fixture must contain 'programs', 'races', 'stadiums', "
            "or a stadium manifest"
        )
    if not all(isinstance(item, Mapping) for item in items):
        raise AuditInputError("expected fixture contains a non-object item")
    return items


def _validate_natural_key(stadium: int | None, race_number: int | None, source: str) -> None:
    if stadium is None or race_number is None:
        raise AuditInputError(
            f"{source} race is missing a valid stadium_number or race_number"
        )
    if not 1 <= stadium <= 24 or not 1 <= race_number <= 12:
        raise AuditInputError(
            f"{source} race has an out-of-range natural key "
            f"{stadium:02d}-{race_number:02d}"
        )


def _normalize_expected(payload: Any) -> dict[tuple[int, int], dict[str, Any]]:
    normalized: dict[tuple[int, int], dict[str, Any]] = {}
    manifest_stadiums: set[int] = set()
    for item in _extract_expected_items(payload):
        stadium = _optional_int(
            _first_value(item, "stadium_number", "race_stadium_number")
        )
        if "race_numbers" in item:
            if stadium is None or not 1 <= stadium <= 24:
                raise AuditInputError("expected manifest has an out-of-range stadium_number")
            if stadium in manifest_stadiums:
                raise AuditInputError(
                    f"expected manifest contains duplicate stadium {stadium:02d}"
                )
            manifest_stadiums.add(stadium)
            race_numbers_raw = item.get("race_numbers")
            if not isinstance(race_numbers_raw, list) or not race_numbers_raw:
                raise AuditInputError(
                    f"expected manifest stadium {stadium:02d} race_numbers must be a non-empty list"
                )
            race_numbers = [_optional_int(value) for value in race_numbers_raw]
            if len(set(race_numbers)) != len(race_numbers):
                raise AuditInputError(
                    f"expected manifest stadium {stadium:02d} contains duplicate race numbers"
                )
            expanded = [
                (race_number, item.get("race_date")) for race_number in race_numbers
            ]
        else:
            expanded = [
                (
                    _optional_int(_first_value(item, "race_number")),
                    item.get("race_date"),
                )
            ]

        for race_number, race_date in expanded:
            _validate_natural_key(stadium, race_number, "expected")
            assert stadium is not None and race_number is not None
            key = (stadium, race_number)
            if key in normalized:
                raise AuditInputError(
                    f"expected contains duplicate race {stadium:02d}-{race_number:02d}"
                )
            normalized[key] = {"race_date": race_date}
    return normalized


def _normalize_races(
    races: Iterable[Mapping[str, Any]], source: str
) -> dict[tuple[int, int], dict[str, Any]]:
    normalized: dict[tuple[int, int], dict[str, Any]] = {}
    for race in races:
        stadium = _optional_int(
            _first_value(race, "stadium_number", "race_stadium_number")
        )
        race_number = _optional_int(_first_value(race, "race_number"))
        _validate_natural_key(stadium, race_number, source)
        assert stadium is not None and race_number is not None
        key = (stadium, race_number)
        if key in normalized:
            raise AuditInputError(
                f"{source} contains duplicate race {stadium:02d}-{race_number:02d}"
            )

        boats_raw = race.get("boats") or []
        if not isinstance(boats_raw, list):
            raise AuditInputError(
                f"{source} race {stadium:02d}-{race_number:02d} boats must be a list"
            )
        boats: list[dict[str, int | None]] = []
        for boat in boats_raw:
            if not isinstance(boat, Mapping):
                raise AuditInputError(
                    f"{source} race {stadium:02d}-{race_number:02d} has a non-object boat"
                )
            boats.append(
                {
                    "boat_number": _optional_int(
                        _first_value(boat, "boat_number", "racer_boat_number")
                    ),
                    "racer_number": _optional_int(_first_value(boat, "racer_number")),
                    "motor_number": _optional_int(
                        _first_value(
                            boat,
                            "assigned_motor_number",
                            "racer_assigned_motor_number",
                            "motor_number",
                        )
                    ),
                }
            )
        race_date = _first_value(race, "race_date")
        compact_date = str(race_date).replace("-", "") if race_date else None
        expected_race_id = (
            f"{compact_date}-{stadium:02d}-{race_number:02d}"
            if compact_date
            else None
        )
        supplied_race_id = _first_value(race, "race_id")
        if supplied_race_id and str(supplied_race_id) != expected_race_id:
            raise AuditInputError(
                f"{source} race_id does not match its natural key: "
                f"{supplied_race_id} != {expected_race_id}"
            )
        normalized[key] = {
            "race_date": race_date,
            "deadline": _normalize_deadline(
                _first_value(race, "race_closed_at", "closed_at", "deadline")
            ),
            "boats": boats,
        }
    return normalized


def _race_ref(key: tuple[int, int], race: Mapping[str, Any]) -> dict[str, Any]:
    stadium, race_number = key
    race_date = race.get("race_date")
    compact_date = str(race_date).replace("-", "") if race_date else None
    race_id = (
        f"{compact_date}-{stadium:02d}-{race_number:02d}"
        if compact_date
        else f"{stadium:02d}-{race_number:02d}"
    )
    return {
        "race_id": race_id,
        "stadium_number": stadium,
        "race_number": race_number,
    }


def _boat_issues(
    source: str,
    key: tuple[int, int],
    race: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    boat_numbers = [boat.get("boat_number") for boat in race["boats"]]
    valid_numbers = [n for n in boat_numbers if isinstance(n, int)]
    counts = Counter(valid_numbers)
    duplicates = sorted(number for number, count in counts.items() if count > 1)
    present = set(valid_numbers)
    ref = _race_ref(key, race)

    incomplete: list[dict[str, Any]] = []
    if len(race["boats"]) != 6 or present != EXPECTED_BOATS:
        incomplete.append(
            {
                **ref,
                "source": source,
                "boat_count": len(race["boats"]),
                "unique_boat_count": len(present),
                "missing_boat_numbers": sorted(EXPECTED_BOATS - present),
                "unexpected_boat_numbers": sorted(present - EXPECTED_BOATS),
            }
        )

    duplicate_issues: list[dict[str, Any]] = []
    if duplicates:
        duplicate_issues.append(
            {
                **ref,
                "source": source,
                "duplicate_boat_numbers": duplicates,
            }
        )
    return incomplete, duplicate_issues


def _boats_by_number(race: Mapping[str, Any]) -> dict[int, Mapping[str, Any]]:
    result: dict[int, Mapping[str, Any]] = {}
    for boat in race["boats"]:
        number = boat.get("boat_number")
        if isinstance(number, int) and number not in result:
            result[number] = boat
    return result


def audit_program_source_consistency(
    official_payload: Any,
    openapi_payload: Any,
    *,
    target_date: str | None = None,
    expected_payload: Any | None = None,
) -> dict[str, Any]:
    """Return a deterministic, JSON-serializable comparison report."""
    official = _normalize_races(_extract_official_races(official_payload), "official_b")
    openapi = _normalize_races(_extract_openapi_races(openapi_payload), "openapi")
    expected = _normalize_expected(expected_payload) if expected_payload is not None else {}

    official_stadiums = {key[0] for key in official}
    openapi_stadiums = {key[0] for key in openapi}
    source_empty = [
        {"source": source}
        for source, races in (("official_b", official), ("openapi", openapi))
        if not races
    ]
    stadium_missing = [
        {"stadium_number": stadium, "missing_from": "openapi", "present_in": "official_b"}
        for stadium in sorted(official_stadiums - openapi_stadiums)
    ] + [
        {"stadium_number": stadium, "missing_from": "official_b", "present_in": "openapi"}
        for stadium in sorted(openapi_stadiums - official_stadiums)
    ]

    race_missing: list[dict[str, Any]] = []
    for key in sorted(set(official) - set(openapi)):
        race_missing.append(
            {**_race_ref(key, official[key]), "missing_from": "openapi", "present_in": "official_b"}
        )
    for key in sorted(set(openapi) - set(official)):
        race_missing.append(
            {**_race_ref(key, openapi[key]), "missing_from": "official_b", "present_in": "openapi"}
        )

    expected_missing: list[dict[str, Any]] = []
    for key in sorted(set(expected) - (set(official) | set(openapi))):
        race_date = expected[key].get("race_date") or target_date
        expected_missing.append(
            {
                **_race_ref(key, {"race_date": race_date}),
                "missing_from": ["official_b", "openapi"],
            }
        )

    incomplete_boats: list[dict[str, Any]] = []
    duplicate_boat_numbers: list[dict[str, Any]] = []
    missing_required_fields: list[dict[str, Any]] = []
    invalid_dates: list[dict[str, Any]] = []
    for source, races in (("official_b", official), ("openapi", openapi)):
        for key, race in sorted(races.items()):
            incomplete, duplicates = _boat_issues(source, key, race)
            incomplete_boats.extend(incomplete)
            duplicate_boat_numbers.extend(duplicates)
            if target_date and race.get("race_date") != target_date:
                invalid_dates.append(
                    {
                        **_race_ref(key, race),
                        "source": source,
                        "expected_date": target_date,
                        "actual_date": race.get("race_date"),
                    }
                )
            for boat in race["boats"]:
                boat_number = boat.get("boat_number")
                if not isinstance(boat_number, int):
                    continue
                for field in ("racer_number", "motor_number"):
                    if boat.get(field) is None:
                        missing_required_fields.append(
                            {
                                **_race_ref(key, race),
                                "source": source,
                                "boat_number": boat_number,
                                "field": field,
                            }
                        )

    mismatches: list[dict[str, Any]] = []
    for key in sorted(set(official) & set(openapi)):
        official_race = official[key]
        openapi_race = openapi[key]
        ref = _race_ref(key, official_race)
        if official_race["race_date"] != openapi_race["race_date"]:
            mismatches.append(
                {
                    **ref,
                    "field": "race_date",
                    "official_b": official_race["race_date"],
                    "openapi": openapi_race["race_date"],
                }
            )
        if official_race["deadline"] != openapi_race["deadline"]:
            mismatches.append(
                {
                    **ref,
                    "field": "race_closed_at",
                    "official_b": official_race["deadline"],
                    "openapi": openapi_race["deadline"],
                }
            )

        official_boats = _boats_by_number(official_race)
        openapi_boats = _boats_by_number(openapi_race)
        for boat_number in sorted(set(official_boats) & set(openapi_boats)):
            for field in ("racer_number", "motor_number"):
                official_value = official_boats[boat_number].get(field)
                openapi_value = openapi_boats[boat_number].get(field)
                if official_value != openapi_value:
                    mismatches.append(
                        {
                            **ref,
                            "boat_number": boat_number,
                            "field": field,
                            "official_b": official_value,
                            "openapi": openapi_value,
                        }
                    )

    issues = {
        "source_empty": source_empty,
        "stadium_missing": stadium_missing,
        "race_missing": race_missing,
        "expected_missing": expected_missing,
        "incomplete_boats": incomplete_boats,
        "duplicate_boat_numbers": duplicate_boat_numbers,
        "missing_required_fields": missing_required_fields,
        "invalid_dates": invalid_dates,
        "mismatches": mismatches,
    }
    counts = {name: len(items) for name, items in issues.items()}
    issue_count = sum(counts.values())
    return {
        "status": "consistent" if issue_count == 0 else "inconsistent",
        "summary": {
            "official_stadium_count": len(official_stadiums),
            "openapi_stadium_count": len(openapi_stadiums),
            "official_race_count": len(official),
            "openapi_race_count": len(openapi),
            "expected_stadium_count": len({key[0] for key in expected}),
            "expected_race_count": len(expected),
            "issue_count": issue_count,
            "issue_counts": counts,
        },
        "issues": issues,
    }


def classify_program_source_gate(
    report: Mapping[str, Any],
    *,
    openapi_state: str = "available",
    require_expected_manifest: bool = True,
) -> str:
    """Classify an audit for downstream cron work without treating deadline revisions as fatal."""
    issues = report.get("issues") or {}
    summary = report.get("summary") or {}
    empty_sources = {
        str(item.get("source"))
        for item in issues.get("source_empty", [])
        if isinstance(item, Mapping)
    }

    if "official_b" in empty_sources:
        return "blocked"
    if require_expected_manifest and int(summary.get("expected_race_count", 0) or 0) == 0:
        return "retry_wait"
    if openapi_state != "available":
        return "retry_wait"

    blocking_issue_names = (
        "source_empty",
        "stadium_missing",
        "race_missing",
        "expected_missing",
        "incomplete_boats",
        "duplicate_boat_numbers",
        "missing_required_fields",
        "invalid_dates",
    )
    if any(issues.get(name) for name in blocking_issue_names):
        return "blocked"

    mismatches = issues.get("mismatches") or []
    if any(item.get("field") != "race_closed_at" for item in mismatches):
        return "blocked"
    if mismatches:
        return "ready_with_warning"
    return "ready"


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise AuditInputError(f"cannot read fixture: {path}") from exc
    except json.JSONDecodeError as exc:
        raise AuditInputError(f"invalid JSON fixture: {path}") from exc


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--official", required=True, type=Path, help="official B parser JSON")
    parser.add_argument("--openapi", required=True, type=Path, help="Open API programs JSON")
    parser.add_argument(
        "--expected",
        type=Path,
        help="optional expected stadium/race manifest JSON",
    )
    parser.add_argument("--date", help="expected race date in YYYY-MM-DD format")
    parser.add_argument("--output", type=Path, help="optional report path (stdout when omitted)")
    args = parser.parse_args(argv)

    try:
        report = audit_program_source_consistency(
            _load_json(args.official),
            _load_json(args.openapi),
            target_date=args.date,
            expected_payload=_load_json(args.expected) if args.expected else None,
        )
    except AuditInputError as exc:
        print(json.dumps({"status": "input_error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2

    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0 if report["status"] == "consistent" else 1


if __name__ == "__main__":
    raise SystemExit(main())
