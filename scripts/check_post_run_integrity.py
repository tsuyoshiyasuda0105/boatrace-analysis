"""Validate operational data after cron jobs mutate race-detail dependencies.

This is intentionally lightweight and DB-backed.  A cron can finish without an
exception while still leaving stale or partial rows behind; these checks turn
that state into an explicit ``system_status`` entry and a non-zero exit code.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts.cache_racer_accident_rank_snapshot import accident_period_start_for_date  # noqa: E402
from scripts.rebuild_racer_accident_stats import RULE_VERSION  # noqa: E402
from src.db.connection import connect as db_connect  # noqa: E402
from src.web import app as web_app  # noqa: E402


MOTOR_CACHE_VERSION = "v9"
STATUS_ORDER = {"ok": 0, "warning": 1, "error": 2}
JST = ZoneInfo("Asia/Tokyo")


def _jst_now_naive() -> datetime:
    return datetime.now(JST).replace(tzinfo=None)


def _placeholders(values: Iterable[object]) -> str:
    return ",".join("?" for _ in values)


def _ensure_system_status(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS system_status (
          check_name TEXT NOT NULL,
          check_date TEXT NOT NULL,
          status TEXT NOT NULL,
          message TEXT,
          detail_json TEXT,
          checked_at TEXT,
          PRIMARY KEY (check_name, check_date)
        )
        """
    )
    conn.commit()


def _upsert_status(conn, check_name: str, check_date: str, status: str, message: str, detail: dict) -> None:
    _ensure_system_status(conn)
    now_iso = datetime.now().isoformat(timespec="seconds")
    detail_json = json.dumps(detail, ensure_ascii=False)
    row = conn.execute(
        "SELECT 1 FROM system_status WHERE check_name=? AND check_date=?",
        (check_name, check_date),
    ).fetchone()
    if row:
        conn.execute(
            """
            UPDATE system_status
               SET status=?, message=?, detail_json=?, checked_at=?
             WHERE check_name=? AND check_date=?
            """,
            (status, message, detail_json, now_iso, check_name, check_date),
        )
    else:
        conn.execute(
            """
            INSERT INTO system_status
              (check_name, check_date, status, message, detail_json, checked_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (check_name, check_date, status, message, detail_json, now_iso),
        )
    conn.commit()


def _race_ids_for_date(conn, target_date: str) -> list[str]:
    rows = conn.execute(
        "SELECT race_id FROM races WHERE race_date=? ORDER BY stadium_number, race_number",
        (target_date,),
    ).fetchall()
    return [str(row[0]) for row in rows]


def _select_race_ids(conn, target_date: str, race_ids: list[str] | None) -> list[str]:
    if race_ids:
        return [str(r) for r in race_ids]
    return _race_ids_for_date(conn, target_date)


def check_race_detail_rows(conn, target_date: str, race_ids: list[str] | None = None) -> tuple[str, str, dict]:
    target_races = _select_race_ids(conn, target_date, race_ids)
    if not target_races:
        return "error", "対象レースが0件です", {"race_count": 0}
    placeholders = _placeholders(target_races)
    rows = conn.execute(
        f"""
        SELECT r.race_id,
               COUNT(e.boat_number) AS entry_rows,
               SUM(CASE WHEN e.racer_number IS NOT NULL THEN 1 ELSE 0 END) AS racer_rows,
               SUM(CASE WHEN e.assigned_motor_number IS NOT NULL THEN 1 ELSE 0 END) AS motor_rows,
               SUM(CASE WHEN e.assigned_motor_top_2_percent IS NOT NULL THEN 1 ELSE 0 END) AS motor_rate_rows
          FROM races r
          LEFT JOIN race_entries e ON e.race_id = r.race_id
         WHERE r.race_id IN ({placeholders})
         GROUP BY r.race_id
         ORDER BY r.race_id
        """,
        tuple(target_races),
    ).fetchall()
    missing = []
    for race_id, entry_rows, racer_rows, motor_rows, motor_rate_rows in rows:
        if int(entry_rows or 0) < 6 or int(racer_rows or 0) < 6 or int(motor_rows or 0) < 6:
            missing.append(
                {
                    "race_id": str(race_id),
                    "entry_rows": int(entry_rows or 0),
                    "racer_rows": int(racer_rows or 0),
                    "motor_rows": int(motor_rows or 0),
                    "motor_rate_rows": int(motor_rate_rows or 0),
                }
            )
    detail = {
        "target_races": len(target_races),
        "checked_races": len(rows),
        "missing": missing[:20],
        "missing_count": len(missing),
    }
    if len(rows) != len(target_races):
        return "error", f"race_id照合に欠落があります {len(rows)}/{len(target_races)}", detail
    if missing:
        return "error", f"レース詳細の基礎データ欠落 {len(missing)}件", detail
    return "ok", f"レース詳細基礎データOK {len(target_races)}レース", detail


def check_race_detail_caches(conn, target_date: str, race_ids: list[str] | None = None) -> tuple[str, str, dict]:
    target_races = _select_race_ids(conn, target_date, race_ids)
    if not target_races:
        return "error", "対象レースが0件です", {"race_count": 0}
    page_keys = [web_app._race_detail_page_cache_key(race_id) for race_id in target_races]
    tag_keys = [web_app._race_detail_tag_cache_key(race_id) for race_id in target_races]
    all_keys = page_keys + tag_keys
    found: set[str] = set()
    for start in range(0, len(all_keys), 900):
        chunk = all_keys[start : start + 900]
        rows = conn.execute(
            f"SELECT cache_key FROM page_html_cache WHERE cache_key IN ({_placeholders(chunk)})",
            tuple(chunk),
    ).fetchall()
        found.update(str(row[0]) for row in rows)
    missing_pages = [key for key in page_keys if key not in found]
    missing_tags = [key for key in tag_keys if key not in found]
    detail = {
        "target_races": len(target_races),
        "expected_pages": len(page_keys),
        "expected_tags": len(tag_keys),
        "missing_pages": missing_pages[:20],
        "missing_tags": missing_tags[:20],
        "missing_pages_count": len(missing_pages),
        "missing_tags_count": len(missing_tags),
    }
    if missing_pages or missing_tags:
        return (
            "error",
            f"レース詳細キャッシュ欠落 page={len(missing_pages)} tag={len(missing_tags)}",
            detail,
        )
    return "ok", f"レース詳細キャッシュOK {len(target_races)}レース", detail


def check_motor_history_caches(conn, target_date: str, race_ids: list[str] | None = None) -> tuple[str, str, dict]:
    target_races = _select_race_ids(conn, target_date, race_ids)
    if not target_races:
        return "error", "対象レースが0件です", {"race_count": 0}
    motor_keys = [
        f"motor_history_{MOTOR_CACHE_VERSION}:{race_id}:{boat}"
        for race_id in target_races
        for boat in range(1, 7)
    ]
    found: set[str] = set()
    for start in range(0, len(motor_keys), 900):
        chunk = motor_keys[start : start + 900]
        rows = conn.execute(
            f"SELECT cache_key FROM page_html_cache WHERE cache_key IN ({_placeholders(chunk)})",
            tuple(chunk),
        ).fetchall()
        found.update(str(row[0]) for row in rows)
    missing = [key for key in motor_keys if key not in found]
    detail = {
        "target_races": len(target_races),
        "expected_motor_histories": len(motor_keys),
        "missing_motor_histories": missing[:20],
        "missing_motor_histories_count": len(missing),
    }
    if missing:
        return "error", f"モーター履歴キャッシュ欠落 {len(missing)}件", detail
    return "ok", f"モーター履歴キャッシュOK {len(motor_keys)}件", detail


def check_motor_history_caches(conn, target_date: str, race_ids: list[str] | None = None) -> tuple[str, str, dict]:
    target_races = _select_race_ids(conn, target_date, race_ids)
    if not target_races:
        return "error", "target races not found", {"race_count": 0}
    race_rows = conn.execute(
        f"""
        SELECT race_id, stadium_number, race_number
          FROM races
         WHERE race_id IN ({_placeholders(target_races)})
         ORDER BY stadium_number, race_number
        """,
        tuple(target_races),
    ).fetchall()
    race_meta = {
        str(row[0]): {"stadium_number": row[1], "race_number": row[2]}
        for row in race_rows
    }
    expected = [
        (race_id, boat, f"motor_history_{MOTOR_CACHE_VERSION}:{race_id}:{boat}")
        for race_id in target_races
        for boat in range(1, 7)
    ]
    payloads: dict[str, str] = {}
    for start in range(0, len(expected), 900):
        chunk = [key for _race_id, _boat, key in expected[start : start + 900]]
        rows = conn.execute(
            f"SELECT cache_key, html FROM page_html_cache WHERE cache_key IN ({_placeholders(chunk)})",
            tuple(chunk),
        ).fetchall()
        payloads.update({str(row[0]): row[1] for row in rows})

    missing: list[str] = []
    invalid: list[dict] = []
    missing_by_stadium: dict[str, int] = {}
    invalid_by_stadium: dict[str, int] = {}

    def add_count(bucket: dict[str, int], race_id: str) -> None:
        meta = race_meta.get(race_id) or {}
        stadium = str(meta.get("stadium_number") or "-")
        bucket[stadium] = bucket.get(stadium, 0) + 1

    for race_id, boat, key in expected:
        raw = payloads.get(key)
        if raw is None:
            missing.append(key)
            add_count(missing_by_stadium, race_id)
            continue
        try:
            payload = json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            invalid.append({"cache_key": key, "reason": f"json:{type(exc).__name__}"})
            add_count(invalid_by_stadium, race_id)
            continue
        if not isinstance(payload, dict):
            invalid.append({"cache_key": key, "reason": "not_object"})
            add_count(invalid_by_stadium, race_id)
            continue

        position_rows = payload.get("position_rows")
        position_boats: set[int] = set()
        if isinstance(position_rows, list):
            for row in position_rows:
                if not isinstance(row, dict):
                    continue
                try:
                    boat_no = int(row.get("boat_number") or 0)
                except (TypeError, ValueError):
                    boat_no = 0
                if 1 <= boat_no <= 6:
                    position_boats.add(boat_no)
        history_rows = payload.get("history")
        reasons: list[str] = []
        if "current" not in payload:
            reasons.append("missing_current")
        if len(position_boats) < 6:
            reasons.append(f"position_boats={len(position_boats)}")
        if not isinstance(history_rows, list) or not history_rows:
            reasons.append("empty_history")
        if reasons:
            meta = race_meta.get(race_id) or {}
            invalid.append(
                {
                    "cache_key": key,
                    "race_id": race_id,
                    "stadium_number": meta.get("stadium_number"),
                    "race_number": meta.get("race_number"),
                    "boat": boat,
                    "reason": ",".join(reasons),
                }
            )
            add_count(invalid_by_stadium, race_id)

    detail = {
        "target_races": len(target_races),
        "expected_motor_histories": len(expected),
        "missing_motor_histories": missing[:20],
        "missing_motor_histories_count": len(missing),
        "invalid_motor_histories": invalid[:20],
        "invalid_motor_histories_count": len(invalid),
        "missing_by_stadium": missing_by_stadium,
        "invalid_by_stadium": invalid_by_stadium,
    }
    if missing or invalid:
        return "error", f"motor history cache incomplete {len(missing) + len(invalid)} items", detail
    return "ok", f"motor history cache OK {len(expected)} items", detail


def check_accident_integrity(conn, target_date: str, _race_ids: list[str] | None = None) -> tuple[str, str, dict]:
    period_start = accident_period_start_for_date(target_date)
    preferred_source_row = conn.execute(
        """
        SELECT source_kind
          FROM racer_accident_period_stats
         WHERE period_start = ?
           AND source_kind IN ('official_external', 'reconstructed')
           AND rule_version = ?
         GROUP BY source_kind
         ORDER BY CASE WHEN source_kind = 'official_external' THEN 0 ELSE 1 END
         LIMIT 1
        """,
        (period_start, RULE_VERSION),
    ).fetchone()
    source_kind = str(preferred_source_row[0]) if preferred_source_row and preferred_source_row[0] else "reconstructed"
    period = conn.execute(
        """
        SELECT MAX(period_end), COUNT(*), MAX(updated_at)
          FROM racer_accident_period_stats
         WHERE period_start = ?
           AND source_kind = ?
           AND rule_version = ?
        """,
        (period_start, source_kind, RULE_VERSION),
    ).fetchone()
    period_end = str(period[0]) if period and period[0] else None
    period_rows = int(period[1] or 0) if period else 0
    snapshot = conn.execute(
        """
        SELECT MAX(snapshot_date), MAX(period_end), COUNT(*), MAX(updated_at)
          FROM racer_accident_rank_snapshots
         WHERE period_start = ?
           AND source_kind = ?
           AND source_rule_version = ?
        """,
        (period_start, source_kind, RULE_VERSION),
    ).fetchone()
    snapshot_date = str(snapshot[0]) if snapshot and snapshot[0] else None
    snapshot_end = str(snapshot[1]) if snapshot and snapshot[1] else None
    snapshot_rows = int(snapshot[2] or 0) if snapshot else 0
    invalid_rates = conn.execute(
        """
        SELECT COUNT(*)
          FROM racer_accident_period_stats
         WHERE period_start = ?
           AND source_kind = ?
           AND rule_version = ?
           AND (accident_rate < 0 OR accident_rate > 10)
        """,
        (period_start, source_kind, RULE_VERSION),
    ).fetchone()[0]
    detail = {
        "period_start": period_start,
        "period_end": period_end,
        "period_rows": period_rows,
        "snapshot_date": snapshot_date,
        "snapshot_period_end": snapshot_end,
        "snapshot_rows": snapshot_rows,
        "invalid_rate_rows": int(invalid_rates or 0),
        "rule_version": RULE_VERSION,
        "source_kind": source_kind,
    }
    if period_rows == 0:
        return "error", "事故率period_statsが0件です", detail
    if not period_end or period_end < target_date:
        return "error", f"事故率period_statsが古いです period_end={period_end}", detail
    if snapshot_rows == 0:
        return "error", "事故率ランキングsnapshotが0件です", detail
    if not snapshot_date or snapshot_date < target_date or not snapshot_end or snapshot_end < target_date:
        return "error", "事故率ランキングsnapshotが古いです", detail
    if invalid_rates:
        return "error", f"事故率の異常値があります {invalid_rates}件", detail
    return "ok", f"事故率OK period_rows={period_rows} snapshot_rows={snapshot_rows}", detail


CHECKS = {
    "detail_rows": check_race_detail_rows,
    "detail_cache": check_race_detail_caches,
    "motor_cache": check_motor_history_caches,
    "accident": check_accident_integrity,
}


def check_result_after_close(conn, target_date: str, race_ids: list[str] | None = None) -> tuple[str, str, dict]:
    target_races = _select_race_ids(conn, target_date, race_ids)
    if not target_races:
        return "ok", "no target races for result check", {"race_count": 0}
    cutoff = (_jst_now_naive() - timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S")
    placeholders = _placeholders(target_races)
    closed_rows = conn.execute(
        f"""
        SELECT r.race_id, r.stadium_number, r.race_number, r.race_closed_at
          FROM races r
         WHERE r.race_id IN ({placeholders})
           AND r.race_date = ?
           AND r.race_closed_at IS NOT NULL
           AND r.race_closed_at < ?
         ORDER BY r.stadium_number, r.race_number
        """,
        (*target_races, target_date, cutoff),
    ).fetchall()
    if not closed_rows:
        return "ok", "no races closed more than 15 minutes ago", {"race_count": len(target_races), "cutoff": cutoff}
    closed_ids = [str(row[0]) for row in closed_rows]
    result_rows = conn.execute(
        f"""
        SELECT rr.race_id, COUNT(*) AS result_rows
          FROM race_results rr
         WHERE rr.race_id IN ({_placeholders(closed_ids)})
           AND rr.finishing_position IS NOT NULL
         GROUP BY rr.race_id
        """,
        tuple(closed_ids),
    ).fetchall()
    result_counts = {str(row[0]): int(row[1] or 0) for row in result_rows}
    missing = [
        {
            "race_id": str(row[0]),
            "stadium": int(row[1]),
            "race_no": int(row[2]),
            "closed_at": str(row[3]),
            "result_rows": result_counts.get(str(row[0]), 0),
        }
        for row in closed_rows
        if result_counts.get(str(row[0]), 0) < 6
    ]
    detail = {
        "target_races": len(target_races),
        "closed_races": len(closed_rows),
        "missing_result_races": missing[:20],
        "missing_result_count": len(missing),
        "cutoff": cutoff,
    }
    if missing:
        coverage = (len(closed_rows) - len(missing)) / len(closed_rows) * 100.0
        status = "error" if coverage < 80.0 else "warning"
        return status, f"result rows incomplete {len(missing)}/{len(closed_rows)} closed races", detail
    return "ok", f"result rows OK {len(closed_rows)} closed races", detail


CHECKS["result"] = check_result_after_close

STAGE_SCOPES = {
    # Morning prewarm: source rows and caches should exist, but exhibition/result
    # data is not expected yet.
    "morning": ["detail_rows", "motor_cache", "detail_cache"],
    # Exhibition cron: validate only the races it touched. Missing exhibition
    # values themselves are not fatal because unsupported venues/sources exist.
    "exhibition": ["detail_rows", "motor_cache", "detail_cache"],
    # Result polling: only races closed at least 15 minutes ago are expected to
    # have complete result rows.
    "post-result": ["result"],
    # Nightly: accident stats are only strict after the full result day has run.
    "nightly": ["accident"],
}


def scopes_for_stage(stage: str) -> list[str]:
    return list(STAGE_SCOPES[stage])


def run_checks(target_date: str, scopes: list[str], race_ids: list[str] | None = None, *, persist: bool = True) -> dict:
    selected = list(CHECKS) if "all" in scopes else scopes
    results = []
    worst = "ok"
    with db_connect() as conn:
        for scope in selected:
            check = CHECKS[scope]
            try:
                status, message, detail = check(conn, target_date, race_ids)
            except Exception as exc:  # noqa: BLE001
                status = "error"
                message = f"{scope} check failed: {type(exc).__name__}: {exc}"
                detail = {"error": str(exc)}
            check_name = (
                f"post_run_{scope}_targeted"
                if race_ids is not None
                else f"post_run_{scope}"
            )
            if persist:
                _upsert_status(conn, check_name, target_date, status, message, detail)
            worst = max(worst, status, key=lambda s: STATUS_ORDER[s])
            results.append({"scope": scope, "status": status, "message": message, "detail": detail})
    return {"date": target_date, "status": worst, "results": results}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--stage", choices=sorted(STAGE_SCOPES), help="Use the safe check set for a cron timing.")
    parser.add_argument(
        "--scope",
        action="append",
        choices=["all", *CHECKS.keys()],
        default=[],
        help="Check scope. Can be specified multiple times.",
    )
    parser.add_argument("--race-id", action="append", default=[])
    parser.add_argument("--no-persist", action="store_true")
    args = parser.parse_args()
    scopes = scopes_for_stage(args.stage) if args.stage else args.scope or ["all"]
    summary = run_checks(
        args.date,
        scopes,
        args.race_id or None,
        persist=not args.no_persist,
    )
    if args.stage:
        summary["stage"] = args.stage
    print("[post-run-integrity] " + json.dumps(summary, ensure_ascii=False), flush=True)
    return 2 if summary["status"] == "error" else 1 if summary["status"] == "warning" else 0


if __name__ == "__main__":
    raise SystemExit(main())
