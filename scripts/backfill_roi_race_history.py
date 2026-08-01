from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.db.connection import connect as db_connect  # noqa: E402
from src.roi_contract import strategy_definition_signature  # noqa: E402
from src.roi_history import replace_roi_history_snapshot  # noqa: E402
from src.web.app import _parse_market_signal_bets_for_roi, create_app  # noqa: E402

JST = ZoneInfo("Asia/Tokyo")


def _choose_snapshots(rows):
    by_date = {}
    for cache_key, html, updated_at in rows:
        race_date = str(cache_key)[-10:]
        by_date.setdefault(race_date, []).append((cache_key, html, int(updated_at or 0)))
    chosen = []
    for race_date, items in sorted(by_date.items()):
        last_good = [row for row in items if str(row[0]).startswith("market_signals:last-good:")]
        if last_good:
            row = max(last_good, key=lambda value: value[2])
            quality = "exact_last_good"
        else:
            same_day = [
                row for row in items
                if datetime.fromtimestamp(row[2], JST).date().isoformat() == race_date
            ]
            if same_day:
                row = max(same_day, key=lambda value: value[2])
                quality = "same_day_final_cache"
            else:
                row = min(items, key=lambda value: abs(value[2] - datetime.fromisoformat(race_date).replace(tzinfo=JST).timestamp()))
                quality = "recovered_nearest_cache"
        chosen.append((race_date, *row, quality))
    return chosen


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="from_date")
    parser.add_argument("--to", dest="to_date")
    args = parser.parse_args()
    today = date.today()
    from_date = args.from_date or (today - timedelta(days=30)).isoformat()
    to_date = args.to_date or (today - timedelta(days=1)).isoformat()
    if not (os.getenv("DATABASE_URL") or "").startswith(("postgres://", "postgresql://")):
        raise RuntimeError("DATABASE_URL must point to PostgreSQL for ROI history backfill")

    app = create_app()
    adopted_keys = tuple(app.config["ROI_STRATEGY_KEYS"])
    bet_unit_map = dict(app.config["ROI_BET_UNIT_MAP"])
    with db_connect() as conn:
        rows = conn.execute(
            """
            SELECT cache_key, html, updated_at
              FROM page_html_cache
             WHERE cache_key LIKE ?
               AND RIGHT(cache_key, 10) BETWEEN ? AND ?
             ORDER BY updated_at
            """,
            ("market_signals:%", from_date, to_date),
        ).fetchall()
        chosen = _choose_snapshots(rows)
        total = 0
        for race_date, cache_key, html, _updated_at, quality in chosen:
            try:
                payload = json.loads(html)
            except Exception:
                continue
            count = replace_roi_history_snapshot(
                conn,
                payload,
                source_cache_key=str(cache_key),
                capture_quality=quality,
                adopted_keys=adopted_keys,
                bet_unit_map=bet_unit_map,
                parse_bets=_parse_market_signal_bets_for_roi,
                strategy_signature=strategy_definition_signature(REPO),
            )
            total += count
            print(f"{race_date} {quality} rows={count}")
    print(f"dates={len(chosen)} rows={total} range={from_date}..{to_date}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
