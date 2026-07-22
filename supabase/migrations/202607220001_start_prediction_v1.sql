-- Start/development prediction v1. Server-only tables; no client policies.
CREATE TABLE IF NOT EXISTS start_prediction_models (
 model_version text PRIMARY KEY, component text NOT NULL, model_kind text NOT NULL,
 training_start date, training_end date, feature_names jsonb NOT NULL DEFAULT '[]'::jsonb,
 parameters jsonb NOT NULL DEFAULT '{}'::jsonb, metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
 artifact_uri text, is_active boolean NOT NULL DEFAULT false, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS race_start_predictions (
 prediction_id bigserial PRIMARY KEY, race_id text NOT NULL,
 prediction_stage text NOT NULL DEFAULT 'post_exhibition', model_bundle_version text NOT NULL,
 predicted_at timestamptz NOT NULL DEFAULT now(), feature_cutoff_at timestamptz NOT NULL,
 input_snapshot jsonb NOT NULL, primary_attack_boat integer, primary_attack_style text,
 first_mark_boat integer, first_mark_probability double precision,
 predicted_kimarite text, kimarite_probability double precision,
 confidence double precision NOT NULL, reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
 status text NOT NULL DEFAULT 'predicted', evaluated_at timestamptz,
 UNIQUE (race_id, prediction_stage, model_bundle_version)
);
CREATE TABLE IF NOT EXISTS race_start_prediction_boats (
 prediction_id bigint NOT NULL REFERENCES race_start_predictions(prediction_id) ON DELETE CASCADE,
 boat_number integer NOT NULL CHECK (boat_number BETWEEN 1 AND 6), predicted_st double precision NOT NULL,
 predicted_st_sigma double precision NOT NULL, predicted_start_rank integer NOT NULL,
 start_top_probability double precision NOT NULL, first_probability double precision NOT NULL,
 second_probability double precision NOT NULL, third_probability double precision NOT NULL,
 attack_probability double precision NOT NULL, attack_style text, reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
 PRIMARY KEY (prediction_id, boat_number)
);
CREATE TABLE IF NOT EXISTS race_start_prediction_scenarios (
 prediction_id bigint NOT NULL REFERENCES race_start_predictions(prediction_id) ON DELETE CASCADE,
 scenario_kind text NOT NULL, scenario_key text NOT NULL, rank integer NOT NULL,
 probability double precision NOT NULL, payload jsonb NOT NULL DEFAULT '{}'::jsonb,
 PRIMARY KEY (prediction_id, scenario_kind, scenario_key)
);
CREATE TABLE IF NOT EXISTS race_start_prediction_evaluations (
 prediction_id bigint PRIMARY KEY REFERENCES race_start_predictions(prediction_id) ON DELETE CASCADE,
 evaluated_at timestamptz NOT NULL DEFAULT now(), actual_first_boat integer, actual_kimarite text,
 actual_start_top_boat integer, st_mae double precision, st_rmse double precision,
 st_mean_error double precision, start_top_hit boolean, start_top2_hit boolean,
 first_mark_hit boolean, kimarite_hit boolean, winner_hit boolean,
 trifecta_top3_hit boolean, trifecta_top5_hit boolean, trifecta_top10_hit boolean,
 log_loss double precision, brier_score double precision,
 error_categories jsonb NOT NULL DEFAULT '[]'::jsonb, actual_snapshot jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS start_prediction_metrics_daily (
 metric_date date NOT NULL, model_bundle_version text NOT NULL, stadium_number integer NOT NULL DEFAULT 0,
 race_grade_number integer NOT NULL DEFAULT 0, prediction_count integer NOT NULL, evaluated_count integer NOT NULL,
 st_mae double precision, st_rmse double precision, start_top_accuracy double precision,
 winner_accuracy double precision, kimarite_accuracy double precision,
 trifecta_top10_accuracy double precision, roi double precision,
 payload jsonb NOT NULL DEFAULT '{}'::jsonb, updated_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY (metric_date, model_bundle_version, stadium_number, race_grade_number)
);
CREATE INDEX IF NOT EXISTS idx_start_predictions_race ON race_start_predictions(race_id, predicted_at DESC);
CREATE INDEX IF NOT EXISTS idx_start_predictions_cutoff ON race_start_predictions(feature_cutoff_at DESC);
CREATE INDEX IF NOT EXISTS idx_start_evaluations_date ON race_start_prediction_evaluations(evaluated_at DESC);
CREATE INDEX IF NOT EXISTS idx_start_metrics_filters ON start_prediction_metrics_daily(model_bundle_version, metric_date, stadium_number, race_grade_number);
ALTER TABLE start_prediction_models ENABLE ROW LEVEL SECURITY;
ALTER TABLE race_start_predictions ENABLE ROW LEVEL SECURITY;
ALTER TABLE race_start_prediction_boats ENABLE ROW LEVEL SECURITY;
ALTER TABLE race_start_prediction_scenarios ENABLE ROW LEVEL SECURITY;
ALTER TABLE race_start_prediction_evaluations ENABLE ROW LEVEL SECURITY;
ALTER TABLE start_prediction_metrics_daily ENABLE ROW LEVEL SECURITY;
