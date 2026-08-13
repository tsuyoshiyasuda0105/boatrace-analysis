"""Layer 3 result scraping from boatrace.jp.

Open API result updates can lag behind race close, so this module scrapes only
the races that are still missing reliable result rows.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from typing import Iterable, Optional

from src.collectors._http import fetch_html
from src.parsers.result_html import parse_result_html

logger = logging.getLogger(__name__)


RESULT_URL = "https://www.boatrace.jp/owpc/pc/race/raceresult?rno={rno}&jcd={jcd:02d}&hd={date}"


def _market_signal_candidate_ids(conn, target_date: date) -> set[str]:
    """Return race ids from the precomputed ROI/high-signal cache."""
    try:
        row = conn.execute(
            """
            SELECT html
              FROM page_html_cache
             WHERE cache_key = ?
             LIMIT 1
            """,
            (f"market_signals:last-good:{target_date.isoformat()}",),
        ).fetchone()
        if not row or not row[0]:
            return set()
        payload = json.loads(row[0])
        signals = payload.get("signals") if isinstance(payload, dict) else None
        if not isinstance(signals, dict):
            return set()
        return {str(race_id) for race_id in signals.keys() if race_id}
    except Exception as exc:  # noqa: BLE001
        logger.warning("market signal result target lookup failed: %s", exc)
        return set()


def scrape_race_result(race_id: str) -> Optional[dict]:
    """Scrape a single race result page and convert it to Open API-like shape."""
    date_str, jcd_str, rno_str = race_id.split("-")
    jcd = int(jcd_str)
    rno = int(rno_str)
    race_date_iso = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"

    url = RESULT_URL.format(rno=rno, jcd=jcd, date=date_str)
    html = fetch_html(url)
    if not html:
        logger.info("no html for %s", race_id)
        return None

    parsed = parse_result_html(html)
    if parsed is None:
        return None

    return {
        "race_date": race_date_iso,
        "race_stadium_number": jcd,
        "race_number": rno,
        "race_kimarite": parsed.get("race_kimarite"),
        "boats": parsed["boats"],
        "payouts": parsed["payouts"],
        "weather": parsed.get("weather") or {},
    }


def overwrite_race_previews_weather(race_id: str, weather: dict, conn) -> int:
    """Backfill post-race weather into race_previews when present."""
    if not weather:
        return 0
    if not any(value is not None for value in weather.values()):
        return 0
    cur = conn.execute(
        """UPDATE race_previews
              SET weather_number        = COALESCE(?, weather_number),
                  wind_speed            = COALESCE(?, wind_speed),
                  wind_direction_number = COALESCE(?, wind_direction_number),
                  wave_height           = COALESCE(?, wave_height),
                  temperature           = COALESCE(?, temperature),
                  water_temperature     = COALESCE(?, water_temperature),
                  live_updated_at       = COALESCE(live_updated_at, ?)
            WHERE race_id=?""",
        (
            weather.get("weather_number"),
            weather.get("wind_speed"),
            weather.get("wind_direction_number"),
            weather.get("wave_height"),
            weather.get("temperature"),
            weather.get("water_temperature"),
            "post-race",
            race_id,
        ),
    )
    try:
        updated = cur.rowcount
    except Exception:  # noqa: BLE001
        updated = 0
    conn.commit()
    return updated


def scrape_results_for_pending_races(
    target_date: date,
    conn,
    l4_only: bool = True,
    race_ids: Optional[Iterable[str]] = None,
) -> dict:
    """Scrape boatrace.jp result pages for pending races.

    By default this keeps the older L4/high-signal filter so the first cron pass
    stays light. Callers can provide `race_ids` or set `l4_only=False` when they
    need targeted repair for already-closed races that are still incomplete.
    """
    from datetime import datetime, timedelta

    now = datetime.now()
    target_race_ids = {str(race_id) for race_id in (race_ids or []) if race_id}
    l4_candidate_ids: set[str] = set()

    if l4_only:
        exclude_b = (2, 4, 7, 8, 10, 19, 21, 24)
        try:
            placeholders = ",".join("?" for _ in exclude_b)
            cur = conn.execute(
                f"""
                SELECT r.race_id
                  FROM races r
                  JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
                  JOIN predictions p  ON p.race_id = r.race_id AND p.boat_number = 1
                 WHERE r.race_date = ?
                   AND r.stadium_number NOT IN ({placeholders})
                   AND e.class_number = 1
                   AND p.prob_first BETWEEN ? AND ?
                """,
                (target_date.isoformat(), *exclude_b, 0.65, 0.85),
            )
            l4_candidate_ids = {str(row[0]) for row in cur.fetchall()}
            l4_candidate_ids.update(_market_signal_candidate_ids(conn, target_date))
            logger.info("L4 result scrape targets for %s: %d races", target_date, len(l4_candidate_ids))
        except Exception as exc:  # noqa: BLE001
            logger.warning("L4 candidate lookup failed (%s); falling back to all races", exc)
            l4_only = False

    def _is_due(race_id: str, closed_at) -> bool:
        if target_race_ids and race_id not in target_race_ids:
            return False
        if l4_only and race_id not in l4_candidate_ids:
            return False
        if isinstance(closed_at, datetime):
            close_dt = closed_at
        else:
            try:
                close_dt = datetime.fromisoformat(str(closed_at))
            except (ValueError, TypeError):
                return False
        if now < close_dt + timedelta(minutes=5):
            return False
        if now > close_dt + timedelta(hours=24):
            return False
        return True

    cur = conn.execute(
        """
        SELECT r.race_id, r.race_closed_at
          FROM races r
         WHERE r.race_date = ?
           AND r.race_closed_at IS NOT NULL
           AND r.race_id NOT IN (
               SELECT DISTINCT race_id FROM race_payouts WHERE bet_type = 'trifecta'
           )
         ORDER BY r.race_closed_at
        """,
        (target_date.isoformat(),),
    )
    pending = [
        str(race_id)
        for race_id, closed_at in cur.fetchall()
        if _is_due(str(race_id), closed_at)
    ]

    cur = conn.execute(
        """
        SELECT r.race_id, r.race_closed_at
          FROM races r
         WHERE r.race_date = ?
           AND r.race_closed_at IS NOT NULL
           AND r.race_id IN (
               SELECT DISTINCT race_id FROM race_payouts WHERE bet_type = 'trifecta'
           )
           AND r.race_id IN (
               SELECT race_id
                 FROM race_results
                GROUP BY race_id
               HAVING SUM(CASE WHEN kimarite IS NOT NULL AND TRIM(kimarite) <> ''
                               THEN 1 ELSE 0 END) = 0
           )
         ORDER BY r.race_closed_at
        """,
        (target_date.isoformat(),),
    )
    seen = set(pending)
    for race_id, closed_at in cur.fetchall():
        race_id = str(race_id)
        if race_id in seen or not _is_due(race_id, closed_at):
            continue
        pending.append(race_id)
        seen.add(race_id)

    results = []
    for race_id in pending:
        try:
            payload = scrape_race_result(race_id)
            if payload:
                results.append(payload)
                logger.info(
                    "scraped %s (trifecta=%d items)",
                    race_id,
                    len(payload["payouts"].get("trifecta", [])),
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("scrape failed for %s: %s", race_id, exc)

    return {"results": results}
