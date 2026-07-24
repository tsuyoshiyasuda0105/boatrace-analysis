"""Deterministic and explainable v1 ensemble for start/development prediction."""
from __future__ import annotations

import hashlib
import math
from collections import Counter
from typing import Any

import numpy as np

MODEL_VERSIONS = {
    "bundle": "start_development_v4_calibrated_exhibition",
    "st": "st_model_v4_calibrated_exhibition_rule_ensemble",
    "start_rank": "start_rank_model_v1_mc",
    "development": "development_model_v1_rule",
    "finish": "finish_order_model_v1_plackett_luce",
}

LANE_PRIOR = np.array([1.15, 0.42, 0.20, 0.12, 0.07, 0.04], dtype=float)


def _softmax(values: np.ndarray) -> np.ndarray:
    z = values - np.max(values)
    e = np.exp(np.clip(z, -30, 30))
    return e / e.sum()


def _weighted(values: list[tuple[float | None, float]], default: float) -> float:
    valid = [(float(v), w) for v, w in values if v is not None]
    if not valid:
        return default
    total = sum(w for _, w in valid)
    return sum(v * w for v, w in valid) / total


def _seed(race_id: str) -> int:
    return int(hashlib.sha256(race_id.encode("utf-8")).hexdigest()[:16], 16) % (2**32)


class RuleEnsembleV1:
    def predict(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        boats = snapshot["boats"]
        race = snapshot["race"]
        means, sigmas, reasons = [], [], []
        for b in boats:
            exhibit_adjusted = None
            if b.get("exhibition_st") is not None:
                bias_values: list[tuple[float | None, float]] = [
                    (b.get("exhibition_to_actual_bias"), 0.45),
                ]
                if int(b.get("racer_exhibition_bias_count") or 0) >= 5:
                    bias_values.append((b.get("racer_exhibition_bias_recent20"), 0.35))
                if int(b.get("motor_exhibition_bias_count") or 0) >= 6:
                    bias_values.append((b.get("motor_exhibition_bias"), 0.20))
                exhibit_adjusted = b["exhibition_st"] + _weighted(bias_values, 0.04)
            course_recent10 = (
                b.get("course_recent10_avg_st")
                if int(b.get("course_recent10_count") or 0) >= 3 else None
            )
            motor_avg_st = (
                b.get("motor_avg_st")
                if int(b.get("motor_st_count") or 0) >= 6 else None
            )
            preinspection_lift = b.get("preinspection_time_vs_day_avg")
            preinspection_st_adjust = None
            if preinspection_lift is not None:
                preinspection_st_adjust = 0.17 - 0.012 * float(np.clip(preinspection_lift / 0.10, -1.0, 1.0))
            mean = _weighted([
                (course_recent10, 0.20),
                (b.get("course_avg_st"), 0.22),
                (b.get("derived_st_12"), 0.24),
                (b.get("derived_st_180d"), 0.14),
                (b.get("entry_avg_st"), 0.10),
                (motor_avg_st, 0.10),
                (preinspection_st_adjust, 0.06),
                # Walk-forward tests showed that exhibition ST is useful as a weak
                # confirmation signal, but over-weighting it worsens actual ST MAE.
                (exhibit_adjusted, 0.05),
            ], 0.17)
            caution = min(0.025, 0.004 * int(b.get("flying_count") or 0) + 0.008 * float(b.get("accident_rate") or 0))
            weather = 0.0015 * max(0.0, float(b.get("wind_speed") or 0) - 3) + 0.0007 * max(0.0, float(b.get("wave_height") or 0) - 3)
            mean = float(np.clip(mean + caution + weather, -0.05, 0.35))
            sigma_source = (
                b.get("course_recent10_st_std")
                if int(b.get("course_recent10_count") or 0) >= 5 else b.get("st_std")
            )
            sigma = float(np.clip(sigma_source or 0.045, 0.018, 0.10))
            means.append(mean)
            sigmas.append(sigma)
            why = [f"コース別過去ST {b.get('course_avg_st'):.3f}" if b.get("course_avg_st") is not None else "コース別ST欠損"]
            if b.get("exhibition_st") is not None:
                why.append(f"展示ST {b['exhibition_st']:.2f}は弱い補正として反映")
            if caution:
                why.append("F持ち・事故率による慎重化補正")
            reasons.append(why)

        means_a = np.array(means)
        sigmas_a = np.array(sigmas)
        courses = np.array([
            int(b.get("course_number") or b.get("boat_number") or i + 1)
            for i, b in enumerate(boats)
        ], dtype=int)
        rng = np.random.default_rng(_seed(str(race["race_id"])))
        starts = rng.normal(means_a, sigmas_a, size=(6000, 6))
        start_orders = np.argsort(starts, axis=1)
        top_probs = np.bincount(start_orders[:, 0], minlength=6) / len(starts)
        predicted_order = np.argsort(means_a)
        ranks = np.empty(6, dtype=int)
        ranks[predicted_order] = np.arange(1, 7)

        exhibit = np.array([b.get("exhibition_time") if b.get("exhibition_time") is not None else 7.0 for b in boats])
        exhibit_score = -(exhibit - np.nanmin(exhibit)) * 2.8
        motor = np.array([(b.get("motor_asof_top2") or b.get("published_motor_top2") or 30.0) / 100 for b in boats])
        preinspection = np.array([
            float(np.clip((b.get("preinspection_time_vs_day_avg") or 0.0) / 0.10, -1.0, 1.0))
            for b in boats
        ])
        course_win = np.array([(b.get("course_win_rate") or 0.0) / 100 for b in boats])
        national = np.array([(b.get("national_top1") or 0.0) / 100 for b in boats])
        local = np.array([(b.get("local_top1") or 0.0) / 100 for b in boats])
        start_edge = (np.mean(means_a) - means_a) * 7.5
        lane = np.log(LANE_PRIOR[np.clip(courses - 1, 0, 5)])
        strength = lane + start_edge + exhibit_score + 1.4 * motor + 0.20 * preinspection + 1.8 * course_win + 1.0 * national + 0.7 * local
        leader_probs = _softmax(strength)

        attack_score = start_edge + exhibit_score + 1.2 * motor
        attack_score[0] -= 0.8
        attack_probs = _softmax(attack_score)
        attack_idx = int(np.argmax(attack_probs))
        leader_idx = int(np.argmax(leader_probs))
        attack_style = self._style(int(courses[attack_idx]))
        kimarite_probs = self._kimarite_probs(leader_probs, attack_probs)
        kimarite, kim_prob = max(kimarite_probs.items(), key=lambda x: x[1])

        first_counts = np.zeros(6)
        second_counts = np.zeros(6)
        third_counts = np.zeros(6)
        combos: Counter[str] = Counter()
        for _ in range(18000):
            remaining = list(range(6))
            order = []
            local_strength = strength.copy()
            for pos in range(3):
                remaining_courses = courses[remaining] - 1
                probs = _softmax(local_strength[remaining] - pos * 0.10 * remaining_courses)
                pick = int(rng.choice(remaining, p=probs))
                order.append(pick)
                remaining.remove(pick)
            first_counts[order[0]] += 1
            second_counts[order[1]] += 1
            third_counts[order[2]] += 1
            combos[f"{order[0]+1}-{order[1]+1}-{order[2]+1}"] += 1
        n_sim = float(sum(first_counts))
        first_p, second_p, third_p = first_counts / n_sim, second_counts / n_sim, third_counts / n_sim
        top_trifectas = [
            {"combination": combo, "probability": count / n_sim, "rank": rank}
            for rank, (combo, count) in enumerate(combos.most_common(10), 1)
        ]
        scenario_rows = [
            {"key": key, "probability": value, "rank": rank}
            for rank, (key, value) in enumerate(sorted(kimarite_probs.items(), key=lambda x: x[1], reverse=True), 1)
        ]
        completeness = sum(v is not None for b in boats for v in (b.get("course_avg_st"), b.get("exhibition_st"), b.get("motor_asof_top2"))) / 18
        entropy = -float(np.sum(first_p * np.log(np.clip(first_p, 1e-12, 1)))) / math.log(6)
        confidence = float(np.clip(0.25 + 0.40 * completeness + 0.35 * (1 - entropy), 0, 1))

        boat_rows = []
        for i, b in enumerate(boats):
            boat_rows.append({
                "boat_number": i + 1,
                "predicted_st": round(means[i], 4),
                "predicted_st_sigma": round(sigmas[i], 4),
                "predicted_start_rank": int(ranks[i]),
                "start_top_probability": float(top_probs[i]),
                "first_probability": float(first_p[i]),
                "second_probability": float(second_p[i]),
                "third_probability": float(third_p[i]),
                "attack_probability": float(attack_probs[i]),
                "attack_style": self._style(int(courses[i])),
                "reasons": reasons[i],
                "exhibition_st": b.get("exhibition_st"),
                "historical_avg_st": b.get("course_avg_st") or b.get("derived_st_180d"),
            })
        general_reasons = [
            f"{attack_idx+1}号艇は内艇との予測ST差と展示・モーター評価から攻め確率が最高",
            f"1マーク先頭は{leader_idx+1}号艇が{leader_probs[leader_idx]*100:.1f}%で最多",
            "確率は選手・モーター・展示・水面の欠損率を含めて算出",
        ]
        return {
            "model_versions": MODEL_VERSIONS,
            "boats": boat_rows,
            "primary_attack_boat": attack_idx + 1,
            "primary_attack_style": attack_style,
            "first_mark_boat": leader_idx + 1,
            "first_mark_probability": float(leader_probs[leader_idx]),
            "predicted_kimarite": kimarite,
            "kimarite_probability": float(kim_prob),
            "kimarite_scenarios": scenario_rows,
            "trifectas": top_trifectas,
            "confidence": confidence,
            "reasons": general_reasons,
        }

    @staticmethod
    def _style(course: int) -> str:
        return {1: "逃げ残り", 2: "差し", 3: "まくり差し", 4: "まくり", 5: "まくり差し", 6: "まくり差し"}[course]

    @staticmethod
    def _kimarite_probs(leader: np.ndarray, attack: np.ndarray) -> dict[str, float]:
        raw = {
            "逃げ": 0.80 * leader[0],
            "差し": 0.55 * leader[1] + 0.12 * leader[2],
            "まくり": 0.35 * (attack[2] + attack[3]) + 0.10 * attack[1],
            "まくり差し": 0.30 * (leader[2] + leader[3] + leader[4] + leader[5]),
            "抜き": 0.035,
            "恵まれ": 0.010,
            "その他": 0.025,
        }
        total = sum(raw.values())
        return {k: v / total for k, v in raw.items()}
