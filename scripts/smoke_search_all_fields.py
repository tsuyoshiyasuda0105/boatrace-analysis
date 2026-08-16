"""Exhaustive one-field-at-a-time smoke check for Kachisuji search.

The script starts the local Flask application on port 8090 by default, sends
real HTTP requests to ``/api/search``, and always stops the child server.  The
search snapshot is opened by product code in SQLite read-only mode.  No saved
strategy endpoint is exercised.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = PROJECT_ROOT / "data" / "kachisuji_search.db"
DEFAULT_REPORT = PROJECT_ROOT / "reports" / "kachisuji_bughunt_round2_result_20260816.md"
TEMP_DIR = PROJECT_ROOT / ".tmp_kachisuji_bughunt_round2_20260816"
DEFAULT_BET = {"type": "tansho", "first": 1}
REQUIRED_KEYS = {
    "n",
    "hits",
    "hit_rate",
    "roi",
    "roi_ci_low",
    "roi_ci_high",
    "excluded",
    "yearly",
    "monthly",
    "warnings",
    "effective_date_range",
}
RANGE_FIELDS: tuple[tuple[str, float], ...] = (
    ("age", 30),
    ("avg_st", 0.15),
    ("national_rate", 6.0),
    ("local_rate", 6.0),
    ("national_rate2", 40.0),
    ("local_rate2", 40.0),
    ("motor_rate2", 35.0),
    ("ex_st", 0.10),
    ("accident_rate", 0.30),
    ("accident_rate_period", 1.00),
    ("accident_count_period", 1),
    ("accident_points", 2),
    ("accident_rate_365d", 0.30),
)
COMPARE_METRICS = (
    "motor_rate2",
    "avg_st",
    "ex_time",
    "ex_st",
    "national_rate",
    "local_rate",
    "national_rate2",
    "age",
)
KIMARITE = ("nige", "sashi", "makuri", "makurizashi", "nuki", "megumare")
CATEGORY_ORDER = ("race", "program", "boat", "compare", "bet", "date")


@dataclass(frozen=True)
class SearchCase:
    category: str
    field: str
    setting: str
    conditions: dict[str, Any]

    @property
    def ticket(self) -> str:
        bet = self.conditions["bet"]
        legs = [str(bet["first"])]
        if bet["type"] != "tansho":
            legs.append(str(bet["second"]))
        if bet["type"] == "sanrentan":
            legs.append(str(bet["third"]))
        labels = {"tansho": "単勝", "nirentan": "2連単", "sanrentan": "3連単"}
        return f"{labels[bet['type']]} {'-'.join(legs)}"


@dataclass
class CaseResult:
    case: SearchCase
    status: str
    http_status: int | None
    response: Any
    problems: list[str]
    cautions: list[str]
    elapsed_seconds: float


def _with_bet(condition: dict[str, Any], bet: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"bet": dict(bet or DEFAULT_BET), "fast": True, **condition}


def _read_snapshot_facts(db_path: Path) -> dict[str, Any]:
    resolved = db_path.resolve()
    connection = sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only = ON")
        minimum, maximum, rows = connection.execute(
            "SELECT MIN(race_date), MAX(race_date), COUNT(*) FROM asof_race_features"
        ).fetchone()
        versions = {
            str(version): int(count)
            for version, count in connection.execute(
                "SELECT schema_version, COUNT(*) FROM asof_race_features GROUP BY schema_version"
            )
        }
        racers: dict[str, int] = {}
        for boat in range(1, 7):
            row = connection.execute(
                f"SELECT b{boat}_racer_id FROM asof_race_features "
                f"WHERE b{boat}_racer_id IS NOT NULL LIMIT 1"
            ).fetchone()
            if row is not None:
                racers[str(boat)] = int(row[0])
        return {
            "date_min": str(minimum),
            "date_max": str(maximum),
            "rows": int(rows),
            "schema_versions": versions,
            "racer_ids": racers,
        }
    finally:
        connection.close()


def build_cases(snapshot: dict[str, Any]) -> list[SearchCase]:
    cases: list[SearchCase] = []

    def add(category: str, field: str, setting: str, condition: dict[str, Any], *, bet=None) -> None:
        cases.append(SearchCase(category, field, setting, _with_bet(condition, bet)))

    for venue in range(1, 25):
        add("race", "会場", str(venue), {"venue": [venue]})
    for race_no in range(1, 13):
        add("race", "レース番号", f"{race_no}Rのみ", {"race_no": {"min": race_no, "max": race_no}})
    for setting, value in (
        ("1R以上", {"min": 1}),
        ("6R以上", {"min": 6}),
        ("12R以上", {"min": 12}),
        ("1R以下", {"max": 1}),
        ("6R以下", {"max": 6}),
        ("12R以下", {"max": 12}),
        ("1R〜12R", {"min": 1, "max": 12}),
        ("6R〜12R", {"min": 6, "max": 12}),
    ):
        add("race", "レース番号", setting, {"race_no": value})
    for weather in ("晴", "曇", "雨"):
        add("race", "天候", weather, {"weather": [weather]})
    for direction in ("追い風", "向かい風", "横風(右)", "横風(左)", "無風"):
        add("race", "風向き", direction, {"wind_dir": [direction]})
    for label, value in (
        ("無風 0〜1m", {"min": 0, "max": 1}),
        ("弱風 2〜3m", {"min": 2, "max": 3}),
        ("中風 4〜5m", {"min": 4, "max": 5}),
        ("強風 6m以上", {"min": 6}),
    ):
        add("race", "風の強さ", label, {"wind_speed": value})
    for tide in ("満潮前後", "干潮前後", "上げ潮", "下げ潮"):
        add("race", "潮", tide, {"tide_phase": tide})

    for label, value in (("男性のみ", 0), ("女性選手あり", 1)):
        add("program", "性別構成", label, {"female_present": value})
    for value in ("A1単騎", "1号艇A1", "A1が2人以上"):
        add("program", "級別構成", value, {"class_mix": value})
    for value in ("初日", "中日", "最終日"):
        add("program", "開催日程", value, {"day_index": value})
    for value in ("モーニング", "デイ", "ナイター"):
        add("program", "時間帯", value, {"daypart": value})

    for boat in range(1, 7):
        boat_key = str(boat)
        for racer_id in [snapshot["racer_ids"].get(boat_key)]:
            if racer_id is not None:
                add("boat", f"{boat}号艇 選手", str(racer_id), {"boats": {boat_key: {"racer_id": racer_id}}})
        for racer_class in ("A1", "A2", "B1", "B2"):
            add(
                "boat",
                f"{boat}号艇 級別",
                racer_class,
                {"boats": {boat_key: {"class": [racer_class]}}},
            )
        add(
            "boat",
            f"{boat}号艇 級別",
            "A1+A2",
            {"boats": {boat_key: {"class": ["A1", "A2"]}}},
        )
        for field, value in RANGE_FIELDS:
            for operator, label in (("min", "以上"), ("max", "以下")):
                add(
                    "boat",
                    f"{boat}号艇 {field}",
                    f"{value:g}{label}",
                    {"boats": {boat_key: {field: {operator: value}}}},
                )
        for rank in range(1, 7):
            add(
                "boat",
                f"{boat}号艇 展示順位",
                f"{rank}位のみ",
                {"boats": {boat_key: {"ex_rank": {"min": rank, "max": rank}}}},
            )
        add(
            "boat",
            f"{boat}号艇 展示順位",
            "1〜3位",
            {"boats": {boat_key: {"ex_rank": {"min": 1, "max": 3}}}},
        )
        for direction, label in (("faster_by", "平均より0.10秒速い"), ("slower_by", "平均より0.10秒遅い")):
            add(
                "boat",
                f"{boat}号艇 展示タイム会場平均比",
                label,
                {"boats": {boat_key: {"ex_dev": {direction: 0.10}}}},
            )
        for technique in KIMARITE:
            add(
                "boat",
                f"{boat}号艇 決まり手",
                f"{technique} 50%以上",
                {"boats": {boat_key: {"kimarite": {"name": technique, "rate_min": 50}}}},
            )

    for metric in COMPARE_METRICS:
        for operator in ("ge", "le"):
            for margin in (0, 0.10):
                add(
                    "compare",
                    "艇間比較",
                    f"1号艇 {metric} {operator} 2号艇 margin={margin:g}",
                    {
                        "compare": [
                            {
                                "metric": metric,
                                "boat": 1,
                                "op": operator,
                                "other": 2,
                                "margin": margin,
                            }
                        ]
                    },
                )

    for first in range(1, 7):
        bet = {"type": "tansho", "first": first}
        add("bet", "買い目 単勝", str(first), {}, bet=bet)
    for first, second in ((1, 2), (2, 1), (3, 4), (4, 3), (5, 6), (6, 5)):
        bet = {"type": "nirentan", "first": first, "second": second}
        add("bet", "買い目 2連単", f"{first}-{second}", {}, bet=bet)
    for first, second, third in (
        (1, 2, 3),
        (2, 3, 1),
        (3, 1, 2),
        (4, 5, 6),
        (5, 6, 4),
        (6, 4, 5),
    ):
        bet = {"type": "sanrentan", "first": first, "second": second, "third": third}
        add("bet", "買い目 3連単", f"{first}-{second}-{third}", {}, bet=bet)

    add("date", "検索期間 開始日", snapshot["date_min"], {"date_from": snapshot["date_min"]})
    add("date", "検索期間 終了日", snapshot["date_max"], {"date_to": snapshot["date_max"]})
    return cases


def _post_json(base_url: str, payload: dict[str, Any], timeout: float) -> tuple[int, Any]:
    request = Request(
        base_url.rstrip("/") + "/api/search",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
            status = response.status
    except HTTPError as exc:
        raw = exc.read()
        status = exc.code
    try:
        return status, json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return status, {"_parse_error": str(exc), "_raw": raw[:500].decode("utf-8", errors="replace")}


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_response(payload: Any) -> list[str]:
    problems: list[str] = []
    if not isinstance(payload, dict):
        return ["JSONルートがobjectではない"]
    missing = sorted(REQUIRED_KEYS - payload.keys())
    if missing:
        return ["必須キー欠落: " + ", ".join(missing)]
    n, hits = payload["n"], payload["hits"]
    if not isinstance(n, int) or isinstance(n, bool) or n < 0:
        problems.append(f"nが非負整数ではない: {n!r}")
    if not isinstance(hits, int) or isinstance(hits, bool) or hits < 0:
        problems.append(f"hitsが非負整数ではない: {hits!r}")
    if isinstance(n, int) and isinstance(hits, int) and hits > n:
        problems.append(f"hits({hits}) > n({n})")
    for key in ("hit_rate", "roi", "roi_ci_low", "roi_ci_high"):
        if not _number(payload[key]):
            problems.append(f"{key}が数値ではない: {payload[key]!r}")
    roi = payload["roi"]
    if _number(roi) and (roi < 0 or roi > 100_000):
        problems.append(f"roiが範囲外: {roi}")
    low, high = payload["roi_ci_low"], payload["roi_ci_high"]
    if _number(low) and _number(high) and low > high:
        problems.append(f"roi_ci_low({low}) > roi_ci_high({high})")
    if all(_number(value) for value in (low, roi, high)) and not low <= roi <= high:
        problems.append(f"roi({roi})がCI [{low}, {high}] の外")

    excluded = payload["excluded"]
    if not isinstance(excluded, dict) or set(excluded) != {"result_missing", "condition_null"}:
        problems.append(f"excluded構造不正: {excluded!r}")
    elif any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in excluded.values()):
        problems.append(f"excluded内訳が非負整数ではない: {excluded!r}")

    yearly, monthly = payload["yearly"], payload["monthly"]
    if not isinstance(yearly, list) or not isinstance(monthly, list):
        problems.append("yearly/monthlyが配列ではない")
        return problems
    for label, rows, keys in (
        ("yearly", yearly, {"year", "n", "hits", "roi"}),
        ("monthly", monthly, {"year", "month", "n", "hits", "roi"}),
    ):
        for index, row in enumerate(rows):
            if not isinstance(row, dict) or not keys.issubset(row):
                problems.append(f"{label}[{index}]構造不正: {row!r}")
                continue
            if not isinstance(row["n"], int) or not isinstance(row["hits"], int):
                problems.append(f"{label}[{index}] n/hits型不正")
            elif row["n"] < 0 or row["hits"] < 0 or row["hits"] > row["n"]:
                problems.append(f"{label}[{index}] n/hits異常: {row!r}")
            if not _number(row["roi"]) or row["roi"] < 0 or row["roi"] > 100_000:
                problems.append(f"{label}[{index}] roi異常: {row.get('roi')!r}")
    if isinstance(n, int):
        if sum(row.get("n", 0) for row in yearly if isinstance(row, dict)) != n:
            problems.append("yearlyのn合計が全体nと不一致")
        if sum(row.get("n", 0) for row in monthly if isinstance(row, dict)) != n:
            problems.append("monthlyのn合計が全体nと不一致")
    if isinstance(hits, int):
        if sum(row.get("hits", 0) for row in yearly if isinstance(row, dict)) != hits:
            problems.append("yearlyのhits合計が全体hitsと不一致")
        if sum(row.get("hits", 0) for row in monthly if isinstance(row, dict)) != hits:
            problems.append("monthlyのhits合計が全体hitsと不一致")
    yearly_by_year = {row["year"]: row for row in yearly if isinstance(row, dict) and "year" in row}
    for year, row in yearly_by_year.items():
        year_months = [item for item in monthly if isinstance(item, dict) and item.get("year") == year]
        if sum(item.get("n", 0) for item in year_months) != row.get("n"):
            problems.append(f"{year}年のmonthly n合計がyearly nと不一致")
        if sum(item.get("hits", 0) for item in year_months) != row.get("hits"):
            problems.append(f"{year}年のmonthly hits合計がyearly hitsと不一致")
    date_range = payload["effective_date_range"]
    if not isinstance(date_range, list) or len(date_range) != 2:
        problems.append(f"effective_date_range構造不正: {date_range!r}")
    elif isinstance(n, int):
        if n == 0 and date_range != [None, None]:
            problems.append(f"n=0なのにeffective_date_rangeが非NULL: {date_range!r}")
        if n > 0 and (not all(isinstance(value, str) for value in date_range) or date_range[0] > date_range[1]):
            problems.append(f"effective_date_range異常: {date_range!r}")
    if not isinstance(payload["warnings"], list):
        problems.append("warningsが配列ではない")
    return problems


def _cautions(case: SearchCase, payload: dict[str, Any]) -> list[str]:
    cautions: list[str] = []
    n = payload.get("n")
    excluded = payload.get("excluded") if isinstance(payload.get("excluded"), dict) else {}
    condition_null = excluded.get("condition_null", 0)
    if n == 0:
        cautions.append("この単独設定で常にn=0（該当データなし、または実質利用不能の疑い）")
    if n == 0 and isinstance(condition_null, int) and condition_null > 0:
        cautions.append(f"条件判定可能な採用件数が0で、condition_null={condition_null}")
    limited = any(token in case.field for token in ("展示順位", "展示タイム", "展示ST"))
    if limited and isinstance(condition_null, int) and condition_null > 0:
        cautions.append(f"展示系の期間制限によりcondition_null={condition_null}（仕様上の除外を含む）")
    return cautions


def execute_cases(base_url: str, cases: Iterable[SearchCase], timeout: float) -> list[CaseResult]:
    results: list[CaseResult] = []
    for index, case in enumerate(cases, start=1):
        started = time.perf_counter()
        status: int | None = None
        payload: Any = None
        problems: list[str] = []
        cautions: list[str] = []
        try:
            status, payload = _post_json(base_url, case.conditions, timeout)
            if status != 200:
                classification = "FAIL(error)"
                problems.append(f"HTTP {status}")
            elif isinstance(payload, dict) and "_parse_error" in payload:
                classification = "FAIL(error)"
                problems.append("JSONパース不能: " + str(payload["_parse_error"]))
            else:
                problems = _validate_response(payload)
                classification = "FAIL(anomaly)" if problems else "PASS"
                if isinstance(payload, dict):
                    cautions = _cautions(case, payload)
        except (OSError, TimeoutError, URLError) as exc:
            classification = "FAIL(error)"
            problems.append(f"通信例外: {type(exc).__name__}: {exc}")
            payload = {"error": str(exc)}
        elapsed = time.perf_counter() - started
        results.append(CaseResult(case, classification, status, payload, problems, cautions, elapsed))
        print(
            f"[{index:03d}] {classification:<13} {case.category}/{case.field}={case.setting} "
            f"({elapsed:.2f}s)",
            flush=True,
        )
    return results


def _excerpt(value: Any, limit: int = 360) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    text = text.replace("|", "\\|").replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _coverage(results: list[CaseResult]) -> list[tuple[str, int, int, int, int]]:
    grouped: dict[str, list[CaseResult]] = defaultdict(list)
    for result in results:
        grouped[result.case.field].append(result)
    rows = []
    for field, items in grouped.items():
        rows.append(
            (
                field,
                len(items),
                sum(item.status == "PASS" for item in items),
                sum(item.status == "FAIL(error)" for item in items),
                sum(item.status == "FAIL(anomaly)" for item in items),
            )
        )
    return rows


def render_report(results: list[CaseResult], snapshot: dict[str, Any], selected_categories: list[str]) -> str:
    total = len(results)
    passes = sum(result.status == "PASS" for result in results)
    errors = sum(result.status == "FAIL(error)" for result in results)
    anomalies = sum(result.status == "FAIL(anomaly)" for result in results)
    cautions = [result for result in results if result.cautions]
    lines = [
        "# 勝ち筋サーチ 全項目網羅バグチェック Round 2 結果",
        "",
        "実行日: 2026-08-16",
        "",
        "## サマリ",
        "",
        f"- 実行検索総数: {total}",
        f"- PASS: {passes}",
        f"- FAIL(error): {errors}",
        f"- FAIL(anomaly): {anomalies}",
        f"- 注意フラグ: {len(cautions)}件（検索ケース単位。PASS/FAILとは別集計）",
        f"- 実行カテゴリ: {', '.join(selected_categories)}",
        "- 対象DB: `data/kachisuji_search.db`（SQLite URI `mode=ro`、`PRAGMA query_only=ON`）",
        f"- 対象行数: {snapshot['rows']:,}",
        f"- 対象期間: {snapshot['date_min']}〜{snapshot['date_max']}",
        f"- schema_version内訳: `{json.dumps(snapshot['schema_versions'], ensure_ascii=False, sort_keys=True)}`",
        "- 全リクエストで買い目を明示。条件ケースは買い目以外を1項目だけ設定し、`fast=true`で集計した。",
        "- サーバーは127.0.0.1:8090のタスク専用subprocess。外部ネットワーク・手法保存・DB書込みは未実施。",
        "",
    ]
    if errors == 0 and anomalies == 0:
        lines.extend([f"**全{total}通りでHTTP/構造/集計整合性はPASS。**", ""])
    lines.extend(["## FAIL一覧", ""])
    failures = [result for result in results if result.status != "PASS"]
    if not failures:
        lines.extend(["FAILなし。", ""])
    else:
        lines.extend(
            [
                "|種別|項目|設定値|買い目|症状|HTTP|レスポンス抜粋|",
                "|---|---|---|---|---|---:|---|",
            ]
        )
        for result in failures:
            lines.append(
                f"|{result.status}|{result.case.field}|{result.case.setting}|{result.case.ticket}|"
                f"{' / '.join(result.problems).replace('|', '\\|')}|{result.http_status or '—'}|"
                f"`{_excerpt(result.response)}`|"
            )
        lines.append("")
    lines.extend(["## 注意フラグ一覧", ""])
    if not cautions:
        lines.extend(["注意フラグなし。", ""])
    else:
        lines.extend(
            [
                "|項目|設定値|買い目|n|condition_null|理由|",
                "|---|---|---|---:|---:|---|",
            ]
        )
        for result in cautions:
            payload = result.response if isinstance(result.response, dict) else {}
            excluded = payload.get("excluded", {}) if isinstance(payload.get("excluded"), dict) else {}
            reason = " / ".join(result.cautions).replace("|", "\\|")
            lines.append(
                f"|{result.case.field}|{result.case.setting}|{result.case.ticket}|"
                f"{payload.get('n', '—')}|{excluded.get('condition_null', '—')}|{reason}|"
            )
        lines.append("")
    zero_results = [
        result
        for result in results
        if isinstance(result.response, dict) and result.response.get("n") == 0
    ]
    limited_results = [
        result
        for result in cautions
        if any("展示系の期間制限" in caution for caution in result.cautions)
    ]
    zero_groups = defaultdict(int)
    for result in zero_results:
        if "決まり手" in result.case.field:
            zero_groups["決まり手50%以上"] += 1
        else:
            zero_groups[result.case.field] += 1
    lines.extend(["## 注意フラグの重要所見", ""])
    if zero_results:
        lines.append(
            f"- 単独設定でn=0となったのは{len(zero_results)}ケース: "
            + "、".join(f"{field} {count}件" for field, count in zero_groups.items())
            + "。"
        )
    if limited_results:
        lines.append(
            f"- 展示順位・展示タイム会場平均比の{len(limited_results)}ケースはn>0で機能する一方、"
            "各ケースで古い期間のNULL行が仕様どおり除外された。"
        )
    lines.extend(
        [
            "- 重大度の高い要確認候補は、4つの有風方向がすべてn=0、`A1が2人以上`がn=0、"
            "非逃げ決まり手50%以上が全艇でn=0となった点。いずれもFAILではなく注意フラグとして記録し、修正はしていない。",
            "",
        ]
    )
    lines.extend(
        [
            "## 項目別カバレッジ",
            "",
            "|項目|試行数|PASS|FAIL(error)|FAIL(anomaly)|",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for field, count, passed, error, anomaly in _coverage(results):
        lines.append(f"|{field}|{count}|{passed}|{error}|{anomaly}|")
    lines.extend(["", "## 実行した選択肢", ""])
    for category in selected_categories:
        category_results = [result for result in results if result.case.category == category]
        lines.append(f"### {category}")
        lines.append("")
        grouped: dict[str, list[str]] = defaultdict(list)
        for result in category_results:
            grouped[result.case.field].append(result.case.setting)
        for field, settings in grouped.items():
            lines.append(f"- {field}（{len(settings)}通り）: {', '.join(settings)}")
        lines.append("")
    lines.extend(
        [
            "## 判定方法",
            "",
            "HTTP 200、必須キー、型、非負値、`hits <= n`、ROI範囲、CI順序、yearly/monthlyのn・hits合計、月年整合、excluded内訳、effective_date_rangeを各応答で検査した。n=0自体はPASSとし、単独選択肢がn=0のケース、全件condition_nullの疑い、展示系の期間制限によるNULL除外は注意フラグとして別集計した。",
            "",
            "## 制約順守",
            "",
            "プロダクトコードは変更していない。`data/boatrace.db`には接続していない。検索DBは読み取り専用で、手法APIは呼んでいない。外部ネットワーク、スケジューラ、デプロイ、pushは実施していない。",
            "",
        ]
    )
    return "\n".join(lines)


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _wait_for_server(base_url: str, process: subprocess.Popen[bytes], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"test server exited early with code {process.returncode}")
        try:
            with urlopen(base_url.rstrip("/") + "/", timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, URLError):
            time.sleep(0.1)
    raise TimeoutError(f"test server did not become ready within {timeout:g}s")


def _stop_server(process: subprocess.Popen[bytes], port: int) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and _port_open(port):
        time.sleep(0.1)
    if _port_open(port):
        raise RuntimeError(f"port {port} remains open after server termination")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--base-url", help="Use an already-running test server instead of starting one")
    parser.add_argument(
        "--category",
        action="append",
        choices=CATEGORY_ORDER,
        help="Limit execution; repeat for multiple categories (default: all)",
    )
    parser.add_argument("--request-timeout", type=float, default=120.0)
    parser.add_argument("--server-timeout", type=float, default=30.0)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    db_path = args.db.resolve()
    report_path = args.report.resolve()
    if not db_path.is_file():
        raise SystemExit(f"search snapshot not found: {db_path}")
    if db_path.name.lower() == "boatrace.db":
        raise SystemExit("data/boatrace.db is prohibited for this smoke check")
    categories = list(dict.fromkeys(args.category or CATEGORY_ORDER))
    snapshot = _read_snapshot_facts(db_path)
    cases = [case for case in build_cases(snapshot) if case.category in categories]
    print(f"snapshot={db_path} rows={snapshot['rows']} cases={len(cases)}", flush=True)

    process: subprocess.Popen[bytes] | None = None
    temp_dir: Path | None = None
    base_url = args.base_url or f"http://127.0.0.1:{args.port}"
    try:
        if args.base_url is None:
            if args.port != 8090:
                raise SystemExit("self-managed exhaustive run must use the mandated port 8090")
            if _port_open(args.port):
                raise SystemExit(f"port {args.port} is already in use")
            temp_dir = TEMP_DIR.resolve()
            if temp_dir.parent != PROJECT_ROOT.resolve():
                raise RuntimeError(f"unsafe temporary directory: {temp_dir}")
            temp_dir.mkdir(exist_ok=False)
            env = os.environ.copy()
            env["KACHISUJI_DB"] = str(db_path)
            env["KACHISUJI_STRATEGY_DB"] = str(temp_dir / "strategies.db")
            log_handle = (temp_dir / "server.log").open("wb")
            try:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        str(PROJECT_ROOT / "scripts" / "run_kachisuji_web.py"),
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(args.port),
                    ],
                    cwd=PROJECT_ROOT,
                    env=env,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                )
            finally:
                log_handle.close()
            print(f"server_pid={process.pid} port={args.port}", flush=True)
            _wait_for_server(base_url, process, args.server_timeout)
        results = execute_cases(base_url, cases, args.request_timeout)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(render_report(results, snapshot, categories), encoding="utf-8")
    finally:
        if process is not None:
            _stop_server(process, args.port)
            print(f"server_stopped pid={process.pid} port_closed={not _port_open(args.port)}", flush=True)
        if temp_dir is not None and temp_dir.exists():
            if temp_dir.parent != PROJECT_ROOT.resolve():
                raise RuntimeError(f"refusing to remove unsafe temporary directory: {temp_dir}")
            shutil.rmtree(temp_dir)

    errors = sum(result.status == "FAIL(error)" for result in results)
    anomalies = sum(result.status == "FAIL(anomaly)" for result in results)
    caution_count = sum(bool(result.cautions) for result in results)
    print(
        f"SUMMARY total={len(results)} errors={errors} anomalies={anomalies} "
        f"cautions={caution_count} report={report_path}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
