"""Layer 3: boatrace.jp からレース結果を直接スクレイプ。

Open API (boatraceopenapi.github.io) はバッチ更新で数時間遅延するため、
レース終了直後のリアルタイム結果取得用フォールバック。

使い方:
    from src.collectors.result_scraper import scrape_race_result
    payload = scrape_race_result("20260515-18-02")
    # payload は upsert_results が期待する dict
"""
from __future__ import annotations

import json
import logging
from datetime import date
from typing import Optional

import config
from src.collectors._http import fetch_html
from src.parsers.result_html import parse_result_html

logger = logging.getLogger(__name__)


# boatrace.jp 結果ページ URL
RESULT_URL = "https://www.boatrace.jp/owpc/pc/race/raceresult?rno={rno}&jcd={jcd:02d}&hd={date}"


def _market_signal_candidate_ids(conn, target_date: date) -> set[str]:
    """Return race ids from the precomputed ROI/high-signal cache.

    The old Layer3 filter only covered legacy L4 probability candidates. Newer
    adopted/watch strategies are precomputed into market_signals, so result
    polling must include those race ids or ended ROI rows stay pending until
    the delayed Open API batch arrives.
    """
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
    except Exception as exc:
        logger.warning("market signal result target lookup failed: %s", exc)
        return set()


def scrape_race_result(race_id: str) -> Optional[dict]:
    """単一レースの結果を boatrace.jp からスクレイプ。
    Returns:
        Open API 互換の race-1件分 dict (race_date / race_stadium_number / race_number /
        boats / payouts / race_kimarite を含む)、または None (未確定)
    """
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

    # Open API 互換の race ペイロードに変換
    return {
        "race_date": race_date_iso,
        "race_stadium_number": jcd,
        "race_number": rno,
        "race_kimarite": parsed.get("race_kimarite"),
        "boats": parsed["boats"],
        "payouts": parsed["payouts"],
        # 確定後の水面気象情報 (weather_number/wind/wave/temp/water_temp/wind_dir)。
        # post-race の上書き用 (race_previews の朝予報を実観測値で置き換える)。
        "weather": parsed.get("weather") or {},
    }


def overwrite_race_previews_weather(race_id: str, weather: dict, conn) -> int:
    """結果ページから取得した確定 weather で race_previews を上書き。

    race_previews は (race_id, boat_number) ごとに行があり、weather 系列は
    レース内全艇で同値。全艇行に同じ値を一括 UPDATE する。
    朝予報や直前情報が誤っていた場合の最終確定値として機能する。

    Returns:
        UPDATE された行数 (race_previews の boats 数)
    """
    if not weather:
        return 0
    # 全部 None なら何もしない (パース失敗のときに既存値を壊さない)
    if not any(v is not None for v in weather.values()):
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
        n = cur.rowcount
    except Exception:
        n = 0
    conn.commit()
    return n


def scrape_results_for_pending_races(target_date: date, conn,
                                     l4_only: bool = True) -> dict:
    """指定日の「締切後だが race_payouts が無い」レースを抽出し、
    boatrace.jp から結果をスクレイプして upsert_results 互換ペイロードに梱包。

    BAN リスク低減のため、デフォルトでは L4 [A1] 候補レースのみを対象とする。
    backlog item 19/20 (2026-05-18): Layer 3 を「採用候補のみ」から
    「採用 + 観察候補すべて」に拡張。当日の ROI ダッシュボード反映を高速化。
      - 1号艇 A1 (class_number = 1)
      - B 除外会場でない
      - 朝予測 prob_first 0.65-0.85 (≈ 本命500-1000円帯)
      - grade 制限なし (SG/G1/G2/G3 + 一般戦すべて)
        → 採用 (SG/G1/G2/G3 + F1) と観察 (一般戦 base, L4-prime/12R) が全て
          含まれる。1日 ~15-25 件程度に抑えられるため BAN リスクは現状の倍程度。

    後で Open API のバッチ更新 (~2-3h 遅延) が来ると upsert_results の
    INSERT OR REPLACE で自動的に上書きされる (Open API の数値が「正」)。
    l4_only=False で従来通り全レース対象 (~150件/日).

    Args:
        target_date: 対象日
        conn: DB connection (psycopg or sqlite)
        l4_only: True=L4 候補 (採用+観察) のみ (default), False=全レース
    Returns:
        {"results": [race_dict, ...]} の dict (upsert_results に渡せる形)。
        該当無しなら {"results": []}.
    """
    from datetime import datetime, timedelta

    # === L4 候補レース ID 集合 (採用 + 観察を全て含む、predictions ベース) ===
    # backlog item 19/20 修正: grade フィルタを撤廃し、A1 + B除外 + prob 0.65-0.85
    # の全レースを対象 = SG/G1/G2/G3 採用、F1 採用、一般戦観察 (gen_tri)、
    # L4-prime/12R 観察 全てカバー。
    l4_candidate_ids: set[str] = set()
    if l4_only:
        EXCLUDE_B = (2, 4, 7, 8, 10, 19, 21, 24)
        try:
            placeholders = ",".join("?" for _ in EXCLUDE_B)
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
                (target_date.isoformat(), *EXCLUDE_B, 0.65, 0.85),
            )
            l4_candidate_ids = {row[0] for row in cur.fetchall()}
            l4_candidate_ids.update(_market_signal_candidate_ids(conn, target_date))
            logger.info("L4 候補 (採用+観察) for %s: %d races", target_date, len(l4_candidate_ids))
        except Exception as e:
            logger.warning("L4 candidate lookup failed (%s) → falling back to all races", e)
            l4_only = False  # safety net

    # === 締切から 5 分以上経過し、まだ race_payouts (trifecta) が無いレース ===
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
    pending: list[str] = []
    now = datetime.now()
    for race_id, closed_at in cur.fetchall():
        # L4 フィルタ
        if l4_only and race_id not in l4_candidate_ids:
            continue
        # closed_at は datetime (psycopg) or 文字列 (SQLite)
        if isinstance(closed_at, datetime):
            close_dt = closed_at
        else:
            try:
                close_dt = datetime.fromisoformat(str(closed_at))
            except (ValueError, TypeError):
                continue
        # 締切から 5 分以内はスキップ (まだレース直後で結果ページに反映されてない)
        if now < close_dt + timedelta(minutes=5):
            continue
        # 現時刻より 24h 以上前のものは別途バッチ処理で扱うのでスキップ
        if now > close_dt + timedelta(hours=24):
            continue
        pending.append(race_id)

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
        if race_id in seen:
            continue
        if l4_only and race_id not in l4_candidate_ids:
            continue
        if isinstance(closed_at, datetime):
            close_dt = closed_at
        else:
            try:
                close_dt = datetime.fromisoformat(str(closed_at))
            except (ValueError, TypeError):
                continue
        if now < close_dt + timedelta(minutes=5):
            continue
        if now > close_dt + timedelta(hours=24):
            continue
        pending.append(race_id)
        seen.add(race_id)

    results = []
    for race_id in pending:
        try:
            payload = scrape_race_result(race_id)
            if payload:
                results.append(payload)
                logger.info("scraped %s (trifecta=%d items)",
                            race_id, len(payload["payouts"].get("trifecta", [])))
        except Exception as e:
            logger.warning("scrape failed for %s: %s", race_id, e)

    return {"results": results}
