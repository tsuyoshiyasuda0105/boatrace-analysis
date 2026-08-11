"""Fail-closed program-source gate for cron downstream generation."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from scripts.audit_program_source_consistency import (
    AuditInputError,
    audit_program_source_consistency,
    classify_program_source_gate,
)
from src.collectors.official_manifest import fetch_official_race_manifest
from src.db.connection import connect as db_connect
from src.parsers.official_b import parse_b_text


def _official_program_path(target_date: date) -> Path:
    return config.OFFICIAL_PROGRAMS_DIR / f"B{target_date.strftime('%y%m%d')}.TXT"


def _openapi_program_path(target_date: date) -> Path:
    return config.OPENAPI_RAW_DIR / f"{target_date.isoformat()}_programs.json"


def _manifest_cache_path(target_date: date) -> Path:
    return config.RAW_DIR / "official_manifest" / f"{target_date.isoformat()}.json"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _db_program_counts(target_date: date) -> dict[str, int]:
    with db_connect() as conn:
        row = conn.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM races WHERE race_date = ?) AS races,
              (SELECT COUNT(*)
                 FROM race_entries e JOIN races r ON r.race_id = e.race_id
                WHERE r.race_date = ?) AS entries,
              (SELECT COUNT(*)
                 FROM race_entries e JOIN races r ON r.race_id = e.race_id
                WHERE r.race_date = ?
                  AND e.racer_number IS NOT NULL
                  AND e.assigned_motor_number IS NOT NULL
                  AND e.assigned_motor_top_2_percent IS NOT NULL) AS detail_entries
            """,
            (target_date.isoformat(), target_date.isoformat(), target_date.isoformat()),
        ).fetchone()
    return {
        "races": int(row[0] or 0),
        "entries": int(row[1] or 0),
        "detail_entries": int(row[2] or 0),
    }


def _load_or_fetch_manifest(target_date: date) -> dict[str, Any]:
    cache_path = _manifest_cache_path(target_date)
    if cache_path.exists():
        try:
            cached = _read_json(cache_path)
        except (OSError, json.JSONDecodeError):
            return {"status": "parse_error", "expected_payload": None}
        if (
            isinstance(cached, dict)
            and cached.get("target_date") == target_date.isoformat()
            and cached.get("status") == "available"
            and cached.get("expected_payload")
        ):
            return cached
        return {"status": "parse_error", "expected_payload": None}

    fetched = fetch_official_race_manifest(target_date)
    if fetched.get("status") == "available":
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(fetched, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return fetched


def check_program_source_gate(target_date: date) -> dict[str, Any]:
    official_path = _official_program_path(target_date)
    openapi_path = _openapi_program_path(target_date)

    try:
        official_payload = parse_b_text(
            official_path.read_bytes().decode("cp932", errors="replace"),
            target_date,
        )
    except FileNotFoundError:
        official_payload = []
    except (OSError, ValueError, UnicodeError) as exc:
        return {
            "gate_status": "blocked",
            "target_date": target_date.isoformat(),
            "reason": f"official_b_{type(exc).__name__}",
        }

    openapi_state = "available"
    try:
        openapi_payload = _read_json(openapi_path)
    except FileNotFoundError:
        openapi_payload = {"programs": []}
        openapi_state = "unavailable"
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "gate_status": "blocked",
            "target_date": target_date.isoformat(),
            "reason": f"openapi_{type(exc).__name__}",
        }

    manifest = _load_or_fetch_manifest(target_date)
    manifest_state = str(manifest.get("status") or "http_error")
    expected_payload = manifest.get("expected_payload")
    try:
        report = audit_program_source_consistency(
            official_payload,
            openapi_payload,
            target_date=target_date.isoformat(),
            expected_payload=expected_payload,
        )
    except AuditInputError as exc:
        return {
            "gate_status": "blocked",
            "target_date": target_date.isoformat(),
            "reason": f"audit_{type(exc).__name__}",
        }

    gate_status = classify_program_source_gate(
        report,
        openapi_state=openapi_state,
        require_expected_manifest=True,
    )
    if manifest_state == "parse_error":
        gate_status = "blocked"
    elif manifest_state != "available" and gate_status != "blocked":
        gate_status = "retry_wait"

    summary = report["summary"]
    result = {
        "gate_status": gate_status,
        "target_date": target_date.isoformat(),
        "official_races": summary["official_race_count"],
        "openapi_races": summary["openapi_race_count"],
        "expected_races": summary["expected_race_count"],
        "issue_counts": summary["issue_counts"],
        "openapi_state": openapi_state,
        "manifest_state": manifest_state,
    }
    if gate_status in {"ready", "ready_with_warning"}:
        try:
            db_counts = _db_program_counts(target_date)
        except Exception as exc:
            result["gate_status"] = "blocked"
            result["reason"] = f"db_{type(exc).__name__}"
            return result
        expected_races = int(summary["expected_race_count"] or 0)
        expected_entries = expected_races * 6
        result["db_counts"] = db_counts
        if (
            db_counts["races"] != expected_races
            or db_counts["entries"] != expected_entries
            or db_counts["detail_entries"] != expected_entries
        ):
            result["gate_status"] = "blocked"
            result["reason"] = "db_program_incomplete"
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="target date in YYYY-MM-DD format")
    args = parser.parse_args(argv)
    try:
        target_date = date.fromisoformat(args.date)
    except ValueError:
        print(json.dumps({"gate_status": "input_error", "reason": "invalid_date"}))
        return 2

    result = check_program_source_gate(target_date)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if result["gate_status"] in {"ready", "ready_with_warning"}:
        return 0
    if result["gate_status"] == "retry_wait":
        return 3
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
