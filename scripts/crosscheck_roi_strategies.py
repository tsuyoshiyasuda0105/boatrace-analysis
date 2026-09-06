"""Cross-check imported legacy ROI strategy matches against Kachisuji search.

This verification tool is intentionally read-only.  The legacy registry and
daily evaluator are recovered from the Flask view closure so that their
decision logic is executed, not copied.  Kachisuji conditions are evaluated by
``src.search.roi_search.search_roi``; a second query compiled by that module is
used only to attach race IDs to the public aggregate result.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
import inspect
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from typing import Any, Iterable, Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.search.roi_search import _compile_conditions, search_roi  # noqa: E402


DEFAULT_LEGACY_DB = REPO_ROOT / "data" / "boatrace.db"
DEFAULT_SEARCH_DB = REPO_ROOT / "data" / "kachisuji_search.db"
DEFAULT_FROM = "2026-07-01"
DEFAULT_TO = "2026-07-31"


@dataclass(frozen=True)
class CrosscheckSpec:
    key: str
    reproduction: str
    conditions: Mapping[str, Any] | None
    unsupported: tuple[str, ...] = ()


def _bet(kind: str, *boats: int) -> dict[str, Any]:
    keys = ("first", "second", "third")
    return {"type": kind, **{keys[index]: boat for index, boat in enumerate(boats)}}


# Exact A conditions.  These are transcriptions of strategy *data*, not legacy
# evaluator code; the actual legacy decisions always come from the imported
# ``_l4_daily_stats`` closure.
EXACT_SPECS: dict[str, CrosscheckSpec] = {
    "wakamatsu_13_weak2_strong3_exa": CrosscheckSpec(
        "wakamatsu_13_weak2_strong3_exa", "A",
        {
            "venue": 20,
            "boats": {
                "1": {"motor_rate2": {"min": 35}},
                "2": {"avg_st": {"min": 0.17}},
                "3": {"motor_rate2": {"min": 40}},
            },
            "wind_speed": {"max": 3},
            "bet": _bet("nirentan", 1, 3),
        },
    ),
    "tamagawa_13_acc2n30_m3_40_exa": CrosscheckSpec(
        "tamagawa_13_acc2n30_m3_40_exa", "A",
        {
            "venue": 5,
            "boats": {
                "1": {"class": ["A1"]},
                "2": {"accident_rate": {"min": 0.5}, "national_rate2": {"min": 30}},
                "3": {"motor_rate2": {"min": 40}},
            },
            "bet": _bet("nirentan", 1, 3),
        },
    ),
    "kojima_12_acc3_m3_n23_exa": CrosscheckSpec(
        "kojima_12_acc3_m3_n23_exa", "A",
        {
            "venue": 16,
            "boats": {
                "1": {"class": ["A1"]},
                "2": {"national_rate2": {"min": 32}},
                "3": {
                    "accident_rate": {"min": 0.5},
                    "motor_rate2": {"min": 35},
                    "national_rate2": {"min": 30},
                },
            },
            "bet": _bet("nirentan", 1, 2),
        },
    ),
    "edogawa_13_acc2_n23_m3_exa": CrosscheckSpec(
        "edogawa_13_acc2_n23_m3_exa", "A",
        {
            "venue": 3,
            "boats": {
                "1": {"class": ["A1"]},
                "2": {"accident_rate": {"min": 0.5}, "national_rate2": {"min": 30}},
                "3": {"national_rate2": {"min": 28}, "motor_rate2": {"min": 35}},
            },
            "bet": _bet("nirentan", 1, 3),
        },
    ),
}


PARTIAL_SPECS: dict[str, CrosscheckSpec] = {
    "a1_ace_motor_123_corr_tri": CrosscheckSpec(
        "a1_ace_motor_123_corr_tri", "B",
        {
            "boats": {
                "1": {"class": ["A1"], "motor_rate2": {"min": 45}},
                "2": {"motor_rate2": {"min": 35}},
                "4": {"class": ["B1", "B2"], "avg_st": {"min": 0.17}},
            },
            "bet": _bet("sanrentan", 1, 2, 3),
        },
        ("会場除外(5,7,12,21,24)を単一条件JSONで表現不可",),
    ),
    "omura_132_weak2_ex3_tri": CrosscheckSpec(
        "omura_132_weak2_ex3_tri", "B",
        {
            "venue": 24,
            "boats": {
                "1": {"national_rate": {"min": 7}},
                "2": {"motor_rate2": {"max": 35}},
                "3": {"national_rate": {"min": 6}},
            },
            "wind_speed": {"max": 3},
            "compare": [{"metric": "ex_time", "boat": 3, "op": "le", "other": 2, "margin": 0}],
            "bet": _bet("sanrentan", 1, 3, 2),
        },
        ("既存の2号艇モーター<35を検索JSONの<=35で近似",),
    ),
}


# Registry-wide capability classification.  Every imported ROI_STRATEGIES key
# must appear exactly once; tests and runtime abort on drift.
B_KEYS = {
    "a1_ace_motor_123_corr_tri", "hamanako_14_exa", "omura_14_exa",
    "tokuyama_123_tri", "shimonoseki_132_tri", "kojima_124_tri",
    "kojima_13_exa", "marugame_123_tri", "tokoname_12_late_a_exa",
    "tokoname_14_winter_exa", "tokoname_123_late_exst_tri",
    "tri134_acc2_ex3_tri", "omura_132_weak2_ex3_tri",
    "heiwajima_13_acc2_late_exa", "marugame_123_weak4_t5_tri",
    "marugame_123_late_weak4_t5_tri", "edogawa_132_weak4_t5_tri",
    "karatsu_123_weak4_t5_tri", "suminoe_124_weak3_t5_tri",
    "tamagawa_123_fl3_n3_30_m2_35_tri", "hamanako_12_pts3_m23_exa",
    "kiryu_13_fl2_n23_exa", "ashiya_13_pts2_m23_exa",
    "amagasaki_12_acc3_fl3_exa", "omura_13_acc2_fl2_m23_exa",
    "marugame_13_pts2_m23_exa", "toda_dent2_makuri4_41",
    "toda_a_accident2_13_exa", "edogawa_late_dent2_makuri3_31",
    "edogawa_a_accident4_12_exa", "biwako_dent2_makuri3_31",
    "amagasaki_dent3_makuri4_41", "shimonoseki_a_accident4_13_exa",
}

C_REASONS = {
    "g23_optb_tri": "G2/G3、会場別上限、複数時点オッズ帯が未対応",
    "gmkf_132_tri": "180日ローリングの選手・モーター攻めスコアが未対応",
    "shimonoseki_123_tri": "専用履歴 evaluator の軸力・展示順位条件が未対応",
    "tsu_124_tri": "専用履歴 evaluator の条件が未対応",
    "amagasaki_143_tri": "展示タイム順位・平均との差条件が未対応",
    "amagasaki_13_exa": "一般戦区分と厳密な除外/境界条件が未対応",
    "omura_123_tri": "専用履歴 evaluator の軸力・展示順位条件が未対応",
    "omura_132_tri": "選手・モーターの180日ローリング攻めスコアが未対応",
    "omura_13_exa": "180日選手逃げ率・モーター展示改善量が未対応",
    "ashiya_boat4_exa": "選手/モーターの過去展示平均との差が未対応",
    "tokuyama_13_exa": "180日選手逃げ率・モーター展示改善量が未対応",
    "tokuyama_12a_exa": "展示ST順位が未対応",
    "tsu_123_tri": "専用履歴 evaluator の軸力・展示順位条件が未対応",
    "suminoe_123_tri": "専用履歴 evaluator の軸力・展示順位条件が未対応",
    "miyajima_tide_132_tri": "数値潮位差・波高条件が未対応",
    "gamagori_tide_132_tri": "数値潮位差・潮位レンジ条件が未対応",
    "marugame_tide_123_tri": "数値潮位レンジ条件が未対応",
    "fukuoka_tide_132_tri": "数値潮位差・潮位レンジ条件が未対応",
    "fukuoka_ex12_b_exa": "直線/周回タイム順位が未対応",
    "fukuoka_tri124_c": "直線/周回タイム順位が未対応",
    "fukuoka_123_late_foot_tri": "直線/周回タイム順位が未対応",
    "gamagori_123_general_practical_tri": "数値潮位差と一般戦区分が未対応",
    "gamagori_13_exa": "数値潮位レンジ条件が未対応",
    "toda_123_tri": "一般戦区分とT-5以外を含む既存オッズ集合が未対応",
    "tsu_143_tri": "一般戦区分等が未対応",
    "kojima_123_tri": "一般戦区分とT-5以外のオッズ時点が未対応",
    "gamagori_123_tri": "一般戦区分とT-1/T-2オッズが未対応",
    "naruto_123_tri": "一般戦区分とT-1/T-2オッズが未対応",
    "karatsu_132_tri": "一般戦区分とT-1/T-2オッズが未対応",
    "tamagawa_13_weak_sashi2_exa": "2コース過去勝率が未対応",
    "omura_124_original_t5_tri": "直線/周回タイム順位が未対応",
}

COURSE_FIT_KEYS = {
    "tokoname_coursefit_boat2_win", "tokoname_coursefit_boat3_general_win",
    "biwako_coursefit_boat4_gap10_general_win", "shimonoseki_coursefit_boat2_win",
    "biwako_coursefit_boat4_gap5_general_win", "biwako_coursefit_boat4_rank1_general_win",
    "biwako_coursefit_boat4_gap10_all_win",
}
WALL_KEYS = {
    "nov_wall_break_31_41_exa", "marugame_wall_hold_123_tri",
    "miyajima_wall_break_31_41_exa", "july_wall_hold_12_exa",
    "shimonoseki_late_wall_hold_12_exa", "hamanako_wall_hold_12_exa",
    "miyajima_wall_hold_123_132_tri", "g23_wall_hold_12_exa",
    "tamagawa_late_wall_hold_123_132_tri",
}
ACE_KEYS = {
    "kiryu_win4_ace_kimarite_late", "amagasaki_win3_ace_kimarite_late",
    "amagasaki_win3_ace_kimarite_m40", "amagasaki_win3_ace_kimarite_no_rain",
    "amagasaki_win3_ace_kimarite_late_no_rain", "amagasaki_win3_ace_kimarite_all",
    "naruto_win4_ace_kimarite_all", "naruto_win4_ace_kimarite_no_rain",
    "naruto_win3_ace_kimarite_late_no_rain", "ashiya_win4_ace_kimarite_no_rain",
}
C_REASONS.update({key: "合成fit/dash/stretch/turnスコアが未対応" for key in COURSE_FIT_KEYS})
C_REASONS.update({key: "180日ST分散・2コース率・差し率による壁スコアが未対応" for key in WALL_KEYS})
C_REASONS.update({key: "コース別まくり/まくり差し勝利数と合算率が未対応" for key in ACE_KEYS})


def capability_for(key: str) -> tuple[str, tuple[str, ...]]:
    if key in EXACT_SPECS:
        return "A", ()
    if key in B_KEYS:
        detail = PARTIAL_SPECS.get(key)
        return "B", detail.unsupported if detail else ("主要条件は表現可能だが一部条件が未対応",)
    if key in C_REASONS:
        return "C", (C_REASONS[key],)
    raise KeyError(f"unclassified ROI strategy: {key}")


@contextmanager
def readonly_connection(db_path: str | Path):
    resolved = Path(db_path).resolve()
    conn = sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True, timeout=30)
    try:
        conn.execute("PRAGMA query_only=ON")
        if conn.execute("PRAGMA query_only").fetchone()[0] != 1:
            raise RuntimeError("SQLite query_only guard is not active")
        yield conn
    finally:
        conn.close()


def _legacy_runtime(db_path: str | Path):
    # Set (rather than pop) before importing app/config so python-dotenv cannot
    # refill DATABASE_URL from .env.  This is the no-Supabase safety boundary.
    os.environ["DATABASE_URL"] = ""
    import src.web.app as legacy_app

    resolved = Path(db_path).resolve()

    def connect_local_readonly(*_args, **_kwargs):
        conn = sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True, timeout=30)
        conn.execute("PRAGMA query_only=ON")
        if conn.execute("PRAGMA query_only").fetchone()[0] != 1:
            conn.close()
            raise RuntimeError("legacy SQLite query_only guard is not active")
        return conn

    legacy_app.db_connect = connect_local_readonly
    app = legacy_app.create_app()
    view = app.view_functions["member_strategy"]
    while hasattr(view, "__wrapped__"):
        view = view.__wrapped__
    closure = inspect.getclosurevars(view).nonlocals
    return closure["ROI_STRATEGIES"], closure["BET_UNIT_MAP"], closure["_l4_daily_stats"]


def legacy_results(
    db_path: str | Path, date_from: str, date_to: str
) -> tuple[list[dict[str, Any]], dict[str, int], list[dict[str, Any]]]:
    registry, bet_units, evaluator = _legacy_runtime(db_path)
    dump_path = Path(tempfile.gettempdir()) / "kachisuji_step10_legacy_signals.jsonl"
    if dump_path.exists():
        raise RuntimeError(f"refusing to overwrite existing dump: {dump_path}")
    os.environ["BOATRACE_ADOPTED_SIGNAL_DUMP"] = str(dump_path)
    try:
        evaluator(date_from, date_to, force_full_scan=True)
        signals = [
            json.loads(line)
            for line in dump_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ] if dump_path.exists() else []
    finally:
        os.environ.pop("BOATRACE_ADOPTED_SIGNAL_DUMP", None)
        if dump_path.exists():
            dump_path.unlink()
    return list(registry), dict(bet_units), signals


def _search_rows_with_ids(
    db_path: str | Path, conditions: Mapping[str, Any]
) -> list[dict[str, Any]]:
    where, params, null_columns, bet, odds = _compile_conditions(conditions)
    null_expression = " OR ".join(f"{column} IS NULL" for column in null_columns) or "0"
    join_sql = ""
    odds_filter = ""
    query_params: list[Any] = []
    if odds is not None:
        join_sql = (
            " LEFT JOIN odds_snapshot AS ticket_odds"
            " ON ticket_odds.race_id = asof.race_id"
            " AND ticket_odds.combination = ? AND ticket_odds.snapshot = ?"
        )
        # オッズ絞り込みは廃止済みの経路。複数点では代表として先頭の点を使う。
        query_params.extend((str(bet.expected[0]), odds.snapshot))
        comparisons = []
        if odds.minimum is not None:
            comparisons.append("ticket_odds.odds >= ?")
            params.append(odds.minimum)
        if odds.maximum is not None:
            comparisons.append("ticket_odds.odds <= ?")
            params.append(odds.maximum)
        odds_filter = " AND ((" + " AND ".join(comparisons) + ") OR ticket_odds.odds IS NULL)"
        null_expression = f"({null_expression}) OR ticket_odds.odds IS NULL"
    sql = (
        f"SELECT race_id,race_date,schema_version,{bet.result_column},{bet.payout_column},"
        f"{bet.result_column}_json,{bet.payout_column}_json,"
        f"CASE WHEN {null_expression} THEN 1 ELSE 0 END "
        f"FROM asof_race_features AS asof{join_sql} WHERE {where}{odds_filter}"
    )
    included = []
    with readonly_connection(db_path) as conn:
        for race_id, race_date, schema, result, payout, result_json, payout_json, condition_null in conn.execute(
            sql, [*query_params, *params]
        ):
            if condition_null:
                continue
            if int(schema) >= 4:
                try:
                    winners = json.loads(result_json)
                    payouts = json.loads(payout_json)
                    if not isinstance(winners, list) or not winners or not isinstance(payouts, dict):
                        continue
                    if any(not isinstance(value, str) or value not in payouts for value in winners):
                        continue
                    # 指定した点のうち当たったものを全部足す (同着で複数当たりうる)。
                    hit = False
                    pay = 0.0
                    for ticket in bet.expected:
                        if str(ticket) in winners:
                            hit = True
                            pay += float(payouts[str(ticket)])
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
            else:
                if result is None or payout is None:
                    continue
                hit = False
                pay = 0.0
                for ticket in bet.expected:
                    if result == ticket:
                        hit = True
                        pay += float(payout)
            included.append({"race_id": str(race_id), "date": str(race_date), "hit": bool(hit), "pay": pay})
    return included


def search_results(db_path: str | Path, conditions: Mapping[str, Any]) -> dict[str, Any]:
    aggregate = search_roi(db_path, conditions, fast=True)
    rows = _search_rows_with_ids(db_path, conditions)
    n = len(rows)
    hits = sum(row["hit"] for row in rows)
    # ROI% = 払戻合計 ÷ (レース数 × 点数)。search_roi と同じ定義に揃える。
    # 点数は入力の買い目の個数で、検算したい ROI そのものではないので、
    # aggregate から受け取っても照合の独立性は失われない。
    ticket_count = int(aggregate.get("ticket_count") or 1)
    roi = round(sum(row["pay"] for row in rows) / (n * ticket_count), 1) if n else 0.0
    if (n, hits, roi) != (aggregate["n"], aggregate["hits"], aggregate["roi"]):
        raise RuntimeError("race-ID attachment query diverged from search_roi aggregate")
    return {**aggregate, "races": rows, "race_ids": sorted(row["race_id"] for row in rows)}


def _legacy_aggregate(signals: Iterable[Mapping[str, Any]], key: str, unit: int) -> dict[str, Any]:
    rows = [dict(signal) for signal in signals if signal.get("key") == key]
    n = len(rows)
    pay = sum(float(row.get("pay") or 0) for row in rows)
    return {
        "n": n,
        "hits": sum(bool(row.get("hit")) for row in rows),
        "roi": round(pay / (unit * n) * 100.0, 1) if n else 0.0,
        "races": rows,
        "race_ids": sorted(str(row["race_id"]) for row in rows),
    }


def run_crosscheck(
    legacy_db: str | Path,
    search_db: str | Path,
    date_from: str,
    date_to: str,
    keys: Iterable[str] | None = None,
) -> dict[str, Any]:
    registry, bet_units, signals = legacy_results(legacy_db, date_from, date_to)
    registry_by_key = {str(item["key"]): dict(item) for item in registry}
    for key in registry_by_key:
        capability_for(key)
    requested = list(keys or [*EXACT_SPECS, *PARTIAL_SPECS])
    selected_by_race = {str(signal["race_id"]): str(signal["key"]) for signal in signals}
    results = []
    for key in requested:
        if key not in registry_by_key:
            raise KeyError(f"unknown ROI strategy key: {key}")
        spec = EXACT_SPECS.get(key) or PARTIAL_SPECS.get(key)
        if spec is None or spec.conditions is None:
            raise ValueError(f"strategy has no executable Kachisuji condition: {key}")
        conditions = {**spec.conditions, "date_from": date_from, "date_to": date_to}
        legacy = _legacy_aggregate(signals, key, int(bet_units.get(key, 100) or 100))
        search = search_results(search_db, conditions)
        legacy_ids = set(legacy["race_ids"])
        search_ids = set(search["race_ids"])
        only_legacy = sorted(legacy_ids - search_ids)
        only_search = sorted(search_ids - legacy_ids)
        dedup_examples = [
            {"race_id": race_id, "selected_legacy_key": selected_by_race[race_id]}
            for race_id in only_search
            if race_id in selected_by_race and selected_by_race[race_id] != key
        ][:10]
        same_ids = legacy_ids == search_ids
        if legacy["n"] == search["n"] and legacy["hits"] == search["hits"] and same_ids:
            verdict = "一致" if abs(legacy["roi"] - search["roi"]) < 0.05 else (
                "軽微差" if abs(legacy["roi"] - search["roi"]) < 0.5 else "不一致"
            )
        else:
            verdict = "不一致"
        causes = []
        if verdict != "一致":
            if spec.reproduction == "B":
                causes.append("condition-gap")
            if (only_legacy or only_search) and spec.reproduction == "A":
                causes.append("data-source")
            if not causes:
                causes.append("unknown")
        results.append({
            "key": key,
            "label": registry_by_key[key]["label"],
            "reproduction": spec.reproduction,
            "unsupported": list(spec.unsupported),
            "conditions": conditions,
            "legacy": legacy,
            "search": search,
            "verdict": verdict,
            "causes": causes,
            "only_legacy": only_legacy[:10],
            "only_search": only_search[:10],
            "dedup_examples": dedup_examples,
        })
    capability_counts = Counter(capability_for(key)[0] for key in registry_by_key)
    return {
        "period": [date_from, date_to],
        "registry_count": len(registry_by_key),
        "capability_counts": dict(sorted(capability_counts.items())),
        "selected_legacy_races": len({str(signal["race_id"]) for signal in signals}),
        "results": results,
        "unexecuted": [
            {
                "key": key,
                "label": registry_by_key[key]["label"],
                "reproduction": capability_for(key)[0],
                "reason": list(capability_for(key)[1]),
            }
            for key in registry_by_key if key not in requested
        ],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy-db", type=Path, default=DEFAULT_LEGACY_DB)
    parser.add_argument("--search-db", type=Path, default=DEFAULT_SEARCH_DB)
    parser.add_argument("--from", dest="date_from", default=DEFAULT_FROM)
    parser.add_argument("--to", dest="date_to", default=DEFAULT_TO)
    parser.add_argument("--key", action="append", dest="keys")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = _parser().parse_args(argv)
    result = run_crosscheck(args.legacy_db, args.search_db, args.date_from, args.date_to, args.keys)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"period={args.date_from}..{args.date_to} registry={result['registry_count']}")
        for row in result["results"]:
            print(
                f"{row['key']}: legacy N={row['legacy']['n']} hit={row['legacy']['hits']} ROI={row['legacy']['roi']:.1f}; "
                f"search N={row['search']['n']} hit={row['search']['hits']} ROI={row['search']['roi']:.1f}; "
                f"{row['verdict']} {','.join(row['causes']) or '-'}"
            )
            if row["only_legacy"] or row["only_search"]:
                print(f"  only_legacy={row['only_legacy']} only_search={row['only_search']}")
            if row["dedup_examples"]:
                print(f"  cross_strategy_selection={row['dedup_examples']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
