from __future__ import annotations

import sqlite3

from scripts.evaluate_start_predictions import _load_due_predictions
from src.start_prediction.features import PointInTimeFeatureBuilder


def test_feature_builder_uses_database_specific_date_casts():
    sqlite_builder = PointInTimeFeatureBuilder(sqlite3.connect(":memory:"))
    assert sqlite_builder._date_sql("a.updated_at") == "DATE(a.updated_at)"

    class PostgresConnection:
        _kind = "postgres"

    postgres_builder = PointInTimeFeatureBuilder(PostgresConnection())
    assert postgres_builder._date_sql("a.updated_at") == "CAST(a.updated_at AS DATE)"


def test_due_predictions_preserves_stage_and_ignores_incomplete_results():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE races (race_id TEXT PRIMARY KEY, race_date TEXT);
        CREATE TABLE race_results (race_id TEXT, boat_number INTEGER);
        CREATE TABLE race_start_predictions (
            prediction_id INTEGER PRIMARY KEY,
            race_id TEXT,
            prediction_stage TEXT
        );
        CREATE TABLE race_start_prediction_evaluations (prediction_id INTEGER);
        """
    )
    conn.executemany(
        "INSERT INTO races VALUES (?, ?)",
        (("R1", "2026-07-30"), ("R2", "2026-07-30")),
    )
    conn.executemany(
        "INSERT INTO race_start_predictions VALUES (?, ?, ?)",
        (
            (1, "R1", "pre_exhibition"),
            (2, "R1", "post_exhibition"),
            (3, "R2", "post_exhibition"),
        ),
    )
    conn.executemany(
        "INSERT INTO race_results VALUES (?, ?)",
        [("R1", boat) for boat in range(1, 7)] + [("R2", boat) for boat in range(1, 6)],
    )

    assert _load_due_predictions(conn, "2026-07-30", "2026-07-30") == [
        ("R1", "post_exhibition"),
        ("R1", "pre_exhibition"),
    ]
