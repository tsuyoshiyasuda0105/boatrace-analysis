"""Overlay adopted accident-dent strategy metrics onto the daily ROI cache.

This intentionally leaves every unrelated strategy field untouched. It is much
cheaper than recomputing the complete ROI dashboard when these strategies are
added or when yesterday's results arrive.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.db.connection import connect as db_connect
from src.evaluation.accident_dent_strategy import (
    ACCIDENT_DENT_CACHE_VERSION,
    ACCIDENT_DENT_STRATEGIES,
    iter_backtest_matches,
)


JST = ZoneInfo("Asia/Tokyo")


def _now_jst() -> datetime:
    return datetime.now(JST)


def _today_jst_iso() -> str:
    return _now_jst().date().isoformat()


def patch_daily_stats(stats: dict[str, Any], metrics: dict[str, dict[str, int]]) -> dict[str, Any]:
    """Return one cache payload with all adopted accident strategy fields set."""
    patched = dict(stats)
    for strategy in ACCIDENT_DENT_STRATEGIES:
        values = metrics.get(strategy.key, {})
        bets = int(values.get("bets", 0) or 0)
        hits = int(values.get("hits", 0) or 0)
        pay = int(values.get("pay", 0) or 0)
        patched[f"{strategy.key}_bets"] = bets
        patched[f"{strategy.key}_hits"] = hits
        patched[f"{strategy.key}_pay"] = pay
        patched[f"{strategy.key}_roi"] = (pay - 100 * bets) / (100 * bets) * 100 if bets else None
        patched[f"{strategy.key}_recovery"] = pay / (100 * bets) * 100 if bets else None
        patched[f"{strategy.key}_profit"] = pay - 100 * bets if bets else 0
    patched["_accident_dent_version"] = ACCIDENT_DENT_CACHE_VERSION
    return patched


def collect_daily_metrics(conn, from_date: str, to_date: str) -> dict[str, dict[str, dict[str, int]]]:
    daily: dict[str, dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: {"bets": 0, "hits": 0, "pay": 0})
    )
    for match in iter_backtest_matches(conn, from_date, to_date):
        values = daily[str(match["race_date"])][match["strategy"].key]
        values["bets"] += 1
        values["hits"] += int(bool(match["hit"]))
        values["pay"] += int(match["payout"] or 0)
    return daily


def backfill(from_date: str, to_date: str) -> tuple[int, int]:
    with db_connect() as conn:
        daily = collect_daily_metrics(conn, from_date, to_date)
        rows = conn.execute(
            "SELECT race_date, stats_json FROM l4_daily_stats_cache WHERE race_date BETWEEN ? AND ?",
            (from_date, to_date),
        ).fetchall()
        cached_at = _now_jst().replace(tzinfo=None).isoformat(timespec="seconds")
        updated = 0
        skipped = 0
        for race_date, raw in rows:
            try:
                stats = json.loads(raw)
            except (TypeError, ValueError):
                skipped += 1
                continue
            if not isinstance(stats, dict):
                skipped += 1
                continue
            patched = patch_daily_stats(stats, daily.get(str(race_date), {}))
            conn.execute(
                """
                INSERT OR REPLACE INTO l4_daily_stats_cache (race_date, stats_json, cached_at)
                VALUES (?, ?, ?)
                """,
                (str(race_date), json.dumps(patched, ensure_ascii=False), cached_at),
            )
            updated += 1
        conn.commit()
    return updated, skipped


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="from_date")
    parser.add_argument("--to", dest="to_date")
    parser.add_argument("--recent-days", type=int, default=400)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    to_date = args.to_date or _today_jst_iso()
    from_date = args.from_date or (
        date.fromisoformat(to_date) - timedelta(days=max(1, args.recent_days) - 1)
    ).isoformat()
    if date.fromisoformat(to_date) < date.fromisoformat(from_date):
        raise ValueError("--to must be on or after --from")
    updated, skipped = backfill(from_date, to_date)
    print(
        f"[accident-dent-cache] from={from_date} to={to_date} updated={updated} skipped={skipped}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
