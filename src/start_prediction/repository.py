"""Persistence for immutable start predictions and their evaluations."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from .schema import SCHEMA_SQL


DERIVED_START_STATS_SQL = """
CREATE TABLE IF NOT EXISTS derived_start_stats (
  race_id TEXT NOT NULL,
  boat_number INTEGER NOT NULL,
  derived_avg_start_timing_180d REAL,
  derived_start_count_180d INTEGER NOT NULL DEFAULT 0,
  derived_avg_start_timing_12 REAL,
  derived_start_count_12 INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (race_id, boat_number)
)
"""
_POSTGRES_SCHEMA_READY = False


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _loads(value: Any, default):
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _dicts(cur) -> list[dict[str, Any]]:
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


class StartPredictionRepository:
    def __init__(self, conn):
        self.conn = conn

    def ensure_schema(self) -> None:
        global _POSTGRES_SCHEMA_READY
        if isinstance(self.conn, sqlite3.Connection):
            self.conn.executescript(SCHEMA_SQL)
            self.conn.commit()
            return
        if getattr(self.conn, "_kind", "") == "postgres":
            if _POSTGRES_SCHEMA_READY:
                return
            try:
                self.conn.execute("SELECT 1 FROM derived_start_stats LIMIT 0")
                _POSTGRES_SCHEMA_READY = True
                return
            except Exception:
                pass
            self.conn.execute(DERIVED_START_STATS_SQL)
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_derived_start_stats_race "
                "ON derived_start_stats(race_id)"
            )
            self.conn.commit()
            _POSTGRES_SCHEMA_READY = True

    def register_models(self, versions: dict[str, str], feature_names: list[str]) -> None:
        """Register the exact components used by an immutable prediction."""
        for component, version in versions.items():
            if component == "bundle":
                continue
            existing = self.conn.execute(
                "SELECT model_version FROM start_prediction_models WHERE model_version=?",
                (version,),
            ).fetchone()
            if existing:
                continue
            self.conn.execute(
                """INSERT INTO start_prediction_models
                   (model_version,component,model_kind,feature_names,parameters,metrics,is_active)
                   VALUES (?,?,?,?,?,?,?)""",
                (version, component, "rule_monte_carlo_v1", _json(feature_names),
                 _json({"bundle": versions.get("bundle")}), "{}", True),
            )
        self.conn.commit()

    def get(self, race_id: str, stage: str, bundle: str) -> dict[str, Any] | None:
        rows = _dicts(self.conn.execute(
            """SELECT * FROM race_start_predictions
                WHERE race_id=? AND prediction_stage=? AND model_bundle_version=?""",
            (race_id, stage, bundle),
        ))
        if not rows:
            return None
        return self._hydrate(rows[0])

    def get_latest(self, race_id: str, stage: str | None = None) -> dict[str, Any] | None:
        sql = "SELECT * FROM race_start_predictions WHERE race_id=?"
        params: tuple[Any, ...] = (race_id,)
        if stage:
            sql += " AND prediction_stage=?"
            params += (stage,)
        sql += " ORDER BY predicted_at DESC LIMIT 1"
        rows = _dicts(self.conn.execute(sql, params))
        return self._hydrate(rows[0]) if rows else None

    def save(self, race_id: str, stage: str, cutoff: str, snapshot: dict[str, Any], prediction: dict[str, Any]) -> dict[str, Any]:
        bundle = prediction["model_versions"]["bundle"]
        existing = self.get(race_id, stage, bundle)
        if existing:
            return existing
        params = (
            race_id, stage, bundle, cutoff, _json(snapshot),
            prediction["primary_attack_boat"], prediction["primary_attack_style"],
            prediction["first_mark_boat"], prediction["first_mark_probability"],
            prediction["predicted_kimarite"], prediction["kimarite_probability"],
            prediction["confidence"], _json(prediction["reasons"]),
        )
        try:
            if getattr(self.conn, "_kind", "") == "postgres":
                cur = self.conn.execute(
                    """INSERT INTO race_start_predictions
                   (race_id,prediction_stage,model_bundle_version,feature_cutoff_at,input_snapshot,
                    primary_attack_boat,primary_attack_style,first_mark_boat,first_mark_probability,
                    predicted_kimarite,kimarite_probability,confidence,reasons)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING prediction_id""", params)
                prediction_id = int(cur.fetchone()[0])
            else:
                cur = self.conn.execute(
                """INSERT INTO race_start_predictions
                   (race_id,prediction_stage,model_bundle_version,feature_cutoff_at,input_snapshot,
                    primary_attack_boat,primary_attack_style,first_mark_boat,first_mark_probability,
                    predicted_kimarite,kimarite_probability,confidence,reasons)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", params)
                prediction_id = int(cur.lastrowid)
            self.conn.executemany(
                """INSERT INTO race_start_prediction_boats
               (prediction_id,boat_number,predicted_st,predicted_st_sigma,predicted_start_rank,
                start_top_probability,first_probability,second_probability,third_probability,
                attack_probability,attack_style,reasons)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                [(
                    prediction_id, b["boat_number"], b["predicted_st"], b["predicted_st_sigma"],
                    b["predicted_start_rank"], b["start_top_probability"], b["first_probability"],
                    b["second_probability"], b["third_probability"], b["attack_probability"],
                    b["attack_style"], _json(b["reasons"]),
                ) for b in prediction["boats"]],
            )
            scenarios = []
            for row in prediction["kimarite_scenarios"]:
                scenarios.append((prediction_id, "kimarite", row["key"], row["rank"], row["probability"], "{}"))
            for row in prediction["trifectas"]:
                scenarios.append((prediction_id, "trifecta", row["combination"], row["rank"], row["probability"], "{}"))
            self.conn.executemany(
                """INSERT INTO race_start_prediction_scenarios
               (prediction_id,scenario_kind,scenario_key,rank,probability,payload)
               VALUES (?,?,?,?,?,?)""", scenarios,
            )
            self.conn.commit()
        except Exception:
            # Roll back the whole parent/children unit. A concurrent request may
            # have won the unique race/stage/version insert.
            try:
                self.conn.rollback()
            except Exception:
                pass
            existing = self.get(race_id, stage, bundle)
            if existing:
                return existing
            raise
        return self.get(race_id, stage, bundle) or {}

    def save_evaluation(self, prediction_id: int, evaluation: dict[str, Any]) -> dict[str, Any]:
        existing = _dicts(self.conn.execute(
            "SELECT * FROM race_start_prediction_evaluations WHERE prediction_id=?", (prediction_id,)
        ))
        if existing:
            return self._evaluation(existing[0])
        self.conn.execute(
            """INSERT INTO race_start_prediction_evaluations
               (prediction_id,actual_first_boat,actual_kimarite,actual_start_top_boat,
                st_mae,st_rmse,st_mean_error,start_top_hit,start_top2_hit,first_mark_hit,
                kimarite_hit,winner_hit,trifecta_top3_hit,trifecta_top5_hit,trifecta_top10_hit,
                log_loss,brier_score,error_categories,actual_snapshot)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                prediction_id, evaluation.get("actual_first_boat"), evaluation.get("actual_kimarite"),
                evaluation.get("actual_start_top_boat"), evaluation.get("st_mae"), evaluation.get("st_rmse"),
                evaluation.get("st_mean_error"), evaluation.get("start_top_hit"), evaluation.get("start_top2_hit"),
                evaluation.get("first_mark_hit"), evaluation.get("kimarite_hit"), evaluation.get("winner_hit"),
                evaluation.get("trifecta_top3_hit"), evaluation.get("trifecta_top5_hit"),
                evaluation.get("trifecta_top10_hit"), evaluation.get("log_loss"), evaluation.get("brier_score"),
                _json(evaluation.get("error_categories", [])), _json(evaluation["actual_snapshot"]),
            ),
        )
        self.conn.execute(
            "UPDATE race_start_predictions SET status='evaluated', evaluated_at=CURRENT_TIMESTAMP WHERE prediction_id=?",
            (prediction_id,),
        )
        self.conn.commit()
        row = _dicts(self.conn.execute(
            "SELECT * FROM race_start_prediction_evaluations WHERE prediction_id=?", (prediction_id,)
        ))[0]
        return self._evaluation(row)

    def metrics_rows(self, date_from: str | None = None, date_to: str | None = None,
                     stadium_number: int | None = None, grade: int | None = None,
                     race_number: int | None = None,
                     model_version: str | None = None) -> list[dict[str, Any]]:
        where = ["1=1"]
        params: list[Any] = []
        if date_from:
            where.append("r.race_date>=?"); params.append(date_from)
        if date_to:
            where.append("r.race_date<=?"); params.append(date_to)
        if stadium_number:
            where.append("r.stadium_number=?"); params.append(stadium_number)
        if grade:
            where.append("r.race_grade_number=?"); params.append(grade)
        if race_number:
            where.append("r.race_number=?"); params.append(race_number)
        if model_version:
            where.append("p.model_bundle_version=?"); params.append(model_version)
        return _dicts(self.conn.execute(
            f"""SELECT p.*, r.race_date, r.stadium_number, r.race_number, r.race_grade_number,
                       e.st_mae,e.st_rmse,e.start_top_hit,e.winner_hit,e.kimarite_hit,
                       e.trifecta_top10_hit,e.actual_snapshot,e.error_categories,
                       s.scenario_key AS top_trifecta
                  FROM race_start_predictions p
                  JOIN races r ON r.race_id=p.race_id
                  LEFT JOIN race_start_prediction_evaluations e ON e.prediction_id=p.prediction_id
                  LEFT JOIN race_start_prediction_scenarios s
                    ON s.prediction_id=p.prediction_id
                   AND s.scenario_kind='trifecta' AND s.rank=1
                 WHERE {' AND '.join(where)} ORDER BY r.race_date DESC,r.race_id""", tuple(params)
        ))

    def _hydrate(self, head: dict[str, Any]) -> dict[str, Any]:
        pid = int(head["prediction_id"])
        input_snapshot = _loads(head.get("input_snapshot"), {})
        snapshot_boats = {
            int(row["boat_number"]): row
            for row in (input_snapshot.get("boats") or [])
            if row.get("boat_number") is not None
        }
        boats = _dicts(self.conn.execute(
            "SELECT * FROM race_start_prediction_boats WHERE prediction_id=? ORDER BY boat_number", (pid,)
        ))
        scenarios = _dicts(self.conn.execute(
            "SELECT * FROM race_start_prediction_scenarios WHERE prediction_id=? ORDER BY scenario_kind,rank", (pid,)
        ))
        for b in boats:
            b["reasons"] = _loads(b.get("reasons"), [])
            source = snapshot_boats.get(int(b["boat_number"]), {})
            b["exhibition_st"] = source.get("exhibition_st")
            b["exhibition_time"] = source.get("exhibition_time")
            b["historical_avg_st"] = (
                source.get("course_avg_st")
                if source.get("course_avg_st") is not None
                else source.get("derived_st_180d")
            )
        out = dict(head)
        out["input_snapshot"] = input_snapshot
        out["reasons"] = _loads(out.get("reasons"), [])
        out["boats"] = boats
        out["kimarite_scenarios"] = [s for s in scenarios if s["scenario_kind"] == "kimarite"]
        out["trifectas"] = [s for s in scenarios if s["scenario_kind"] == "trifecta"]
        evaluation = _dicts(self.conn.execute(
            "SELECT * FROM race_start_prediction_evaluations WHERE prediction_id=?", (pid,)
        ))
        out["evaluation"] = self._evaluation(evaluation[0]) if evaluation else None
        return out

    def actual_result(self, race_id: str) -> dict[str, Any] | None:
        rows = _dicts(self.conn.execute(
            """SELECT boat_number, finishing_position, course_number, start_timing, remarks, kimarite
                 FROM race_results WHERE race_id=? ORDER BY boat_number""",
            (race_id,),
        ))
        if len(rows) < 6:
            return None
        finishers = sorted(rows, key=lambda x: int(x.get("finishing_position") or 99))
        actual_order = [int(x["boat_number"]) for x in finishers[:3]]
        starts = {
            int(x["boat_number"]): float(x["start_timing"])
            for x in rows
            if x.get("start_timing") is not None
        }
        payouts = _dicts(self.conn.execute(
            "SELECT bet_type, combination, payout FROM race_payouts WHERE race_id=? ORDER BY bet_type, combination",
            (race_id,),
        ))
        actual_combo = "-".join(map(str, actual_order)) if len(actual_order) == 3 else None
        return {
            "race_id": race_id,
            "results": rows,
            "actual_order": actual_order,
            "actual_combo": actual_combo,
            "actual_first_boat": actual_order[0] if actual_order else None,
            "actual_start_top_boat": min(starts, key=starts.get) if starts else None,
            "actual_kimarite": str(finishers[0].get("kimarite") or "その他") if finishers else None,
            "payouts": payouts,
        }

    @staticmethod
    def _evaluation(row: dict[str, Any]) -> dict[str, Any]:
        out = dict(row)
        out["error_categories"] = _loads(out.get("error_categories"), [])
        out["actual_snapshot"] = _loads(out.get("actual_snapshot"), {})
        return out
