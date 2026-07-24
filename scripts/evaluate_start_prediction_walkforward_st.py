"""Fast walk-forward ST prediction benchmark.

This script is intentionally separate from the production prediction service.
It evaluates the idea of:

1. pre-exhibition ST prediction from racer/course history and entry data
2. post-exhibition correction from exhibition ST plus racer/motor bias
3. comparison with actual ST, without using future rows

Rows are processed strictly by race_date/race_id. Histories are updated only
after each race has been evaluated.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "")

import config
from src.db.connection import connect


REPORT_DIR = Path(__file__).resolve().parents[1] / "reports"


@dataclass
class RunningStats:
    n: int = 0
    s: float = 0.0
    ss: float = 0.0

    def add(self, value: float) -> None:
        self.n += 1
        self.s += value
        self.ss += value * value

    @property
    def avg(self) -> float | None:
        return self.s / self.n if self.n else None

    @property
    def std(self) -> float | None:
        if self.n < 2:
            return None
        var = max(0.0, self.ss / self.n - (self.s / self.n) ** 2)
        return math.sqrt(var)


@dataclass
class Metrics:
    races: int = 0
    boats: int = 0
    abs_error: float = 0.0
    sq_error: float = 0.0
    signed_error: float = 0.0
    within_003: int = 0
    within_005: int = 0
    top_hit: int = 0
    top2_hit: int = 0
    top3_hit: int = 0
    groups: dict[str, "Metrics"] = field(default_factory=dict)

    def boat(self, pred: float, actual: float) -> None:
        err = pred - actual
        self.boats += 1
        self.abs_error += abs(err)
        self.sq_error += err * err
        self.signed_error += err
        self.within_003 += int(abs(err) <= 0.03)
        self.within_005 += int(abs(err) <= 0.05)

    def race(self, pred_order: list[int], actual_order: list[int]) -> None:
        self.races += 1
        actual_top = actual_order[0]
        self.top_hit += int(pred_order[0] == actual_top)
        self.top2_hit += int(actual_top in set(pred_order[:2]))
        self.top3_hit += int(actual_top in set(pred_order[:3]))

    def row(self) -> dict[str, Any]:
        return {
            "races": self.races,
            "boats": self.boats,
            "mae": self.abs_error / self.boats if self.boats else None,
            "rmse": math.sqrt(self.sq_error / self.boats) if self.boats else None,
            "bias": self.signed_error / self.boats if self.boats else None,
            "within_003": self.within_003 / self.boats if self.boats else None,
            "within_005": self.within_005 / self.boats if self.boats else None,
            "top": self.top_hit / self.races if self.races else None,
            "top2": self.top2_hit / self.races if self.races else None,
            "top3": self.top3_hit / self.races if self.races else None,
        }


def f(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def weighted(values: list[tuple[float | None, float]], default: float = 0.17) -> float:
    valid = [(v, w) for v, w in values if v is not None]
    if not valid:
        return default
    total = sum(w for _, w in valid)
    return sum(v * w for v, w in valid) / total


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def load_rows(conn, start: str, end: str) -> list[dict[str, Any]]:
    cur = conn.execute(
        """SELECT r.race_id, r.race_date, r.stadium_number, r.race_number,
                  r.race_grade_number,
                  e.boat_number, e.racer_number, e.class_number,
                  e.avg_start_timing, e.flying_count, e.late_count,
                  e.assigned_motor_number,
                  e.assigned_motor_top_2_percent, e.assigned_motor_top_3_percent,
                  p.course_number AS exhibition_course_number,
                  p.start_timing_exhibition, p.exhibition_time,
                  p.wind_speed, p.wave_height, p.weather_number,
                  rr.course_number AS actual_course_number,
                  rr.start_timing AS actual_st,
                  rr.finishing_position,
                  rr.kimarite
             FROM races r
             JOIN race_entries e ON e.race_id=r.race_id
             JOIN race_results rr ON rr.race_id=e.race_id AND rr.boat_number=e.boat_number
             LEFT JOIN race_previews p ON p.race_id=e.race_id AND p.boat_number=e.boat_number
            WHERE r.race_date BETWEEN ? AND ?
              AND rr.start_timing IS NOT NULL
            ORDER BY r.race_date, r.stadium_number, r.race_number, e.boat_number""",
        (start, end),
    )
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def grouped_by_race(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    current_id = None
    current: list[dict[str, Any]] = []
    for row in rows:
        if current_id is None:
            current_id = row["race_id"]
        if row["race_id"] != current_id:
            if len(current) == 6:
                groups.append(current)
            current_id = row["race_id"]
            current = []
        current.append(row)
    if len(current) == 6:
        groups.append(current)
    return groups


def labels(row: dict[str, Any], course_stats: RunningStats | None, recent10: deque[float], racer_bias: deque[float], motor_stats: RunningStats | None) -> list[str]:
    out = []
    out.append("course_recent10>=3" if len(recent10) >= 3 else "course_recent10<3")
    if course_stats and course_stats.n >= 20 and (course_stats.std or 0) <= 0.035:
        out.append("stable_st_racer")
    if course_stats and course_stats.n >= 20 and (course_stats.std or 0) >= 0.060:
        out.append("volatile_st_racer")
    if len(racer_bias) >= 5:
        out.append("racer_ex_bias>=5")
        b = mean(racer_bias)
        if b >= 0.04:
            out.append("show_fast_actual_late_type")
        if b <= 0.00:
            out.append("show_slow_actual_fast_type")
    else:
        out.append("racer_ex_bias<5")
    if motor_stats and motor_stats.n >= 6:
        out.append("motor_st_history>=6")
    if int(row.get("flying_count") or 0) > 0:
        out.append("flying_count>0")
    cls = int(row.get("class_number") or 0)
    if cls in (1, 2):
        out.append("class_A")
    elif cls in (3, 4):
        out.append("class_B")
    return out


def predict_row(
    row: dict[str, Any],
    stage: str,
    course_hist: dict[tuple[int, int], RunningStats],
    course_recent: dict[tuple[int, int], deque[float]],
    racer_bias_hist: dict[int, deque[float]],
    motor_st_hist: dict[tuple[int, int], RunningStats],
    motor_bias_hist: dict[tuple[int, int], deque[float]],
    global_bias: deque[float],
) -> tuple[float, list[str]]:
    boat = int(row["boat_number"])
    racer = int(row["racer_number"])
    stadium = int(row["stadium_number"])
    motor = int(row.get("assigned_motor_number") or 0)
    course = boat if stage == "pre" else int(row.get("exhibition_course_number") or boat)
    key = (racer, course)
    motor_key = (stadium, motor)
    course_stats = course_hist.get(key)
    recent10 = course_recent.get(key, deque(maxlen=10))
    racer_bias = racer_bias_hist.get(racer, deque(maxlen=20))
    motor_stats = motor_st_hist.get(motor_key)
    motor_bias = motor_bias_hist.get(motor_key, deque(maxlen=30))
    recent_avg = mean(recent10) if len(recent10) >= 3 else None
    course_avg = course_stats.avg if course_stats and course_stats.n else None
    entry_avg = f(row.get("avg_start_timing"))
    motor_avg = motor_stats.avg if motor_stats and motor_stats.n >= 6 else None
    exhibit_adjusted = None
    if stage == "post" and row.get("start_timing_exhibition") is not None:
        bias_values = [(mean(global_bias), 0.35)] if global_bias else []
        if len(racer_bias) >= 5:
            bias_values.append((mean(racer_bias), 0.45))
        if len(motor_bias) >= 6:
            bias_values.append((mean(motor_bias), 0.20))
        exhibit_adjusted = float(row["start_timing_exhibition"]) + weighted(bias_values, 0.04)
    mean_st = weighted(
        [
            (recent_avg, 0.26),
            (course_avg, 0.24),
            (entry_avg, 0.20),
            (motor_avg, 0.10),
            (exhibit_adjusted, 0.40),
        ],
        0.17,
    )
    caution = min(0.025, 0.004 * int(row.get("flying_count") or 0))
    weather = 0.0015 * max(0.0, float(row.get("wind_speed") or 0) - 3) + 0.0007 * max(0.0, float(row.get("wave_height") or 0) - 3)
    pred = clamp(mean_st + caution + weather, -0.05, 0.35)
    return pred, labels(row, course_stats, recent10, racer_bias, motor_stats)


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
        actual_st = f(row.get("actual_st"))
        if actual_st is None:
            continue
        racer = int(row["racer_number"])
        course = int(row.get("actual_course_number") or row.get("exhibition_course_number") or row["boat_number"])
        key = (racer, course)
        course_hist[key].add(actual_st)
        course_recent[key].append(actual_st)
        stadium = int(row["stadium_number"])
        motor = int(row.get("assigned_motor_number") or 0)
        motor_key = (stadium, motor)
        motor_st_hist[motor_key].add(actual_st)
        ex_st = f(row.get("start_timing_exhibition"))
        if ex_st is not None:
            bias = actual_st - ex_st
            racer_bias_hist[racer].append(bias)
            motor_bias_hist[motor_key].append(bias)
            global_bias.append(bias)


def add_to_metrics(metrics: Metrics, race: list[dict[str, Any]], preds: dict[int, tuple[float, list[str]]]) -> None:
    actual_st = {int(row["boat_number"]): float(row["actual_st"]) for row in race if row.get("actual_st") is not None}
    if len(actual_st) != 6:
        return
    pred_order = [boat for boat, _ in sorted(((b, p[0]) for b, p in preds.items()), key=lambda x: x[1])]
    actual_order = [boat for boat, _ in sorted(actual_st.items(), key=lambda x: x[1])]
    metrics.race(pred_order, actual_order)
    for row in race:
        boat = int(row["boat_number"])
        pred, boat_labels = preds[boat]
        actual = float(row["actual_st"])
        metrics.boat(pred, actual)
        for label in boat_labels:
            metrics.groups.setdefault(label, Metrics()).boat(pred, actual)


def run(start: str, end: str, warmup_days: int, progress_every: int) -> dict[str, Any]:
    with connect(config.DB_PATH) as conn:
        rows = load_rows(conn, start, end)
    races = grouped_by_race(rows)
    course_hist: dict[tuple[int, int], RunningStats] = defaultdict(RunningStats)
    course_recent: dict[tuple[int, int], deque[float]] = defaultdict(lambda: deque(maxlen=10))
    racer_bias_hist: dict[int, deque[float]] = defaultdict(lambda: deque(maxlen=20))
    motor_st_hist: dict[tuple[int, int], RunningStats] = defaultdict(RunningStats)
    motor_bias_hist: dict[tuple[int, int], deque[float]] = defaultdict(lambda: deque(maxlen=30))
    global_bias: deque[float] = deque(maxlen=3000)
    pre = Metrics()
    post = Metrics()
    eval_start_date = None
    if races:
        first_date = races[0][0]["race_date"]
        # Date arithmetic via ordinal keeps dependencies minimal.
        from datetime import date, timedelta
        eval_start_date = (date.fromisoformat(first_date) + timedelta(days=warmup_days)).isoformat()
    evaluated = 0
    post_available = 0
    for idx, race in enumerate(races, 1):
        race_date = str(race[0]["race_date"])
        should_eval = eval_start_date is None or race_date >= eval_start_date
        if should_eval:
            pre_preds = {
                int(row["boat_number"]): predict_row(
                    row, "pre", course_hist, course_recent, racer_bias_hist,
                    motor_st_hist, motor_bias_hist, global_bias,
                )
                for row in race
            }
            add_to_metrics(pre, race, pre_preds)
            evaluated += 1
            if all(row.get("start_timing_exhibition") is not None and row.get("exhibition_time") is not None for row in race):
                post_available += 1
                post_preds = {
                    int(row["boat_number"]): predict_row(
                        row, "post", course_hist, course_recent, racer_bias_hist,
                        motor_st_hist, motor_bias_hist, global_bias,
                    )
                    for row in race
                }
                add_to_metrics(post, race, post_preds)
        update_histories(race, course_hist, course_recent, racer_bias_hist, motor_st_hist, motor_bias_hist, global_bias)
        if progress_every and idx % progress_every == 0:
            print(f"progress {idx}/{len(races)} evaluated={evaluated}", flush=True)
    return {
        "start": start,
        "end": end,
        "warmup_days": warmup_days,
        "races_loaded": len(races),
        "races_evaluated": evaluated,
        "post_available": post_available,
        "pre": pre,
        "post": post,
    }


def fmt(v: Any, digits: int = 3) -> str:
    if v is None:
        return "-"
    return f"{float(v):.{digits}f}"


def pct(v: Any, digits: int = 1) -> str:
    if v is None:
        return "-"
    return f"{float(v) * 100:.{digits}f}%"


def write_report(result: dict[str, Any], output: Path) -> None:
    pre = result["pre"].row()
    post = result["post"].row()
    lines = [
        "# 展示前/展示後 ST予測 ウォークフォワード検証",
        "",
        f"- データ期間: {result['start']} から {result['end']}",
        f"- 読込レース: {result['races_loaded']}",
        f"- 評価レース: {result['races_evaluated']} (ウォームアップ {result['warmup_days']}日を除外)",
        f"- 展示後評価レース: {result['post_available']}",
        "- 検証条件: 日付順に処理し、各レースの結果は評価後に履歴へ追加。未来情報は使用しない。",
        "",
        "## 全体",
        "",
        "| 段階 | レース | 艇データ | ST MAE | ST RMSE | 平均誤差 | ±0.03内 | ±0.05内 | STトップ | Top2内 | Top3内 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        f"| 展示前 | {pre['races']} | {pre['boats']} | {fmt(pre['mae'])} | {fmt(pre['rmse'])} | {fmt(pre['bias'])} | {pct(pre['within_003'])} | {pct(pre['within_005'])} | {pct(pre['top'])} | {pct(pre['top2'])} | {pct(pre['top3'])} |",
        f"| 展示後補正 | {post['races']} | {post['boats']} | {fmt(post['mae'])} | {fmt(post['rmse'])} | {fmt(post['bias'])} | {pct(post['within_003'])} | {pct(post['within_005'])} | {pct(post['top'])} | {pct(post['top2'])} | {pct(post['top3'])} |",
        "",
        "## 展示後補正の効果",
        "",
    ]
    if pre["mae"] is not None and post["mae"] is not None:
        lines.append(f"- ST MAE: {fmt(pre['mae'])} -> {fmt(post['mae'])} (改善 {fmt(pre['mae'] - post['mae'])})")
    if pre["top"] is not None and post["top"] is not None:
        lines.append(f"- STトップ的中: {pct(pre['top'])} -> {pct(post['top'])} (差分 {pct(post['top'] - pre['top'])})")
    lines.extend([
        "",
        "## 選手特性別",
        "",
        "| 区分 | 展示前 n | 展示前 MAE | 展示前 ±0.03 | 展示後 n | 展示後 MAE | 展示後 ±0.03 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ])
    labels = sorted(set(result["pre"].groups) | set(result["post"].groups))
    for label in labels:
        p = result["pre"].groups.get(label, Metrics()).row()
        q = result["post"].groups.get(label, Metrics()).row()
        lines.append(f"| {label} | {p['boats']} | {fmt(p['mae'])} | {pct(p['within_003'])} | {q['boats']} | {fmt(q['mae'])} | {pct(q['within_003'])} |")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2024-11-24")
    parser.add_argument("--end", default="2026-07-23")
    parser.add_argument("--warmup-days", type=int, default=90)
    parser.add_argument("--progress-every", type=int, default=5000)
    parser.add_argument("--output")
    args = parser.parse_args()
    result = run(args.start, args.end, args.warmup_days, args.progress_every)
    output = Path(args.output) if args.output else REPORT_DIR / f"start_prediction_walkforward_st_{args.start}_{args.end}.md"
    write_report(result, output)
    pre = result["pre"].row()
    post = result["post"].row()
    print(
        f"wrote={output} races={result['races_evaluated']} "
        f"pre_mae={pre['mae']} post_mae={post['mae']} "
        f"pre_top={pre['top']} post_top={post['top']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
