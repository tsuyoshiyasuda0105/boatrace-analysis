"""Search post-exhibition ST correction weight without future leakage."""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "")

import config
from scripts.evaluate_start_prediction_walkforward_st import (
    RunningStats,
    clamp,
    f,
    grouped_by_race,
    load_rows,
    weighted,
)
from src.db.connection import connect


REPORT_DIR = Path(__file__).resolve().parents[1] / "reports"


@dataclass
class Score:
    races: int = 0
    boats: int = 0
    abs_error: float = 0.0
    top: int = 0
    top2: int = 0
    within_003: int = 0

    def add_boat(self, pred: float, actual: float) -> None:
        self.boats += 1
        self.abs_error += abs(pred - actual)
        self.within_003 += int(abs(pred - actual) <= 0.03)

    def add_race(self, pred_order: list[int], actual_order: list[int]) -> None:
        self.races += 1
        self.top += int(pred_order[0] == actual_order[0])
        self.top2 += int(actual_order[0] in set(pred_order[:2]))

    @property
    def mae(self) -> float | None:
        return self.abs_error / self.boats if self.boats else None

    @property
    def top_rate(self) -> float | None:
        return self.top / self.races if self.races else None

    @property
    def top2_rate(self) -> float | None:
        return self.top2 / self.races if self.races else None

    @property
    def within_rate(self) -> float | None:
        return self.within_003 / self.boats if self.boats else None


def base_pre(
    row: dict[str, Any],
    course_hist: dict[tuple[int, int], RunningStats],
    course_recent: dict[tuple[int, int], deque[float]],
    motor_st_hist: dict[tuple[int, int], RunningStats],
) -> float:
    boat = int(row["boat_number"])
    racer = int(row["racer_number"])
    stadium = int(row["stadium_number"])
    motor = int(row.get("assigned_motor_number") or 0)
    key = (racer, boat)
    motor_key = (stadium, motor)
    recent = course_recent.get(key, deque(maxlen=10))
    course_stats = course_hist.get(key)
    motor_stats = motor_st_hist.get(motor_key)
    pred = weighted([
        (mean(recent) if len(recent) >= 3 else None, 0.26),
        (course_stats.avg if course_stats and course_stats.n else None, 0.24),
        (f(row.get("avg_start_timing")), 0.20),
        (motor_stats.avg if motor_stats and motor_stats.n >= 6 else None, 0.10),
    ], 0.17)
    caution = min(0.025, 0.004 * int(row.get("flying_count") or 0))
    weather = 0.0015 * max(0.0, float(row.get("wind_speed") or 0) - 3) + 0.0007 * max(0.0, float(row.get("wave_height") or 0) - 3)
    return clamp(pred + caution + weather, -0.05, 0.35)


def exhibition_adjusted(
    row: dict[str, Any],
    racer_bias_hist: dict[int, deque[float]],
    motor_bias_hist: dict[tuple[int, int], deque[float]],
    global_bias: deque[float],
) -> float | None:
    ex_st = f(row.get("start_timing_exhibition"))
    if ex_st is None:
        return None
    racer = int(row["racer_number"])
    stadium = int(row["stadium_number"])
    motor = int(row.get("assigned_motor_number") or 0)
    rv = racer_bias_hist.get(racer, deque(maxlen=20))
    mv = motor_bias_hist.get((stadium, motor), deque(maxlen=30))
    vals: list[tuple[float | None, float]] = []
    if len(rv) >= 5:
        vals.append((mean(rv), 0.55))
    if len(mv) >= 6:
        vals.append((mean(mv), 0.25))
    if global_bias:
        vals.append((mean(global_bias), 0.20))
    return ex_st + weighted(vals, 0.04)


def update_histories(
    race: list[dict[str, Any]],
    course_hist: dict[tuple[int, int], RunningStats],
    course_recent: dict[tuple[int, int], deque[float]],
    racer_bias_hist: dict[int, deque[float]],
    motor_st_hist: dict[tuple[int, int], RunningStats],
    motor_bias_hist: dict[tuple[int, int], deque[float]],
    global_bias: deque[float],
) -> None:
    for row in race:
        actual = f(row.get("actual_st"))
        if actual is None:
            continue
        racer = int(row["racer_number"])
        course = int(row.get("actual_course_number") or row.get("exhibition_course_number") or row["boat_number"])
        course_hist[(racer, course)].add(actual)
        course_recent[(racer, course)].append(actual)
        motor_key = (int(row["stadium_number"]), int(row.get("assigned_motor_number") or 0))
        motor_st_hist[motor_key].add(actual)
        ex = f(row.get("start_timing_exhibition"))
        if ex is not None:
            bias = actual - ex
            racer_bias_hist[racer].append(bias)
            motor_bias_hist[motor_key].append(bias)
            global_bias.append(bias)


def run(start: str, end: str, warmup_days: int, weights: list[float], progress_every: int) -> dict[str, Score]:
    with connect(config.DB_PATH) as conn:
        races = grouped_by_race(load_rows(conn, start, end))
    from datetime import date, timedelta

    eval_start = (date.fromisoformat(races[0][0]["race_date"]) + timedelta(days=warmup_days)).isoformat() if races else start
    course_hist: dict[tuple[int, int], RunningStats] = defaultdict(RunningStats)
    course_recent: dict[tuple[int, int], deque[float]] = defaultdict(lambda: deque(maxlen=10))
    racer_bias_hist: dict[int, deque[float]] = defaultdict(lambda: deque(maxlen=20))
    motor_st_hist: dict[tuple[int, int], RunningStats] = defaultdict(RunningStats)
    motor_bias_hist: dict[tuple[int, int], deque[float]] = defaultdict(lambda: deque(maxlen=30))
    global_bias: deque[float] = deque(maxlen=3000)
    scores = {f"w={w:.2f}": Score() for w in weights}
    for idx, race in enumerate(races, 1):
        if str(race[0]["race_date"]) >= eval_start and all(row.get("start_timing_exhibition") is not None for row in race):
            actual_order = [
                int(boat)
                for boat, _ in sorted(
                    ((int(row["boat_number"]), float(row["actual_st"])) for row in race),
                    key=lambda x: x[1],
                )
            ]
            per_boat = []
            for row in race:
                pre = base_pre(row, course_hist, course_recent, motor_st_hist)
                ex = exhibition_adjusted(row, racer_bias_hist, motor_bias_hist, global_bias)
                per_boat.append((int(row["boat_number"]), float(row["actual_st"]), pre, ex))
            for w in weights:
                key = f"w={w:.2f}"
                preds = []
                for boat, actual, pre, ex in per_boat:
                    pred = pre if ex is None else (1.0 - w) * pre + w * ex
                    scores[key].add_boat(pred, actual)
                    preds.append((boat, pred))
                scores[key].add_race([boat for boat, _ in sorted(preds, key=lambda x: x[1])], actual_order)
        update_histories(race, course_hist, course_recent, racer_bias_hist, motor_st_hist, motor_bias_hist, global_bias)
        if progress_every and idx % progress_every == 0:
            print(f"progress {idx}/{len(races)}", flush=True)
    return scores


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2026-01-01")
    parser.add_argument("--end", default="2026-07-23")
    parser.add_argument("--warmup-days", type=int, default=30)
    parser.add_argument("--progress-every", type=int, default=10000)
    args = parser.parse_args()
    weights = [i / 100 for i in range(0, 51, 5)]
    scores = run(args.start, args.end, args.warmup_days, weights, args.progress_every)
    lines = [
        "# 展示後ST補正ウェイト探索",
        "",
        f"- 期間: {args.start} から {args.end}",
        "- 方式: 展示前予測と、展示ST+選手/モーター/全体バイアス補正を重みでブレンド",
        "- 検証: 日付順ウォークフォワード。各レース結果は評価後に履歴へ追加。",
        "",
        "| 展示補正重み | レース | 艇 | ST MAE | ±0.03内 | STトップ | Top2内 |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for key, score in scores.items():
        lines.append(
            f"| {key} | {score.races} | {score.boats} | {score.mae:.4f} | {score.within_rate*100:.1f}% | {score.top_rate*100:.1f}% | {score.top2_rate*100:.1f}% |"
        )
    best = min(scores.items(), key=lambda x: x[1].mae if x[1].mae is not None else 99)
    lines.extend(["", f"- MAE最良: {best[0]} / MAE {best[1].mae:.4f}"])
    out = REPORT_DIR / f"start_prediction_post_weight_search_{args.start}_{args.end}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote={out} best={best[0]} mae={best[1].mae}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
