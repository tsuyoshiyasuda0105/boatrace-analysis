# Start development prediction v1

## Scope

The initial release predicts official ST, start order, start-top probability,
attacking boat/style, first-mark leader, kimarite, finish marginals and the top
10 trifecta scenarios. It stores an immutable input snapshot and evaluates the
prediction after official results arrive.

This is a probability analysis feature. It does not guarantee a race outcome or
profit and does not place bets.

## Architecture

- `src/start_prediction/features.py`: strict point-in-time features
- `src/start_prediction/models.py`: production-safe rule/Monte Carlo ensemble
- `src/start_prediction/repository.py`: immutable prediction storage
- `src/start_prediction/evaluation.py`: official-result comparison
- `src/start_prediction/service.py`: application service
- `src/web/start_prediction_api.py`: API and administration page
- `scripts/train_start_prediction_models.py`: time-series ML benchmark
- `scripts/generate_start_predictions.py`: post-exhibition generation
- `scripts/evaluate_start_predictions.py`: result evaluation
- `scripts/aggregate_start_prediction_metrics.py`: daily metrics

## Leakage policy

Historical racer and motor queries end strictly before the target race date.
Official ST, finish, kimarite and payout are labels/evaluation fields only.
Exhibition fields are only included in `post_exhibition` predictions. The saved
snapshot records the feature cutoff and makes later database changes irrelevant
to the original prediction.

## API

- `POST /api/predictions/races/{race_id}` with `{ "stage": "post_exhibition" }`
- `GET /api/predictions/races/{race_id}`
- `POST /api/predictions/races/{race_id}/evaluate`
- `GET /api/predictions/metrics?from=YYYY-MM-DD&to=YYYY-MM-DD`

All endpoints require a member session.

## Database

Apply `supabase/migrations/202607220001_start_prediction_v1.sql` before enabling
the Render batches. Tables have RLS enabled and no browser-client policy. The
Flask service connects through the server-side Postgres connection.

## Training

```powershell
python scripts/train_start_prediction_models.py --start 2020-01-01 --end 2026-06-30
```

The command uses chronological train/validation/test periods. It compares Ridge,
Random Forest and Histogram Gradient Boosting for ST, plus Logistic Regression
and Histogram Gradient Boosting for the winner label. LightGBM, XGBoost and
CatBoost availability is recorded without making them mandatory Render
dependencies. A candidate is not promoted automatically.

### Initial chronological benchmark

Benchmark data: 18,837 boat rows, 2022-01-01 through 2026-06-30. The split is
strictly chronological: training through 2025-09-06, validation through
2026-02-06, and test through 2026-06-30.

- ST model selected by validation MAE: Random Forest
  - validation MAE 0.05914 / RMSE 0.07746
  - test MAE 0.05728 / RMSE 0.07404
- Winner candidate selected by validation log loss: Histogram Gradient Boosting
  - validation log loss 0.41685
  - calibrated test log loss 0.42058 / Brier score 0.13238
- LightGBM was available and compared. XGBoost and CatBoost were unavailable in
  the current local runtime.
- Chronological kimarite labels were insufficient (validation 0, test 18), so
  the ML kimarite classifier is not promoted and v1 keeps the rule fallback.

The generated artifact is a candidate only. `RuleEnsembleV1` remains the active
production model until a walk-forward comparison shows a material improvement.

## Operation

Render's regular scheduler generates predictions after all six exhibition rows
are present, evaluates them after all six result rows arrive, and materializes a
daily summary during the nightly run. Failure is isolated from the existing race
page and predictor.

## Known limitations

- Local `race_original_exhibitions` currently has no rows, so lap/turn/straight
  times are not active v1 features.
- First-mark leader and attacking-boat ground truth are not published directly.
  V1 evaluates first-mark leader using the winner as a documented proxy.
- Relative wind direction still needs a verified venue orientation map.
- The trained ML artifact is a promotion candidate; the explainable rule/Monte
  Carlo ensemble remains the default until walk-forward metrics beat it.
- Current probabilities are model estimates, not guaranteed outcomes. The rule
  model has not yet passed a full out-of-sample calibration gate.
- ROI metrics distinguish Top1 (one 100-yen ticket) from a Top10 basket (ten
  100-yen tickets). The two stake definitions must not be compared as one rule.
