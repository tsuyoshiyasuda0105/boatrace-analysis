"""Compare immutable predictions with official race results."""
from __future__ import annotations

import math
from typing import Any

import numpy as np


def _dicts(cur) -> list[dict[str, Any]]:
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def evaluate_prediction(conn, prediction: dict[str, Any]) -> dict[str, Any]:
    race_id = prediction["race_id"]
    results = _dicts(conn.execute(
        """SELECT boat_number,finishing_position,course_number,start_timing,remarks,kimarite
             FROM race_results WHERE race_id=? ORDER BY boat_number""", (race_id,)
    ))
    if len(results) < 6:
        raise ValueError(f"official result is incomplete: {race_id}")
    finishers = sorted(results, key=lambda x: int(x.get("finishing_position") or 99))
    actual_order = [int(x["boat_number"]) for x in finishers[:3]]
    actual_first = actual_order[0]
    actual_kimarite = str(finishers[0].get("kimarite") or "その他")
    starts = {int(x["boat_number"]): float(x["start_timing"]) for x in results if x.get("start_timing") is not None}
    actual_start_top = min(starts, key=starts.get) if starts else None
    predicted_st = {int(x["boat_number"]): float(x["predicted_st"]) for x in prediction["boats"]}
    errors = np.array([predicted_st[b] - starts[b] for b in sorted(starts) if b in predicted_st])
    st_mae = float(np.mean(np.abs(errors))) if len(errors) else None
    st_rmse = float(np.sqrt(np.mean(errors**2))) if len(errors) else None
    st_mean_error = float(np.mean(errors)) if len(errors) else None
    predicted_start = sorted(prediction["boats"], key=lambda x: int(x["predicted_start_rank"]))
    predicted_start_top = int(predicted_start[0]["boat_number"])
    top2 = {int(x["boat_number"]) for x in predicted_start[:2]}
    winner_boat = max(prediction["boats"], key=lambda x: float(x["first_probability"]))
    winner_prob = max(1e-12, float(winner_boat["first_probability"]))
    actual_prob = max(1e-12, float(next(x for x in prediction["boats"] if int(x["boat_number"]) == actual_first)["first_probability"]))
    brier = sum((float(x["first_probability"]) - (1.0 if int(x["boat_number"]) == actual_first else 0.0)) ** 2 for x in prediction["boats"]) / 6
    combos = [str(x["scenario_key"]) for x in sorted(prediction["trifectas"], key=lambda x: int(x["rank"]))]
    actual_combo = "-".join(map(str, actual_order))
    payout_rows = _dicts(conn.execute(
        "SELECT combination,payout FROM race_payouts WHERE race_id=? AND bet_type='trifecta'", (race_id,)
    ))
    actual_payout = next((int(x["payout"]) for x in payout_rows if str(x["combination"]) == actual_combo), 0)
    categories: list[str] = []
    if actual_start_top is not None and predicted_start_top != actual_start_top:
        predicted_actual = predicted_st.get(actual_start_top)
        actual_value = starts.get(actual_start_top)
        categories.append("予測より本番STが速かった" if predicted_actual is not None and actual_value is not None and actual_value < predicted_actual else "予測より本番STが遅かった")
    snapshot_courses = {int(x["boat_number"]): int(x.get("course_number") or x["boat_number"]) for x in prediction["input_snapshot"].get("boats", [])}
    if any(snapshot_courses.get(int(x["boat_number"]), int(x["boat_number"])) != int(x.get("course_number") or x["boat_number"]) for x in results):
        categories.append("進入が想定と異なった")
    if prediction.get("primary_attack_boat") != actual_first and actual_first != 1:
        categories.append("攻め艇が不発")
    if any(str(x.get("remarks") or "").strip() for x in results):
        categories.append("接触や不利")
    if not categories and int(winner_boat["boat_number"]) != actual_first:
        categories.append("その他")
    actual_snapshot = {
        "results": results,
        "actual_order": actual_order,
        "actual_combo": actual_combo,
        "actual_trifecta_payout": actual_payout,
        "evaluation_only_fields": ["start_timing", "finishing_position", "kimarite", "payout"],
    }
    return {
        "actual_first_boat": actual_first,
        "actual_kimarite": actual_kimarite,
        "actual_start_top_boat": actual_start_top,
        "st_mae": st_mae,
        "st_rmse": st_rmse,
        "st_mean_error": st_mean_error,
        "start_top_hit": predicted_start_top == actual_start_top,
        "start_top2_hit": actual_start_top in top2 if actual_start_top is not None else False,
        "first_mark_hit": int(prediction["first_mark_boat"]) == actual_first,
        "kimarite_hit": str(prediction["predicted_kimarite"]) == actual_kimarite,
        "winner_hit": int(winner_boat["boat_number"]) == actual_first,
        "trifecta_top3_hit": actual_combo in combos[:3],
        "trifecta_top5_hit": actual_combo in combos[:5],
        "trifecta_top10_hit": actual_combo in combos[:10],
        "log_loss": -math.log(actual_prob),
        "brier_score": float(brier),
        "error_categories": categories,
        "actual_snapshot": actual_snapshot,
    }
