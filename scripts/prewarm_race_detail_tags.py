"""Precompute race-detail display tags for every race on one date."""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
os.environ.setdefault("BOATRACE_TASK_TRIGGER", "render-prewarm")

from src.db.connection import connect as db_connect  # noqa: E402
from src.web.app import (  # noqa: E402
    JST,
    RACE_DETAIL_TAG_CACHE_VERSION,
    _accident_period_start_for_date,
    _accident_watch_map,
    _ace_motor_threshold,
    _race_detail_tag_cache_key,
    _safe_float,
    _write_json_cache,
)


def _placeholders(values: list[Any] | tuple[Any, ...]) -> str:
    return ",".join("?" for _ in values)


def _boat1_escape_stats_by_race(target_date: str, race_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not race_ids:
        return {}
    placeholders = _placeholders(race_ids)
    with db_connect() as conn:
        rows = conn.execute(
            f"""
            WITH current_boat1 AS (
                SELECT race_id, racer_number
                  FROM race_entries
                 WHERE race_id IN ({placeholders})
                   AND boat_number = 1
            )
            SELECT c.race_id,
                   COUNT(*) AS starts,
                   SUM(CASE WHEN rr.finishing_position = 1 THEN 1 ELSE 0 END) AS wins
              FROM current_boat1 c
              JOIN race_entries e
                ON e.racer_number = c.racer_number
              JOIN races r
                ON r.race_id = e.race_id
               AND r.race_date < ?
              JOIN race_results rr
                ON rr.race_id = e.race_id
               AND rr.boat_number = e.boat_number
             WHERE COALESCE(NULLIF(rr.course_number, 0), e.boat_number) = 1
               AND rr.finishing_position IS NOT NULL
             GROUP BY c.race_id
            """,
            (*race_ids, target_date),
        ).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for race_id, starts_raw, wins_raw in rows:
        starts = int(starts_raw or 0)
        wins = int(wins_raw or 0)
        if starts > 0:
            out[str(race_id)] = {
                "starts": starts,
                "wins": wins,
                "rate": wins / starts * 100.0,
            }
    return out


def prewarm(target_date: str) -> dict[str, int]:
    with db_connect() as conn:
        race_rows = conn.execute(
            """
            SELECT race_id, stadium_number, race_date
              FROM races
             WHERE race_date = ?
             ORDER BY stadium_number, race_number
            """,
            (target_date,),
        ).fetchall()
        race_ids = [str(row[0]) for row in race_rows]
        entries = []
        if race_ids:
            entries = conn.execute(
                f"""
                SELECT race_id, boat_number, racer_number, assigned_motor_top_2_percent
                  FROM race_entries
                 WHERE race_id IN ({_placeholders(race_ids)})
                 ORDER BY race_id, boat_number
                """,
                tuple(race_ids),
            ).fetchall()

    summary = {"races": len(race_rows), "cached": 0, "failed": 0}
    if not race_rows:
        print(f"[race-detail-tags] date={target_date} {summary}", flush=True)
        return summary

    race_info = {
        str(race_id): {"stadium_number": int(stadium_number), "race_date": str(race_date)}
        for race_id, stadium_number, race_date in race_rows
    }
    entries_by_race: dict[str, list[tuple[Any, ...]]] = {}
    racers = set()
    for row in entries:
        entries_by_race.setdefault(str(row[0]), []).append(row)
        if row[2] is not None:
            racers.add(int(row[2]))

    period_start = _accident_period_start_for_date(target_date)
    try:
        accident_by_racer = _accident_watch_map(period_start, target_date, tuple(sorted(racers)))
    except Exception as exc:  # noqa: BLE001
        accident_by_racer = {}
        print(
            f"[race-detail-tags] accident bulk lookup failed error={type(exc).__name__}: {exc}",
            flush=True,
        )

    ace_threshold_by_stadium: dict[int, Any] = {}
    for stadium_number in sorted({info["stadium_number"] for info in race_info.values()}):
        try:
            ace_threshold_by_stadium[stadium_number] = _ace_motor_threshold(stadium_number, target_date)
        except Exception as exc:  # noqa: BLE001
            ace_threshold_by_stadium[stadium_number] = None
            print(
                f"[race-detail-tags] ace threshold failed stadium={stadium_number} "
                f"error={type(exc).__name__}: {exc}",
                flush=True,
            )

    escape_by_race = _boat1_escape_stats_by_race(target_date, race_ids)
    print(
        f"[race-detail-tags] start date={target_date} races={len(race_rows)} "
        f"entries={len(entries)} racers={len(racers)}",
        flush=True,
    )

    for idx, race_id in enumerate(race_ids, start=1):
        try:
            info = race_info.get(race_id)
            race_entries = entries_by_race.get(race_id, [])
            if not info or not race_entries:
                summary["failed"] += 1
                continue

            ace_threshold = ace_threshold_by_stadium.get(int(info["stadium_number"]))
            boat1_escape = escape_by_race.get(race_id)
            boats: dict[str, dict[str, Any]] = {}
            for _, boat_number, racer_number, motor_rate_raw in race_entries:
                boat: dict[str, Any] = {}
                accident = accident_by_racer.get(int(racer_number)) if racer_number is not None else None
                if accident:
                    rate = float(accident["rate"])
                    boat.update(
                        {
                            "accident_rate": rate,
                            "accident_points": int(accident["points"]),
                            "accident_starts": int(accident["starts"]),
                            "has_accident_watch": rate >= 0.7,
                            "accident_display_level": (
                                "high" if rate >= 0.7 else "watch" if rate >= 0.5 else None
                            ),
                        }
                    )
                motor_rate = _safe_float(motor_rate_raw)
                boat["is_ace_motor"] = bool(
                    motor_rate is not None
                    and ace_threshold is not None
                    and motor_rate >= ace_threshold
                )
                if int(boat_number) == 1 and boat1_escape:
                    escape_rate = _safe_float(boat1_escape.get("rate"))
                    if escape_rate is not None and escape_rate >= 70.0:
                        boat["escape_tag"] = {
                            "label": "逃げ",
                            "rate": round(float(escape_rate), 1),
                            "wins": int(boat1_escape.get("wins") or 0),
                            "starts": int(boat1_escape.get("starts") or 0),
                        }
                boats[str(int(boat_number))] = boat

            payload = {
                "version": RACE_DETAIL_TAG_CACHE_VERSION,
                "race_id": race_id,
                "race_date": target_date,
                "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
                "ace_motor_threshold": ace_threshold,
                "boats": boats,
            }
            _write_json_cache(_race_detail_tag_cache_key(race_id), payload)
            summary["cached"] += 1
            if idx == 1 or idx % 25 == 0 or idx == len(race_ids):
                print(
                    f"[race-detail-tags] progress {idx}/{len(race_ids)} "
                    f"cached={summary['cached']} failed={summary['failed']}",
                    flush=True,
                )
        except Exception as exc:  # noqa: BLE001
            summary["failed"] += 1
            print(
                f"[race-detail-tags] failed race_id={race_id} "
                f"error={type(exc).__name__}: {exc}",
                flush=True,
            )
    print(f"[race-detail-tags] date={target_date} {summary}", flush=True)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=date.today().isoformat())
    args = parser.parse_args()
    summary = prewarm(args.date)
    return 0 if summary["races"] > 0 and summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
