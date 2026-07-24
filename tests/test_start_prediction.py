from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from flask import Flask

from src.start_prediction.evaluation import evaluate_prediction
from src.start_prediction.features import PointInTimeFeatureBuilder
from src.start_prediction.models import MODEL_VERSIONS, RuleEnsembleV1
from src.start_prediction.repository import StartPredictionRepository
from src.start_prediction.service import StartPredictionService
from src.start_prediction.strategy_filters import (
    StrategyBet,
    StrategyCandidate,
    parse_strategy_bets,
    pass_combo_in_trifecta_top,
    pass_head_first_probability,
)
from src.web.start_prediction_api import bp


def sample_snapshot():
    boats = []
    for boat in range(1, 7):
        boats.append({
            "boat_number": boat, "racer_number": 4000 + boat, "course_number": boat,
            "course_avg_st": 0.13 + boat * 0.006, "derived_st_12": 0.14 + boat * 0.004,
            "derived_st_180d": 0.15, "entry_avg_st": 0.15,
            "exhibition_st": 0.08 + boat * 0.01, "exhibition_to_actual_bias": 0.04,
            "st_std": 0.035, "flying_count": 0, "accident_rate": 0.1,
            "wind_speed": 3, "wave_height": 2, "exhibition_time": 6.70 + boat * 0.02,
            "motor_asof_top2": 35 + boat, "published_motor_top2": 35 + boat,
            "course_win_rate": 55 if boat == 1 else 12, "national_top1": 18,
            "local_top1": 20, "exhibition_rank": boat, "exhibition_st_rank": boat,
        })
    return {"race": {"race_id": "202607220101", "race_date": "2026-07-22", "stadium_number": 1,
                     "race_number": 1, "race_grade_number": 5},
            "boats": boats, "tide": {}, "market": {}, "stage": "post_exhibition",
            "feature_cutoff_at": "2026-07-22T10:00:00"}


def test_probability_columns_sum_to_one_and_top10_unique():
    out = RuleEnsembleV1().predict(sample_snapshot())
    for key in ("first_probability", "second_probability", "third_probability"):
        assert sum(x[key] for x in out["boats"]) == pytest.approx(1.0)
    combos = [x["combination"] for x in out["trifectas"]]
    assert len(combos) == len(set(combos)) == 10


def test_pre_exhibition_missing_values_are_safe():
    snapshot = sample_snapshot()
    snapshot["stage"] = "pre_exhibition"
    for boat in snapshot["boats"]:
        boat["exhibition_st"] = None
        boat["exhibition_time"] = None
        boat["motor_asof_top2"] = None
    out = RuleEnsembleV1().predict(snapshot)
    assert len(out["boats"]) == 6
    assert 0 <= out["confidence"] <= 1


def test_post_exhibition_course_changes_drive_lane_prior_and_style():
    snapshot = sample_snapshot()
    snapshot["boats"][0]["course_number"] = 4
    snapshot["boats"][3]["course_number"] = 1
    out = RuleEnsembleV1().predict(snapshot)
    boat4 = next(x for x in out["boats"] if x["boat_number"] == 4)
    assert boat4["attack_style"] == "逃げ残り"
    assert boat4["first_probability"] > next(x for x in out["boats"] if x["boat_number"] == 1)["first_probability"]


def make_db():
    conn = sqlite3.connect(":memory:")
    repo = StartPredictionRepository(conn); repo.ensure_schema()
    conn.executescript("""
      CREATE TABLE races (race_id TEXT PRIMARY KEY,race_date TEXT,stadium_number INTEGER,race_number INTEGER,race_grade_number INTEGER);
      CREATE TABLE race_results (race_id TEXT,boat_number INTEGER,finishing_position INTEGER,course_number INTEGER,start_timing REAL,remarks TEXT,kimarite TEXT);
      CREATE TABLE race_payouts (race_id TEXT,bet_type TEXT,combination TEXT,payout INTEGER);
    """)
    conn.execute("INSERT INTO races VALUES ('202607220101','2026-07-22',1,1,5)")
    for boat in range(1, 7):
        conn.execute("INSERT INTO race_results VALUES (?,?,?,?,?,?,?)",
                     ("202607220101", boat, boat, boat, 0.10 + boat * 0.01, "", "逃げ" if boat == 1 else None))
    conn.execute("INSERT INTO race_payouts VALUES ('202607220101','trifecta','1-2-3',1230)")
    return conn, repo


def test_prediction_is_immutable_and_model_version_saved():
    conn, repo = make_db()
    prediction = RuleEnsembleV1().predict(sample_snapshot())
    first = repo.save("202607220101", "post_exhibition", "2026-07-22T10:00:00", sample_snapshot(), prediction)
    second = repo.save("202607220101", "post_exhibition", "later", {"changed": True}, prediction)
    assert first["prediction_id"] == second["prediction_id"]
    assert first["model_bundle_version"] == MODEL_VERSIONS["bundle"]
    assert second["feature_cutoff_at"] == "2026-07-22T10:00:00"
    assert first["boats"][0]["exhibition_st"] == pytest.approx(.09)
    assert first["boats"][0]["historical_avg_st"] == pytest.approx(.136)


def test_result_evaluation_and_roi_payload():
    conn, repo = make_db()
    out = RuleEnsembleV1().predict(sample_snapshot())
    saved = repo.save("202607220101", "post_exhibition", "2026-07-22T10:00:00", sample_snapshot(), out)
    evaluation = evaluate_prediction(conn, saved)
    assert evaluation["actual_first_boat"] == 1
    assert evaluation["actual_snapshot"]["actual_trifecta_payout"] == 1230
    stored = repo.save_evaluation(saved["prediction_id"], evaluation)
    assert stored["prediction_id"] == saved["prediction_id"]


def test_roi_uses_correct_stake_for_top1_and_top10():
    class FakeRepo:
        def __init__(self, conn): pass
        def ensure_schema(self): pass
        def metrics_rows(self, **kwargs):
            return [{
                "st_mae": .05, "top_trifecta": "1-2-3", "trifecta_top10_hit": 1,
                "actual_snapshot": {"actual_combo": "1-2-3", "actual_trifecta_payout": 2000},
                "input_snapshot": {"boats": [], "market": {}, "tide": {}},
            }]

    class DummyConn:
        def __enter__(self): return self
        def __exit__(self, *args): return False

    import src.start_prediction.service as service_module
    original = service_module.StartPredictionRepository
    service_module.StartPredictionRepository = FakeRepo
    try:
        metrics = StartPredictionService(lambda: DummyConn()).metrics({"from": "2026-01-01", "to": "2026-01-01"})
    finally:
        service_module.StartPredictionRepository = original
    assert metrics["roi_top1"] == pytest.approx(2000.0)
    assert metrics["roi_top10_box"] == pytest.approx(200.0)
    assert "roi_top10_first_pick" not in metrics


def test_future_result_fields_are_not_in_input_snapshot():
    raw = str(sample_snapshot()).lower()
    for forbidden in ("finishing_position", "kimarite", "payout", "actual_first_boat", "label_st"):
        assert forbidden not in raw


def test_prediction_api_requires_member_and_returns_payload(monkeypatch):
    class FakeService:
        def get(self, race_id, stage=None):
            return {"race_id": race_id, "stage": stage, "boats": []}
        def generate(self, race_id, stage):
            return {"race_id": race_id, "stage": stage, "status": "predicted"}
        def evaluate(self, race_id, stage=None):
            return {"race_id": race_id, "stage": stage, "status": "evaluated"}
    monkeypatch.setattr("src.web.start_prediction_api._service", lambda: FakeService())
    app = Flask(__name__); app.secret_key = "test"; app.register_blueprint(bp)
    client = app.test_client()
    assert client.get("/api/predictions/races/R1").status_code == 401
    with client.session_transaction() as session:
        session["is_member"] = True
    response = client.get("/api/predictions/races/R1?stage=post_exhibition")
    assert response.status_code == 200
    assert response.get_json()["race_id"] == "R1"
    generated = client.post(
        "/api/predictions/races/R1",
        json={"stage": "post_exhibition"},
    )
    assert generated.status_code == 200
    assert generated.get_json()["status"] == "predicted"
    evaluated = client.post("/api/predictions/races/R1/evaluate?stage=post_exhibition")
    assert evaluated.status_code == 200
    assert evaluated.get_json()["status"] == "evaluated"


def test_start_prediction_assets_are_valid_utf8_without_mojibake():
    root = Path(__file__).resolve().parents[1]
    targets = [
        root / "src/start_prediction/models.py",
        root / "src/start_prediction/evaluation.py",
        root / "src/web/start_prediction_api.py",
        root / "src/web/static/start_prediction.js",
    ]
    mojibake_markers = ("繧", "縺", "蜿", "螻", "莠")
    for target in targets:
        content = target.read_text(encoding="utf-8")
        assert "\ufffd" not in content
        assert not any(marker in content for marker in mojibake_markers)


def test_point_in_time_builder_excludes_future_rows_and_final_odds():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
      CREATE TABLE races (race_id TEXT PRIMARY KEY,race_date TEXT,stadium_number INTEGER,
        race_number INTEGER,race_grade_number INTEGER,race_title TEXT,race_subtitle TEXT,
        race_distance INTEGER,race_closed_at TEXT,series_day INTEGER);
      CREATE TABLE race_entries (race_id TEXT,boat_number INTEGER,racer_number INTEGER,
        racer_name TEXT,class_number INTEGER,age INTEGER,weight REAL,flying_count INTEGER,
        late_count INTEGER,avg_start_timing REAL,national_top_1_percent REAL,
        national_top_2_percent REAL,national_top_3_percent REAL,local_top_1_percent REAL,
        local_top_2_percent REAL,local_top_3_percent REAL,assigned_motor_number INTEGER,
        assigned_motor_top_2_percent REAL,assigned_motor_top_3_percent REAL);
      CREATE TABLE race_previews (race_id TEXT,boat_number INTEGER,weather_number INTEGER,
        wind_speed REAL,wind_direction_number INTEGER,wave_height REAL,temperature REAL,
        water_temperature REAL,course_number INTEGER,exhibition_time REAL,
        start_timing_exhibition REAL,stable_plate INTEGER,live_updated_at TEXT);
      CREATE TABLE race_results (race_id TEXT,boat_number INTEGER,finishing_position INTEGER,
        course_number INTEGER,start_timing REAL);
      CREATE TABLE derived_start_stats (race_id TEXT,boat_number INTEGER,
        derived_avg_start_timing_180d REAL,derived_start_count_180d INTEGER,
        derived_avg_start_timing_12 REAL,derived_start_count_12 INTEGER);
      CREATE TABLE racer_accident_period_stats (racer_number INTEGER,period_start TEXT,
        period_end TEXT,accident_points INTEGER,accident_rate REAL,updated_at TEXT);
      CREATE TABLE race_tides (race_id TEXT,tide_height_cm REAL,tide_phase TEXT,
        minutes_from_high INTEGER,minutes_from_low INTEGER,tide_range_cm REAL,
        tide_delta_60m_cm REAL,is_high_tide_zone INTEGER,is_low_tide_zone INTEGER,
        source TEXT,fetched_at TEXT);
      CREATE TABLE motor_cycle_stats (stadium_number INTEGER,motor_number INTEGER,
        starts_count INTEGER,top2_rate REAL,top3_rate REAL,through_race_date TEXT);
      CREATE TABLE motor_preinspection_stats (stadium_number INTEGER,race_date TEXT,
        source_name TEXT,racer_number INTEGER,racer_name TEXT,racer_class TEXT,
        motor_number INTEGER,motor_win2_rate REAL,boat_number INTEGER,boat_win2_rate REAL,
        preinspection_time REAL,preinspection_rank INTEGER,raw_text TEXT,source_url TEXT,
        collected_at TEXT);
      CREATE TABLE odds_trifecta (race_id TEXT,combination TEXT,odds REAL,is_final INTEGER,
        recorded_at TEXT,snapshot_label TEXT);
    """)
    races = [
        ("PAST", "2025-12-31"), ("TARGET", "2026-01-10"), ("FUTURE", "2026-01-11"),
    ]
    for race_id, race_date in races:
        conn.execute("INSERT INTO races VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (race_id, race_date, 1, 1, 5, "", "", 1800, race_date + "T12:00:00", 1))
    for boat in range(1, 7):
        racer = 5000 + boat
        conn.execute("INSERT INTO race_entries VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     ("TARGET", boat, racer, f"R{boat}", 1, 35, 52, 0, 0, .15,
                      18, 40, 60, 20, 42, 62, 10 + boat, 35, 55))
        conn.execute("INSERT INTO race_previews VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     ("TARGET", boat, 1, 3, 1, 2, 25, 23, 7 - boat, 6.7 + boat / 100,
                      .10 + boat / 100, 0, "2026-01-10T11:00:00"))
        conn.execute("INSERT INTO derived_start_stats VALUES (?,?,?,?,?,?)",
                     ("TARGET", boat, .15, 30, .14, 12))
        for historical_id, st in (("PAST", .10), ("FUTURE", .30)):
            conn.execute("INSERT INTO race_entries VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (historical_id, boat, racer, f"R{boat}", 1, 35, 52, 0, 0, .15,
                          18, 40, 60, 20, 42, 62, 10 + boat, 35, 55))
            conn.execute("INSERT INTO race_previews VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (historical_id, boat, 1, 3, 1, 2, 25, 23, boat, 6.8, .06, 0,
                          "2026-01-01T00:00:00"))
            conn.execute("INSERT INTO race_results VALUES (?,?,?,?,?)",
                         (historical_id, boat, boat, 7 - boat, st))
        conn.execute("INSERT INTO motor_cycle_stats VALUES (?,?,?,?,?,?)", (1, 10 + boat, 20, 40, 60, "2026-01-09"))
        conn.execute("INSERT INTO motor_cycle_stats VALUES (?,?,?,?,?,?)", (1, 10 + boat, 30, 90, 95, "2026-01-11"))
        conn.execute(
            "INSERT INTO motor_preinspection_stats VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (1, "2026-01-09", "test", racer, f"R{boat}", "A1", 10 + boat,
             35, boat, 40, 6.50 + boat / 100, boat, "", "", "2026-01-09T08:00:00"),
        )
        conn.execute(
            "INSERT INTO motor_preinspection_stats VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (1, "2026-01-11", "test", racer, f"R{boat}", "A1", 10 + boat,
             35, boat, 40, 6.00, 1, "", "", "2026-01-11T08:00:00"),
        )
        conn.execute("INSERT INTO racer_accident_period_stats VALUES (?,?,?,?,?,?)",
                     (racer, "2026-01-01", "2026-06-30", 20, .8, "2026-02-01"))
    conn.execute("INSERT INTO odds_trifecta VALUES ('TARGET','1-2-3',8.0,0,'2026-01-10T11:00:00','T-5')")
    conn.execute("INSERT INTO odds_trifecta VALUES ('TARGET','1-2-3',9.9,1,'2026-01-10T12:00:00','final')")

    post = PointInTimeFeatureBuilder(conn).build("TARGET", "post_exhibition").as_dict()
    pre = PointInTimeFeatureBuilder(conn).build("TARGET", "pre_exhibition").as_dict()
    assert post["boats"][0]["course_avg_st"] == pytest.approx(.10)
    assert post["boats"][0]["course_recent10_avg_st"] == pytest.approx(.10)
    assert post["boats"][0]["course_recent10_count"] == 1
    assert post["boats"][0]["motor_asof_top2"] == pytest.approx(40)
    assert post["boats"][0]["motor_exhibition_bias"] == pytest.approx(.04)
    assert post["boats"][0]["motor_exhibition_bias_count"] == 1
    assert post["boats"][0]["preinspection_time"] == pytest.approx(6.51)
    assert post["boats"][0]["preinspection_time_vs_day_avg"] == pytest.approx(.025)
    assert post["boats"][0]["accident_rate"] == 0
    assert post["market"]["trifecta_odds"]["1-2-3"] == pytest.approx(8.0)
    assert post["boats"][0]["course_number"] == 6
    assert pre["boats"][0]["course_number"] == 1
    assert pre["boats"][0]["exhibition_st"] is None


def test_point_in_time_builder_allows_missing_optional_derived_start_stats():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
      CREATE TABLE races (race_id TEXT PRIMARY KEY,race_date TEXT,stadium_number INTEGER,
        race_number INTEGER,race_grade_number INTEGER,race_title TEXT,race_subtitle TEXT,
        race_distance INTEGER,race_closed_at TEXT,series_day INTEGER);
      CREATE TABLE race_entries (race_id TEXT,boat_number INTEGER,racer_number INTEGER,
        racer_name TEXT,class_number INTEGER,age INTEGER,weight REAL,flying_count INTEGER,
        late_count INTEGER,avg_start_timing REAL,national_top_1_percent REAL,
        national_top_2_percent REAL,national_top_3_percent REAL,local_top_1_percent REAL,
        local_top_2_percent REAL,local_top_3_percent REAL,assigned_motor_number INTEGER,
        assigned_motor_top_2_percent REAL,assigned_motor_top_3_percent REAL);
      CREATE TABLE race_previews (race_id TEXT,boat_number INTEGER,weather_number INTEGER,
        wind_speed REAL,wind_direction_number INTEGER,wave_height REAL,temperature REAL,
        water_temperature REAL,course_number INTEGER,exhibition_time REAL,
        start_timing_exhibition REAL,stable_plate INTEGER,live_updated_at TEXT);
      CREATE TABLE race_results (race_id TEXT,boat_number INTEGER,finishing_position INTEGER,
        course_number INTEGER,start_timing REAL);
      CREATE TABLE racer_accident_period_stats (racer_number INTEGER,period_start TEXT,
        period_end TEXT,accident_points INTEGER,accident_rate REAL,updated_at TEXT);
      CREATE TABLE race_tides (race_id TEXT,tide_height_cm REAL,tide_phase TEXT,
        minutes_from_high INTEGER,minutes_from_low INTEGER,tide_range_cm REAL,
        tide_delta_60m_cm REAL,is_high_tide_zone INTEGER,is_low_tide_zone INTEGER,
        source TEXT,fetched_at TEXT);
      CREATE TABLE odds_trifecta (race_id TEXT,combination TEXT,odds REAL,is_final INTEGER,
        recorded_at TEXT,snapshot_label TEXT);
    """)
    conn.execute(
        "INSERT INTO races VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("TARGET", "2026-07-22", 24, 9, 5, "", "", 1800, "2026-07-22T21:25:00", 1),
    )
    for boat in range(1, 7):
        conn.execute(
            "INSERT INTO race_entries VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("TARGET", boat, 5000 + boat, f"R{boat}", 1, 35, 52, 0, 0, .15,
             18, 40, 60, 20, 42, 62, 10 + boat, 35, 55),
        )
        conn.execute(
            "INSERT INTO race_previews VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("TARGET", boat, 1, 3, 1, 2, 25, 23, boat, 6.7 + boat / 100,
             .10 + boat / 100, 0, "post-race"),
        )

    snapshot = PointInTimeFeatureBuilder(conn).build("TARGET", "post_exhibition").as_dict()

    assert len(snapshot["boats"]) == 6
    assert snapshot["feature_cutoff_at"] == "2026-07-22T21:25:00"
    assert snapshot["boats"][0]["derived_st_180d"] is None
    assert snapshot["boats"][0]["derived_st_count_180d"] == 0
    assert snapshot["boats"][0]["motor_asof_top2"] is None


def test_strategy_filter_parses_visible_bets_without_results():
    bets = parse_strategy_bets("3連単 1-2-3 / 1-3-2 と 2連単 1-3")

    assert bets == (
        StrategyBet("trifecta", "1-2-3"),
        StrategyBet("trifecta", "1-3-2"),
        StrategyBet("exacta", "1-3"),
    )


def test_strategy_filter_uses_prediction_snapshot_not_payouts():
    candidate = StrategyCandidate(
        race_id="R1",
        race_date="2026-07-22",
        strategy_key="demo_tri",
        label="demo",
        bets=(StrategyBet("trifecta", "1-2-3"),),
    )
    prediction = {
        "boats": [
            {"boat_number": 1, "first_probability": 0.62, "start_top_probability": 0.20},
            {"boat_number": 2, "first_probability": 0.12, "start_top_probability": 0.10},
        ],
        "trifectas": [
            {"scenario_key": "1-2-3", "rank": 4, "probability": 0.08},
        ],
        "actual_snapshot": {"actual_combo": "9-9-9", "actual_trifecta_payout": 999999},
    }

    assert pass_head_first_probability(candidate, prediction, minimum=0.55).passed
    assert pass_combo_in_trifecta_top(candidate, prediction, top_n=5).passed
    assert not pass_combo_in_trifecta_top(candidate, prediction, top_n=3).passed
