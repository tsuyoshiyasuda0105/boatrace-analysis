"""Time-series benchmark for ST, winner and kimarite models.

This command never performs a random split. Every feature is a race-entry or
preview snapshot available before the official result; labels are selected in a
separate result join and are never returned as features.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import (
    HistGradientBoostingClassifier, HistGradientBoostingRegressor,
    RandomForestClassifier, RandomForestRegressor,
)
from sklearn.impute import SimpleImputer
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss, mean_absolute_error, mean_squared_error
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

import config
from src.db.connection import connect

FEATURES = [
    "stadium_number", "race_number", "race_grade_number", "boat_number", "class_number",
    "age", "weight", "flying_count", "late_count", "avg_start_timing",
    "national_top_1_percent", "national_top_2_percent", "local_top_1_percent",
    "assigned_motor_top_2_percent", "assigned_motor_top_3_percent", "course_number",
    "exhibition_time", "start_timing_exhibition", "weather_number", "wind_speed",
    "wind_direction_number", "wave_height", "temperature", "water_temperature", "stable_plate",
    "tide_height_cm", "tide_delta_60m_cm", "tide_range_cm",
]
CATEGORICAL = ["stadium_number", "race_grade_number", "boat_number", "class_number", "course_number", "weather_number", "wind_direction_number"]
NUMERIC = [x for x in FEATURES if x not in CATEGORICAL]


def load_frame(start: str, end: str, limit: int) -> pd.DataFrame:
    with connect() as conn:
        day_count = int(conn.execute(
            "SELECT COUNT(DISTINCT race_date) FROM races WHERE race_date BETWEEN ? AND ?",
            (start, end),
        ).fetchone()[0] or 1)
    rows_per_day = max(6, int(limit) // day_count)
    sql = f"""WITH source AS (
               SELECT r.race_id,r.race_date,r.stadium_number,r.race_number,r.race_grade_number,
                      e.boat_number,e.class_number,e.age,e.weight,e.flying_count,e.late_count,
                      e.avg_start_timing,e.national_top_1_percent,e.national_top_2_percent,
                      e.local_top_1_percent,e.assigned_motor_top_2_percent,e.assigned_motor_top_3_percent,
                      COALESCE(p.course_number,e.boat_number) AS course_number,p.exhibition_time,
                      p.start_timing_exhibition,p.weather_number,p.wind_speed,p.wind_direction_number,
                      p.wave_height,p.temperature,p.water_temperature,p.stable_plate,
                      t.tide_height_cm,t.tide_delta_60m_cm,t.tide_range_cm,
                      rr.start_timing AS label_st,
                      CASE WHEN rr.finishing_position=1 THEN 1 ELSE 0 END AS label_winner,
                      CASE WHEN rr.finishing_position=1 THEN rr.kimarite ELSE NULL END AS label_kimarite
                 FROM races r JOIN race_entries e ON e.race_id=r.race_id
                 JOIN race_previews p ON p.race_id=e.race_id AND p.boat_number=e.boat_number
                 JOIN race_results rr ON rr.race_id=e.race_id AND rr.boat_number=e.boat_number
                 LEFT JOIN race_tides t ON t.race_id=r.race_id
                WHERE r.race_date BETWEEN ? AND ? AND rr.start_timing IS NOT NULL
             ), ranked AS (
               SELECT source.*,
                      ROW_NUMBER() OVER (PARTITION BY race_date ORDER BY race_id,boat_number) AS date_rank
                 FROM source
             )
             SELECT * FROM ranked WHERE date_rank <= {rows_per_day}
              ORDER BY race_date,race_id,boat_number LIMIT {int(limit)}"""
    with connect() as conn:
        cur = conn.execute(sql, (start, end))
        names = [d[0] for d in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=names)


def split_by_time(df: pd.DataFrame):
    dates = sorted(df["race_date"].astype(str).unique())
    if len(dates) < 30:
        raise ValueError("at least 30 race dates are required")
    train_end = dates[int(len(dates) * 0.67)]
    valid_end = dates[int(len(dates) * 0.84)]
    return (
        df[df.race_date <= train_end],
        df[(df.race_date > train_end) & (df.race_date <= valid_end)],
        df[df.race_date > valid_end],
        {"train_end": train_end, "validation_end": valid_end, "test_end": dates[-1]},
    )


def preprocessor(scale: bool = False):
    num_steps = [("impute", SimpleImputer(strategy="median"))]
    if scale:
        num_steps.append(("scale", StandardScaler()))
    return ColumnTransformer([
        ("num", Pipeline(num_steps), NUMERIC),
        ("cat", Pipeline([("impute", SimpleImputer(strategy="most_frequent")), ("onehot", OneHotEncoder(handle_unknown="ignore", sparse_output=False))]), CATEGORICAL),
    ])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default="2026-06-30")
    parser.add_argument("--limit", type=int, default=180000)
    parser.add_argument("--output", default=str(Path(config.MODEL_DIR) / "start_prediction_v1.joblib"))
    args = parser.parse_args()
    df = load_frame(args.start, args.end, args.limit)
    train, valid, test, periods = split_by_time(df)
    x_train, x_valid, x_test = train[FEATURES], valid[FEATURES], test[FEATURES]

    regressors = {
        "ridge": Pipeline([("prep", preprocessor(True)), ("model", Ridge(alpha=3.0))]),
        "random_forest": Pipeline([("prep", preprocessor()), ("model", RandomForestRegressor(n_estimators=160, max_depth=14, min_samples_leaf=12, n_jobs=-1, random_state=42))]),
        "hist_gradient_boosting": Pipeline([("prep", preprocessor()), ("model", HistGradientBoostingRegressor(max_iter=180, max_leaf_nodes=31, learning_rate=0.06, random_state=42))]),
    }
    optional = {}
    optional_regressors = {}
    optional_classifiers = {}
    for name, module in (("lightgbm", "lightgbm"), ("xgboost", "xgboost"), ("catboost", "catboost")):
        try:
            imported = __import__(module)
            if name == "lightgbm":
                optional_regressors[name] = imported.LGBMRegressor(n_estimators=180, max_depth=7, learning_rate=.05, verbosity=-1, random_state=42)
                optional_classifiers[name] = imported.LGBMClassifier(n_estimators=180, max_depth=7, learning_rate=.05, verbosity=-1, random_state=42)
            elif name == "xgboost":
                optional_regressors[name] = imported.XGBRegressor(n_estimators=180, max_depth=7, learning_rate=.05, n_jobs=-1, random_state=42)
                optional_classifiers[name] = imported.XGBClassifier(n_estimators=180, max_depth=7, learning_rate=.05, n_jobs=-1, random_state=42)
            else:
                optional_regressors[name] = imported.CatBoostRegressor(iterations=180, depth=7, learning_rate=.05, verbose=False, random_seed=42)
                optional_classifiers[name] = imported.CatBoostClassifier(iterations=180, depth=7, learning_rate=.05, verbose=False, random_seed=42)
            optional[name] = "installed and benchmarked"
        except (ImportError, OSError, AttributeError) as exc:
            optional[name] = f"not available: {type(exc).__name__}"
    for name, model in optional_regressors.items():
        regressors[name] = Pipeline([("prep", preprocessor()), ("model", model)])
    st_results = {}
    best_name = best_model = None
    best_mae = float("inf")
    for name, model in regressors.items():
        model.fit(x_train, train.label_st)
        pred = model.predict(x_valid)
        mae = mean_absolute_error(valid.label_st, pred)
        st_results[name] = {"validation_mae": mae, "validation_rmse": mean_squared_error(valid.label_st, pred) ** 0.5}
        if mae < best_mae:
            best_name, best_model, best_mae = name, model, mae
    assert best_model is not None
    best_model.fit(pd.concat([x_train, x_valid]), pd.concat([train.label_st, valid.label_st]))
    test_pred = best_model.predict(x_test)
    st_results[best_name]["test_mae"] = mean_absolute_error(test.label_st, test_pred)
    st_results[best_name]["test_rmse"] = mean_squared_error(test.label_st, test_pred) ** 0.5

    classifiers = {
        "logistic": Pipeline([("prep", preprocessor(True)), ("model", LogisticRegression(max_iter=300, class_weight="balanced"))]),
        "random_forest": Pipeline([("prep", preprocessor()), ("model", RandomForestClassifier(n_estimators=180, max_depth=14, min_samples_leaf=10, n_jobs=-1, class_weight="balanced", random_state=42))]),
        "hist_gradient_boosting": Pipeline([("prep", preprocessor()), ("model", HistGradientBoostingClassifier(max_iter=160, learning_rate=0.06, random_state=42))]),
    }
    for name, model in optional_classifiers.items():
        classifiers[name] = Pipeline([("prep", preprocessor()), ("model", model)])
    winner_results = {}
    winner_best = None
    winner_loss = float("inf")
    for name, model in classifiers.items():
        model.fit(x_train, train.label_winner)
        prob = model.predict_proba(x_valid)[:, 1]
        loss = log_loss(valid.label_winner, prob)
        winner_results[name] = {"validation_log_loss": loss, "validation_accuracy": accuracy_score(valid.label_winner, prob >= 0.5)}
        if loss < winner_loss: winner_loss, winner_best = loss, model
    # Keep the selected model fitted on train, then calibrate only with the later
    # validation period. This preserves the chronological boundary.
    valid_prob = winner_best.predict_proba(x_valid)[:, 1]
    calibrator = IsotonicRegression(out_of_bounds="clip").fit(valid_prob, valid.label_winner)
    raw_test_prob = winner_best.predict_proba(x_test)[:, 1]
    test_prob = np.clip(calibrator.predict(raw_test_prob), 1e-6, 1 - 1e-6)
    winner_test = {
        "test_log_loss": log_loss(test.label_winner, test_prob),
        "test_brier": brier_score_loss(test.label_winner, test_prob),
        "test_accuracy": accuracy_score(test.label_winner, test_prob >= 0.5),
        "raw_test_log_loss": log_loss(test.label_winner, np.clip(raw_test_prob, 1e-6, 1 - 1e-6)),
    }

    kim_train, kim_valid, kim_test = (
        train[train.label_kimarite.notna()], valid[valid.label_kimarite.notna()], test[test.label_kimarite.notna()]
    )
    kimarite_results = {}
    kim_best_name = None
    kim_best_model = None
    kim_best_loss = float("inf")
    kimarite_test = {"status": "insufficient chronological labels"}
    if min(len(kim_train), len(kim_valid), len(kim_test)) > 0 and kim_train.label_kimarite.nunique() > 1:
        for name, model in classifiers.items():
            if name not in {"logistic", "random_forest", "hist_gradient_boosting"}:
                continue
            model.fit(kim_train[FEATURES], kim_train.label_kimarite)
            prob = model.predict_proba(kim_valid[FEATURES])
            loss = log_loss(kim_valid.label_kimarite, prob, labels=model.classes_)
            kimarite_results[name] = {
                "validation_log_loss": loss,
                "validation_accuracy": accuracy_score(kim_valid.label_kimarite, model.predict(kim_valid[FEATURES])),
            }
            if loss < kim_best_loss:
                kim_best_name, kim_best_model, kim_best_loss = name, model, loss
        if kim_best_model is not None:
            kim_best_model.fit(
                pd.concat([kim_train[FEATURES], kim_valid[FEATURES]]),
                pd.concat([kim_train.label_kimarite, kim_valid.label_kimarite]),
            )
            kim_test_prob = kim_best_model.predict_proba(kim_test[FEATURES])
            kimarite_test = {
                "test_log_loss": log_loss(kim_test.label_kimarite, kim_test_prob, labels=kim_best_model.classes_),
                "test_accuracy": accuracy_score(kim_test.label_kimarite, kim_best_model.predict(kim_test[FEATURES])),
            }
    kimarite_results["label_counts"] = {
        "train": len(kim_train), "validation": len(kim_valid), "test": len(kim_test),
    }

    artifact = {
        "version": "start_development_ml_candidate_v1",
        "st_model": best_model,
        "winner_model": winner_best,
        "winner_calibrator": calibrator,
        "kimarite_model": kim_best_model,
        "features": FEATURES,
        "periods": periods,
        "trained_rows": len(train) + len(valid),
        "test_rows": len(test),
    }
    output = Path(args.output); output.parent.mkdir(parents=True, exist_ok=True); joblib.dump(artifact, output)
    report = {
        "periods": periods, "rows": len(df), "st": st_results,
        "winner": winner_results, "winner_test": winner_test,
        "kimarite": kimarite_results, "kimarite_test": kimarite_test,
        "optional_models": optional, "selected_st": best_name,
        "selected_winner": next((name for name, model in classifiers.items() if model is winner_best), None),
        "selected_kimarite": kim_best_name, "artifact": str(output),
    }
    report_path = output.with_suffix(".metrics.json"); report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
