"""Evaluate pre/post exhibition start prediction accuracy.

The command rebuilds point-in-time snapshots for each race instead of reading
previously saved prediction rows. This makes the report useful after changing
feature logic and keeps the evaluation independent from production caches.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "")

import config
from src.db.connection import connect
from src.start_prediction.features import PointInTimeFeatureBuilder
from src.start_prediction.models import MODEL_VERSIONS, RuleEnsembleV1


REPORT_DIR = Path(__file__).resolve().parents[1] / "reports"


@dataclass
class StageMetrics:
    boat_rows: int = 0
    races: int = 0
    abs_error_sum: float = 0.0
    sq_error_sum: float = 0.0
    error_sum: float = 0.0
    within_003: int = 0
    within_005: int = 0
    start_top_hit: int = 0
    start_top2_hit: int = 0
    start_top3_hit: int = 0
    winner_hit: int = 0
    pred_top_probability_sum: float = 0.0
    groups: dict[str, "StageMetrics"] = field(default_factory=dict)

    def add_boat_error(self, pred: float, actual: float) -> None:
        err = pred - actual
        self.boat_rows += 1
        self.abs_error_sum += abs(err)
        self.sq_error_sum += err * err
        self.error_sum += err
        if abs(err) <= 0.03:
            self.within_003 += 1
        if abs(err) <= 0.05:
            self.within_005 += 1

    def add_race(self, top_hit: bool, top2_hit: bool, top3_hit: bool, winner_hit: bool, top_prob: float) -> None:
        self.races += 1
        self.start_top_hit += int(top_hit)
        self.start_top2_hit += int(top2_hit)
        self.start_top3_hit += int(top3_hit)
        self.winner_hit += int(winner_hit)
        self.pred_top_probability_sum += top_prob

    def as_row(self) -> dict[str, Any]:
        return {
            "races": self.races,
            "boat_rows": self.boat_rows,
            "mae": self.abs_error_sum / self.boat_rows if self.boat_rows else None,
            "rmse": math.sqrt(self.sq_error_sum / self.boat_rows) if self.boat_rows else None,
            "bias": self.error_sum / self.boat_rows if self.boat_rows else None,
            "within_003": self.within_003 / self.boat_rows if self.boat_rows else None,
            "within_005": self.within_005 / self.boat_rows if self.boat_rows else None,
            "start_top": self.start_top_hit / self.races if self.races else None,
            "start_top2": self.start_top2_hit / self.races if self.races else None,
            "start_top3": self.start_top3_hit / self.races if self.races else None,
            "winner": self.winner_hit / self.races if self.races else None,
            "avg_top_prob": self.pred_top_probability_sum / self.races if self.races else None,
        }


def _fmt(value: Any, digits: int = 3, suffix: str = "") -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.{digits}f}{suffix}"
    return str(value)


def _pct(value: Any, digits: int = 1) -> str:
    if value is None:
        return "-"
    return f"{float(value) * 100:.{digits}f}%"


def _dict_rows(cur) -> list[dict[str, Any]]:
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def race_ids(conn, start: str, end: str, limit: int | None) -> list[str]:
    sql = """SELECT r.race_id
               FROM races r
               JOIN race_results rr ON rr.race_id=r.race_id
              WHERE r.race_date BETWEEN ? AND ?
                AND rr.start_timing IS NOT NULL
              GROUP BY r.race_id
             HAVING COUNT(rr.boat_number)=6
              ORDER BY r.race_date,r.stadium_number,r.race_number"""
    params: tuple[Any, ...] = (start, end)
    if limit:
        sql += " LIMIT ?"
        params += (limit,)
    return [str(row[0]) for row in conn.execute(sql, params).fetchall()]


def actual_rows(conn, race_id: str) -> dict[int, dict[str, Any]]:
    rows = _dict_rows(conn.execute(
        """SELECT boat_number, start_timing, finishing_position, kimarite
             FROM race_results
            WHERE race_id=?
            ORDER BY boat_number""",
        (race_id,),
    ))
    return {int(row["boat_number"]): row for row in rows}


def group_labels(features: dict[str, Any]) -> list[str]:
    labels = []
    if int(features.get("course_recent10_count") or 0) >= 3:
        labels.append("course_recent10>=3")
    else:
        labels.append("course_recent10<3")
    if int(features.get("racer_exhibition_bias_count") or 0) >= 5:
        labels.append("racer_ex_bias>=5")
        bias = features.get("racer_exhibition_bias_recent20")
        if bias is not None and float(bias) >= 0.04:
            labels.append("racer_show_fast_then_actual_late")
        if bias is not None and float(bias) <= 0.00:
            labels.append("racer_show_slow_then_actual_fast")
    else:
        labels.append("racer_ex_bias<5")
    if int(features.get("motor_exhibition_bias_count") or 0) >= 6:
        labels.append("motor_bias>=6")
    if features.get("preinspection_time") is not None:
        labels.append("preinspection_available")
    if int(features.get("flying_count") or 0) > 0:
        labels.append("flying_count>0")
    if float(features.get("accident_rate") or 0.0) >= 0.7:
        labels.append("accident_rate>=0.7")
    if features.get("exhibition_st") is not None:
        labels.append("exhibition_available")
    return labels


def add_prediction(metrics: StageMetrics, snapshot: dict[str, Any], prediction: dict[str, Any], actual: dict[int, dict[str, Any]]) -> None:
    starts = {
        boat: float(row["start_timing"])
        for boat, row in actual.items()
        if row.get("start_timing") is not None
    }
    if len(starts) != 6:
        return
    actual_start_order = sorted(starts, key=starts.get)
    actual_top = actual_start_order[0]
    actual_first = min(actual.values(), key=lambda x: int(x.get("finishing_position") or 99))["boat_number"]
    pred_order = sorted(prediction["boats"], key=lambda x: int(x["predicted_start_rank"]))
    pred_top = int(pred_order[0]["boat_number"])
    pred_top_set2 = {int(x["boat_number"]) for x in pred_order[:2]}
    pred_top_set3 = {int(x["boat_number"]) for x in pred_order[:3]}
    pred_winner = int(max(prediction["boats"], key=lambda x: float(x["first_probability"]))["boat_number"])
    pred_top_prob = float(next(x for x in prediction["boats"] if int(x["boat_number"]) == pred_top)["start_top_probability"])
    metrics.add_race(
        top_hit=pred_top == actual_top,
        top2_hit=actual_top in pred_top_set2,
        top3_hit=actual_top in pred_top_set3,
        winner_hit=pred_winner == int(actual_first),
        top_prob=pred_top_prob,
    )
    features_by_boat = {int(b["boat_number"]): b for b in snapshot["boats"]}
    for boat_pred in prediction["boats"]:
        boat = int(boat_pred["boat_number"])
        pred_st = float(boat_pred["predicted_st"])
        actual_st = starts.get(boat)
        if actual_st is None:
            continue
        metrics.add_boat_error(pred_st, actual_st)
        for label in group_labels(features_by_boat.get(boat, {})):
            child = metrics.groups.setdefault(label, StageMetrics())
            child.add_boat_error(pred_st, actual_st)


def evaluate(start: str, end: str, limit: int | None, progress_every: int) -> dict[str, Any]:
    with connect(config.DB_PATH) as conn:
        ids = race_ids(conn, start, end, limit)
        builder = PointInTimeFeatureBuilder(conn)
        model = RuleEnsembleV1()
        pre_metrics = StageMetrics()
        post_metrics = StageMetrics()
        failures: list[str] = []
        post_available = 0
        for idx, race_id in enumerate(ids, 1):
            try:
                actual = actual_rows(conn, race_id)
                pre_snapshot = builder.build(race_id, "pre_exhibition").as_dict()
                pre_prediction = model.predict(pre_snapshot)
                add_prediction(pre_metrics, pre_snapshot, pre_prediction, actual)
                post_snapshot = builder.build(race_id, "post_exhibition").as_dict()
                if all(b.get("exhibition_st") is not None and b.get("exhibition_time") is not None for b in post_snapshot["boats"]):
                    post_available += 1
                    post_prediction = model.predict(post_snapshot)
                    add_prediction(post_metrics, post_snapshot, post_prediction, actual)
            except Exception as exc:
                failures.append(f"{race_id}: {type(exc).__name__}: {exc}")
            if progress_every and idx % progress_every == 0:
                print(f"progress {idx}/{len(ids)} failures={len(failures)}", flush=True)
    return {
        "start": start,
        "end": end,
        "model_versions": MODEL_VERSIONS,
        "candidate_races": len(ids),
        "post_available_races": post_available,
        "pre": pre_metrics,
        "post": post_metrics,
        "failures": failures[:30],
        "failure_count": len(failures),
    }


def write_report(result: dict[str, Any], output: Path) -> None:
    pre = result["pre"].as_row()
    post = result["post"].as_row()
    lines = [
        "# 展示前/展示後 ST予測 精度検証",
        "",
        f"- 期間: {result['start']} から {result['end']}",
        f"- 対象レース: {result['candidate_races']}",
        f"- 展示後データあり: {result['post_available_races']}",
        f"- モデル: {result['model_versions']['bundle']}",
        f"- 失敗: {result['failure_count']}",
        "",
        "## 全体精度",
        "",
        "| 段階 | レース | 艇データ | ST MAE | ST RMSE | 平均誤差 | ±0.03内 | ±0.05内 | STトップ | Top2内 | Top3内 | 1着艇 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        f"| 展示前 | {pre['races']} | {pre['boat_rows']} | {_fmt(pre['mae'])} | {_fmt(pre['rmse'])} | {_fmt(pre['bias'])} | {_pct(pre['within_003'])} | {_pct(pre['within_005'])} | {_pct(pre['start_top'])} | {_pct(pre['start_top2'])} | {_pct(pre['start_top3'])} | {_pct(pre['winner'])} |",
        f"| 展示後補正 | {post['races']} | {post['boat_rows']} | {_fmt(post['mae'])} | {_fmt(post['rmse'])} | {_fmt(post['bias'])} | {_pct(post['within_003'])} | {_pct(post['within_005'])} | {_pct(post['start_top'])} | {_pct(post['start_top2'])} | {_pct(post['start_top3'])} | {_pct(post['winner'])} |",
        "",
        "## 改善幅",
        "",
    ]
    if pre["mae"] is not None and post["mae"] is not None:
        lines.append(f"- ST MAE: {_fmt(pre['mae'])} -> {_fmt(post['mae'])} / 改善 {_fmt(pre['mae'] - post['mae'])}")
    if pre["start_top"] is not None and post["start_top"] is not None:
        lines.append(f"- STトップ的中: {_pct(pre['start_top'])} -> {_pct(post['start_top'])} / 差分 {_pct(post['start_top'] - pre['start_top'])}")
    lines.extend([
        "",
        "## 選手特性・データ有無別 ST MAE",
        "",
        "| 区分 | 展示前 n | 展示前 MAE | 展示後 n | 展示後 MAE | 展示後 ±0.03内 |",
        "|---|---:|---:|---:|---:|---:|",
    ])
    labels = sorted(set(result["pre"].groups) | set(result["post"].groups))
    for label in labels:
        p = result["pre"].groups.get(label, StageMetrics()).as_row()
        q = result["post"].groups.get(label, StageMetrics()).as_row()
        lines.append(
            f"| {label} | {p['boat_rows']} | {_fmt(p['mae'])} | {q['boat_rows']} | {_fmt(q['mae'])} | {_pct(q['within_003'])} |"
        )
    if result["failures"]:
        lines.extend(["", "## 失敗サンプル", ""])
        lines.extend(f"- {x}" for x in result["failures"])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2025-01-01")
    parser.add_argument("--end", default="2026-07-23")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--progress-every", type=int, default=1000)
    parser.add_argument("--output")
    args = parser.parse_args()
    result = evaluate(args.start, args.end, args.limit, args.progress_every)
    output = Path(args.output) if args.output else REPORT_DIR / f"start_prediction_st_accuracy_{args.start}_{args.end}.md"
    write_report(result, output)
    pre = result["pre"].as_row()
    post = result["post"].as_row()
    print(
        f"wrote={output} races={result['candidate_races']} post={result['post_available_races']} "
        f"pre_mae={pre['mae']} post_mae={post['mae']} failures={result['failure_count']}",
        flush=True,
    )
    return 0 if result["failure_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
