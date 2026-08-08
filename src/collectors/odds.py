"""
Layer 3: 三連単オッズスクレイパー

- races テーブルにある race_id のうち、まだ odds_trifecta が無いものを対象
- 1日1回の取得を想定 (途中オッズは取らない)
- is_final: race_results に該当 race_id が存在すれば 1、なければ 0
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime, timezone
from typing import Optional

import config
from src.collectors._http import FetchHtmlResult, fetch_html_detailed
from src.db.connection import connect as db_connect
from src.parsers.odds import parse_trifecta_odds

logger = logging.getLogger(__name__)
EXPECTED_TRIFECTA_COMBINATIONS = 120


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _save_raw_html(target_date: date, stadium: int, race_no: int, html: str) -> None:
    out_dir = config.RAW_DIR / "odds3t" / target_date.isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{stadium:02d}_{race_no:02d}.html"
    out.write_text(html, encoding="utf-8")


def _list_target_races(
    conn: sqlite3.Connection,
    target_date: date,
    force: bool,
) -> list[tuple[str, int, int]]:
    if force:
        sql = """
            SELECT race_id, stadium_number, race_number
              FROM races
             WHERE race_date = ?
             ORDER BY stadium_number, race_number
        """
    else:
        sql = """
            SELECT r.race_id, r.stadium_number, r.race_number
              FROM races r
             WHERE r.race_date = ?
               AND r.race_id IN (
                    SELECT r2.race_id
                      FROM races r2
                      LEFT JOIN odds_trifecta o
                        ON o.race_id = r2.race_id
                     WHERE r2.race_date = ?
                     GROUP BY r2.race_id
                    HAVING COUNT(DISTINCT o.combination) < ?
               )
             ORDER BY r.stadium_number, r.race_number
        """
        return list(
            conn.execute(
                sql,
                (target_date.isoformat(), target_date.isoformat(), EXPECTED_TRIFECTA_COMBINATIONS),
            ).fetchall()
        )
    return list(conn.execute(sql, (target_date.isoformat(),)).fetchall())


def _is_finalized(conn: sqlite3.Connection, race_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM race_results WHERE race_id = ? LIMIT 1",
        (race_id,),
    ).fetchone() is not None


def _upsert_odds(
    conn: sqlite3.Connection,
    race_id: str,
    odds_map: dict[str, float],
    recorded_at: str,
    is_final: int,
    snapshot_label: Optional[str] = None,
) -> int:
    if not odds_map:
        return 0
    if snapshot_label is None:
        snapshot_label = "final" if is_final else "intermediate"
    rows = [
        (race_id, comb, float(odds), is_final, recorded_at, snapshot_label)
        for comb, odds in odds_map.items()
    ]
    conn.executemany(
        """
        INSERT OR REPLACE INTO odds_trifecta
            (race_id, combination, odds, is_final, recorded_at, snapshot_label)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def _ensure_fetch_status_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS odds_fetch_status (
          race_id             TEXT NOT NULL,
          snapshot_label      TEXT NOT NULL,
          state               TEXT NOT NULL,
          detail_code         TEXT NOT NULL,
          http_status         INTEGER,
          combination_count   INTEGER NOT NULL DEFAULT 0,
          retryable           INTEGER NOT NULL DEFAULT 0,
          attempts            INTEGER NOT NULL DEFAULT 0,
          checked_at          TEXT NOT NULL,
          last_success_at     TEXT,
          note                TEXT,
          PRIMARY KEY (race_id, snapshot_label)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_odds_fetch_status_state
          ON odds_fetch_status(state, checked_at)
        """
    )


def _record_fetch_status(
    conn: sqlite3.Connection,
    *,
    race_id: str,
    snapshot_label: str,
    state: str,
    detail_code: str,
    checked_at: str,
    http_status: Optional[int] = None,
    combination_count: int = 0,
    retryable: bool = False,
    attempts: int = 0,
    note: Optional[str] = None,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO odds_fetch_status
            (race_id, snapshot_label, state, detail_code, http_status,
             combination_count, retryable, attempts, checked_at, last_success_at, note)
        VALUES (
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?, 
            CASE WHEN ? = 'fetched' THEN ? ELSE (
                SELECT last_success_at FROM odds_fetch_status
                 WHERE race_id = ? AND snapshot_label = ?
            ) END,
            ?
        )
        """,
        (
            race_id,
            snapshot_label,
            state,
            detail_code,
            http_status,
            combination_count,
            1 if retryable else 0,
            attempts,
            checked_at,
            state,
            checked_at,
            race_id,
            snapshot_label,
            note,
        ),
    )


def _state_from_fetch_result(result: FetchHtmlResult) -> tuple[str, str]:
    detail_code = result.error_type or "request_error"
    if result.retryable:
        return "retry_waiting", detail_code
    return "missing", detail_code


def collect_one_race(
    race_id: str,
    snapshot_label: str,
    db_path: Optional[str] = None,
) -> dict:
    """単一レースのオッズを 1 回スナップショット取得"""
    config.ensure_dirs()
    conn = db_connect(db_path)
    _ensure_fetch_status_table(conn)
    recorded_at = _utc_now_iso()
    summary = {"race_id": race_id, "snapshot_label": snapshot_label, "odds_inserted": 0}
    try:
        # race_id から jcd, date, rno を抽出
        date_str, jcd_str, rno_str = race_id.split("-")
        jcd = int(jcd_str)
        rno = int(rno_str)
        url = config.ODDS_TRIFECTA_URL.format(jcd=jcd, date=date_str, rno=rno)
        fetch_result = fetch_html_detailed(url)
        if not fetch_result.ok or not fetch_result.html:
            state, detail_code = _state_from_fetch_result(fetch_result)
            _record_fetch_status(
                conn,
                race_id=race_id,
                snapshot_label=snapshot_label,
                state=state,
                detail_code=detail_code,
                checked_at=recorded_at,
                http_status=fetch_result.status_code,
                retryable=fetch_result.retryable,
                attempts=fetch_result.attempts,
            )
            conn.commit()
            summary["error"] = detail_code
            summary["fetch_state"] = state
            return summary
        odds_map = parse_trifecta_odds(fetch_result.html)
        if not odds_map:
            _record_fetch_status(
                conn,
                race_id=race_id,
                snapshot_label=snapshot_label,
                state="missing",
                detail_code="parse_empty",
                checked_at=recorded_at,
                attempts=fetch_result.attempts,
            )
            conn.commit()
            summary["error"] = "parse_empty"
            summary["fetch_state"] = "missing"
            return summary
        is_final = 1 if _is_finalized(conn, race_id) else 0
        n = _upsert_odds(conn, race_id, odds_map, recorded_at, is_final, snapshot_label)
        summary["odds_inserted"] = n
        combination_count = len(odds_map)
        if combination_count < EXPECTED_TRIFECTA_COMBINATIONS:
            _record_fetch_status(
                conn,
                race_id=race_id,
                snapshot_label=snapshot_label,
                state="missing",
                detail_code="partial_data",
                checked_at=recorded_at,
                combination_count=combination_count,
                attempts=fetch_result.attempts,
                note=f"expected={EXPECTED_TRIFECTA_COMBINATIONS}",
            )
            summary["error"] = "partial_data"
            summary["fetch_state"] = "missing"
            summary["missing_combinations"] = EXPECTED_TRIFECTA_COMBINATIONS - combination_count
        else:
            _record_fetch_status(
                conn,
                race_id=race_id,
                snapshot_label=snapshot_label,
                state="fetched",
                detail_code="fetched",
                checked_at=recorded_at,
                combination_count=combination_count,
                attempts=fetch_result.attempts,
            )
            summary["fetch_state"] = "fetched"
        conn.commit()
    finally:
        conn.close()
    return summary


def collect_for_date(
    target_date: date,
    db_path: Optional[str] = None,
    force: bool = False,
    save_html: bool = True,
    snapshot_label: Optional[str] = None,
) -> dict:
    config.ensure_dirs()
    conn = db_connect(db_path)
    _ensure_fetch_status_table(conn)
    recorded_at = _utc_now_iso()

    summary = {
        "date": target_date.isoformat(),
        "races_targeted": 0,
        "races_fetched": 0,
        "odds_inserted": 0,
    }

    try:
        targets = _list_target_races(conn, target_date, force)
        summary["races_targeted"] = len(targets)
        if not targets:
            logger.info("no target races for %s", target_date)
            return summary

        date_str = target_date.strftime("%Y%m%d")

        for race_id, stadium, race_no in targets:
            url = config.ODDS_TRIFECTA_URL.format(jcd=stadium, date=date_str, rno=race_no)
            fetch_result = fetch_html_detailed(url)
            label = snapshot_label or ("final" if _is_finalized(conn, race_id) else "intermediate")
            if not fetch_result.ok or not fetch_result.html:
                state, detail_code = _state_from_fetch_result(fetch_result)
                _record_fetch_status(
                    conn,
                    race_id=race_id,
                    snapshot_label=label,
                    state=state,
                    detail_code=detail_code,
                    checked_at=recorded_at,
                    http_status=fetch_result.status_code,
                    retryable=fetch_result.retryable,
                    attempts=fetch_result.attempts,
                )
                conn.commit()
                logger.info("skip (%s): %s", detail_code, race_id)
                continue

            if save_html:
                try:
                    _save_raw_html(target_date, stadium, race_no, fetch_result.html)
                except OSError as e:
                    logger.warning("html save failed %s: %s", race_id, e)

            odds_map = parse_trifecta_odds(fetch_result.html)
            if not odds_map:
                _record_fetch_status(
                    conn,
                    race_id=race_id,
                    snapshot_label=label,
                    state="missing",
                    detail_code="parse_empty",
                    checked_at=recorded_at,
                    attempts=fetch_result.attempts,
                )
                conn.commit()
                logger.info("no odds parsed: %s", race_id)
                continue

            is_final = 1 if _is_finalized(conn, race_id) else 0
            n = _upsert_odds(conn, race_id, odds_map, recorded_at, is_final, label)
            summary["races_fetched"] += 1
            summary["odds_inserted"] += n
            combination_count = len(odds_map)
            if combination_count < EXPECTED_TRIFECTA_COMBINATIONS:
                _record_fetch_status(
                    conn,
                    race_id=race_id,
                    snapshot_label=label,
                    state="missing",
                    detail_code="partial_data",
                    checked_at=recorded_at,
                    combination_count=combination_count,
                    attempts=fetch_result.attempts,
                    note=f"expected={EXPECTED_TRIFECTA_COMBINATIONS}",
                )
            else:
                _record_fetch_status(
                    conn,
                    race_id=race_id,
                    snapshot_label=label,
                    state="fetched",
                    detail_code="fetched",
                    checked_at=recorded_at,
                    combination_count=combination_count,
                    attempts=fetch_result.attempts,
                )

            conn.commit()

        logger.info("odds %s: %s", target_date, summary)
        return summary
    finally:
        conn.close()
