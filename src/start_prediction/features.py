"""Point-in-time feature construction for start/development predictions."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


def _rows(cur) -> list[dict[str, Any]]:
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def _one(cur) -> dict[str, Any] | None:
    rows = _rows(cur)
    return rows[0] if rows else None


def _f(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class RaceFeatureSnapshot:
    race: dict[str, Any]
    boats: list[dict[str, Any]]
    tide: dict[str, Any]
    market: dict[str, Any]
    stage: str
    feature_cutoff_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "race": self.race,
            "boats": self.boats,
            "tide": self.tide,
            "market": self.market,
            "stage": self.stage,
            "feature_cutoff_at": self.feature_cutoff_at,
            "feature_policy": "strictly_before_race_date_for_history_v1",
        }


class PointInTimeFeatureBuilder:
    """Build features with every historical query ending before target race_date."""

    def __init__(self, conn):
        self.conn = conn

    def build(self, race_id: str, stage: str = "post_exhibition") -> RaceFeatureSnapshot:
        race = _one(self.conn.execute(
            """SELECT race_id, race_date, stadium_number, race_number,
                      race_grade_number, race_title, race_subtitle, race_distance,
                      race_closed_at, series_day
                 FROM races WHERE race_id = ?""",
            (race_id,),
        ))
        if not race:
            raise LookupError(f"race not found: {race_id}")

        entries = _rows(self.conn.execute(
            """SELECT e.*, p.weather_number, p.wind_speed, p.wind_direction_number,
                      p.wave_height, p.temperature, p.water_temperature,
                      p.course_number AS exhibition_course_number,
                      p.exhibition_time, p.start_timing_exhibition,
                      p.stable_plate, p.live_updated_at,
                      d.derived_avg_start_timing_180d, d.derived_start_count_180d,
                      d.derived_avg_start_timing_12, d.derived_start_count_12,
                      (SELECT a.accident_points FROM racer_accident_period_stats a
                        WHERE a.racer_number=e.racer_number
                          AND a.period_start <= (SELECT race_date FROM races WHERE race_id=e.race_id)
                          AND a.period_end >= (SELECT race_date FROM races WHERE race_id=e.race_id)
                          AND DATE(a.updated_at) <= (SELECT race_date FROM races WHERE race_id=e.race_id)
                        ORDER BY a.updated_at DESC LIMIT 1) AS accident_points,
                      (SELECT a.accident_rate FROM racer_accident_period_stats a
                        WHERE a.racer_number=e.racer_number
                          AND a.period_start <= (SELECT race_date FROM races WHERE race_id=e.race_id)
                          AND a.period_end >= (SELECT race_date FROM races WHERE race_id=e.race_id)
                          AND DATE(a.updated_at) <= (SELECT race_date FROM races WHERE race_id=e.race_id)
                        ORDER BY a.updated_at DESC LIMIT 1) AS accident_rate
                 FROM race_entries e
                 LEFT JOIN race_previews p ON p.race_id=e.race_id AND p.boat_number=e.boat_number
                 LEFT JOIN derived_start_stats d ON d.race_id=e.race_id AND d.boat_number=e.boat_number
                WHERE e.race_id=? ORDER BY e.boat_number""",
            (race_id,),
        ))
        if len(entries) != 6:
            raise ValueError(f"race requires six entries: {race_id} has {len(entries)}")

        tide = _one(self.conn.execute(
            """SELECT tide_height_cm, tide_phase, minutes_from_high, minutes_from_low,
                      tide_range_cm, tide_delta_60m_cm, is_high_tide_zone,
                      is_low_tide_zone, source, fetched_at
                 FROM race_tides WHERE race_id=?""",
            (race_id,),
        )) or {}

        market = self._market_snapshot(race_id)

        histories = self._historical_racer_features(race, entries)
        motor_histories = self._historical_motor_features(race, entries)
        boats: list[dict[str, Any]] = []
        for entry in entries:
            boat = int(entry["boat_number"])
            hist = histories.get(boat, {})
            motor = motor_histories.get(boat, {})
            preview_allowed = stage == "post_exhibition"
            # Exhibition entry is not available at the pre-exhibition stage.
            course = int(entry.get("exhibition_course_number") or boat) if preview_allowed else boat
            item = {
                "boat_number": boat,
                "racer_number": entry.get("racer_number"),
                "racer_name": entry.get("racer_name"),
                "class_number": entry.get("class_number"),
                "course_number": course,
                "age": entry.get("age"),
                "weight": _f(entry.get("weight")),
                "flying_count": int(entry.get("flying_count") or 0),
                "late_count": int(entry.get("late_count") or 0),
                "entry_avg_st": _f(entry.get("avg_start_timing")),
                "derived_st_180d": _f(entry.get("derived_avg_start_timing_180d")),
                "derived_st_count_180d": int(entry.get("derived_start_count_180d") or 0),
                "derived_st_12": _f(entry.get("derived_avg_start_timing_12")),
                "derived_st_count_12": int(entry.get("derived_start_count_12") or 0),
                "course_avg_st": _f(hist.get("course_avg_st")),
                "course_st_count": int(hist.get("course_st_count") or 0),
                "st_std": _f(hist.get("st_std"), 0.045),
                "exhibition_to_actual_bias": _f(hist.get("exhibition_to_actual_bias"), 0.04),
                "course_win_rate": _f(hist.get("course_win_rate"), 0.0),
                "course_top2_rate": _f(hist.get("course_top2_rate"), 0.0),
                "course_top3_rate": _f(hist.get("course_top3_rate"), 0.0),
                "national_top1": _f(entry.get("national_top_1_percent"), 0.0),
                "national_top2": _f(entry.get("national_top_2_percent"), 0.0),
                "national_top3": _f(entry.get("national_top_3_percent"), 0.0),
                "local_top1": _f(entry.get("local_top_1_percent"), 0.0),
                "local_top2": _f(entry.get("local_top_2_percent"), 0.0),
                "local_top3": _f(entry.get("local_top_3_percent"), 0.0),
                "motor_number": entry.get("assigned_motor_number"),
                "published_motor_top2": _f(entry.get("assigned_motor_top_2_percent")),
                "published_motor_top3": _f(entry.get("assigned_motor_top_3_percent")),
                "motor_asof_top2": _f(motor.get("top2_rate")),
                "motor_asof_top3": _f(motor.get("top3_rate")),
                "motor_asof_starts": int(motor.get("starts_count") or 0),
                "accident_rate": _f(entry.get("accident_rate"), 0.0),
                "accident_points": int(entry.get("accident_points") or 0),
                "exhibition_time": _f(entry.get("exhibition_time")) if preview_allowed else None,
                "exhibition_st": _f(entry.get("start_timing_exhibition")) if preview_allowed else None,
                "weather_number": entry.get("weather_number"),
                "wind_speed": _f(entry.get("wind_speed"), 0.0),
                "wind_direction_number": entry.get("wind_direction_number"),
                "wave_height": _f(entry.get("wave_height"), 0.0),
                "temperature": _f(entry.get("temperature")),
                "water_temperature": _f(entry.get("water_temperature")),
                "stable_plate": bool(entry.get("stable_plate")),
            }
            boats.append(item)

        exhibit_times = [b["exhibition_time"] for b in boats if b["exhibition_time"] is not None]
        exhibit_sts = [b["exhibition_st"] for b in boats if b["exhibition_st"] is not None]
        for b in boats:
            b["exhibition_rank"] = (
                1 + sum(v < b["exhibition_time"] for v in exhibit_times)
                if b["exhibition_time"] is not None else None
            )
            b["exhibition_st_rank"] = (
                1 + sum(v < b["exhibition_st"] for v in exhibit_sts)
                if b["exhibition_st"] is not None else None
            )

        cutoff_values = [str(e.get("live_updated_at")) for e in entries if e.get("live_updated_at")]
        cutoff = max(cutoff_values) if cutoff_values and stage == "post_exhibition" else datetime.now().astimezone().isoformat()
        return RaceFeatureSnapshot(
            race=race, boats=boats, tide=tide, market=market,
            stage=stage, feature_cutoff_at=cutoff,
        )

    def _market_snapshot(self, race_id: str) -> dict[str, Any]:
        """Keep only a pre-result odds snapshot for later metrics filtering.

        Final odds remain evaluation data and are intentionally excluded.
        Older databases may not have the odds table, so this feature is optional.
        """
        try:
            rows = _rows(self.conn.execute(
                """SELECT combination, odds, recorded_at, snapshot_label
                     FROM odds_trifecta
                    WHERE race_id=? AND COALESCE(is_final,0)=0
                    ORDER BY recorded_at DESC, combination""",
                (race_id,),
            ))
        except Exception:
            return {}
        if not rows:
            return {}
        latest = str(rows[0]["recorded_at"])
        same_snapshot = [r for r in rows if str(r["recorded_at"]) == latest]
        return {
            "recorded_at": latest,
            "snapshot_label": same_snapshot[0].get("snapshot_label"),
            "trifecta_odds": {str(r["combination"]): _f(r.get("odds")) for r in same_snapshot},
        }

    def _historical_racer_features(self, race: dict[str, Any], entries: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
        out: dict[int, dict[str, Any]] = {}
        for entry in entries:
            course = int(entry.get("exhibition_course_number") or entry["boat_number"])
            row = _one(self.conn.execute(
                """SELECT COUNT(*) AS course_st_count,
                          AVG(rr.start_timing) AS course_avg_st,
                          AVG(rr.start_timing * rr.start_timing) AS st_sq,
                          AVG(CASE WHEN rr.finishing_position=1 THEN 1.0 ELSE 0.0 END)*100 AS course_win_rate,
                          AVG(CASE WHEN rr.finishing_position<=2 THEN 1.0 ELSE 0.0 END)*100 AS course_top2_rate,
                          AVG(CASE WHEN rr.finishing_position<=3 THEN 1.0 ELSE 0.0 END)*100 AS course_top3_rate,
                          AVG(rr.start_timing - rp.start_timing_exhibition) AS exhibition_to_actual_bias
                     FROM race_entries he
                     JOIN races hr ON hr.race_id=he.race_id
                     JOIN race_results rr ON rr.race_id=he.race_id AND rr.boat_number=he.boat_number
                     LEFT JOIN race_previews rp ON rp.race_id=he.race_id AND rp.boat_number=he.boat_number
                    WHERE he.racer_number=? AND hr.race_date < ?
                      AND COALESCE(rr.course_number, rp.course_number, he.boat_number)=?""",
                (entry["racer_number"], race["race_date"], course),
            )) or {}
            avg = _f(row.get("course_avg_st"))
            sq = _f(row.get("st_sq"))
            row["st_std"] = max(0.015, min(0.12, ((sq - avg * avg) ** 0.5))) if avg is not None and sq is not None and sq >= avg * avg else 0.045
            out[int(entry["boat_number"])] = row
        return out

    def _historical_motor_features(self, race: dict[str, Any], entries: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
        out: dict[int, dict[str, Any]] = {}
        for entry in entries:
            row = _one(self.conn.execute(
                """SELECT starts_count, top2_rate, top3_rate, through_race_date
                     FROM motor_cycle_stats
                    WHERE stadium_number=? AND motor_number=? AND through_race_date < ?
                    ORDER BY through_race_date DESC LIMIT 1""",
                (race["stadium_number"], entry.get("assigned_motor_number"), race["race_date"]),
            )) or {}
            out[int(entry["boat_number"])] = row
        return out
