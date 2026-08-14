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
import time
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import config
from src.collectors._http import fetch_html
from src.parsers.result_html import parse_result_html

logger = logging.getLogger(__name__)
JST = ZoneInfo("Asia/Tokyo")


def _jst_now_naive() -> datetime:
    """Return current JST without tzinfo for DB timestamps stored as JST-naive."""
    return datetime.now(JST).replace(tzinfo=None)


def _coerce_jst_naive(value) -> datetime | None:
    """Normalize DB/API timestamps to the JST-naive convention used by races."""
    if value in (None, "", "-"):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(JST).replace(tzinfo=None)
    return parsed


# boatrace.jp 結果ページ URL
RESULT_URL = "https://www.boatrace.jp/owpc/pc/race/raceresult?rno={rno}&jcd={jcd:02d}&hd={date}"

# 決まり手バックフィルの 1 レースあたり再試行上限。
# パーサーが決まり手を返せないページ (欠場成立レース等) を 5 分毎の cron が
# 24 時間再取得し続けるのを防ぐ (P0-3 止血)。上限到達後は当日対象から除外。
KIMARITE_MAX_ATTEMPTS = 5
_KIMARITE_ATTEMPT_KEY = "kimarite_retry:{race_id}"


def _ensure_attempt_table(conn) -> None:
    """page_html_cache (汎用 KV) が無いローカル/テスト DB でも動くようにする。"""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS page_html_cache (
            cache_key TEXT PRIMARY KEY,
            html TEXT NOT NULL,
            updated_at REAL NOT NULL
        )
        """
    )


def _kimarite_attempt_counts(conn, race_ids: list[str]) -> dict[str, int]:
    """決まり手バックフィルの試行回数を page_html_cache から読む。

    読めない場合は {} を返す (フィルタ無し = 従来動作)。上限判定は
    best-effort だが、記録側が動いていれば有限回で収束する。
    """
    if not race_ids:
        return {}
    try:
        _ensure_attempt_table(conn)
        placeholders = ",".join("?" for _ in race_ids)
        rows = conn.execute(
            f"SELECT cache_key, html FROM page_html_cache WHERE cache_key IN ({placeholders})",
            tuple(_KIMARITE_ATTEMPT_KEY.format(race_id=rid) for rid in race_ids),
        ).fetchall()
        counts: dict[str, int] = {}
        for cache_key, raw in rows:
            rid = str(cache_key).split(":", 1)[1]
            try:
                counts[rid] = int(str(raw).strip() or 0)
            except (TypeError, ValueError):
                counts[rid] = 0
        return counts
    except Exception as exc:  # noqa: BLE001
        logger.warning("kimarite attempt count read failed: %s", exc)
        return {}


def _record_kimarite_attempt(conn, race_id: str) -> None:
    """決まり手が取れなかった試行を 1 回分カウントアップ (best-effort)。"""
    try:
        _ensure_attempt_table(conn)
        key = _KIMARITE_ATTEMPT_KEY.format(race_id=race_id)
        row = conn.execute(
            "SELECT html FROM page_html_cache WHERE cache_key = ?", (key,)
        ).fetchone()
        try:
            current = int(str(row[0]).strip() or 0) if row else 0
        except (TypeError, ValueError):
            current = 0
        conn.execute(
            """
            INSERT INTO page_html_cache (cache_key, html, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                html = excluded.html,
                updated_at = excluded.updated_at
            """,
            (key, str(current + 1), time.time()),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("kimarite attempt record failed for %s: %s", race_id, exc)


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


def scrape_results_for_pending_races(
    target_date: date,
    conn,
    l4_only: bool = True,
    non_candidate_delay_minutes: int = 60,
    max_races: int = 12,
) -> dict:
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
    # === L4 候補レース ID 集合 (採用 + 観察を全て含む、predictions ベース) ===
    # backlog item 19/20 修正: grade フィルタを撤廃し、A1 + B除外 + prob 0.65-0.85
    # の全レースを対象 = SG/G1/G2/G3 採用、F1 採用、一般戦観察 (gen_tri)、
    # L4-prime/12R 観察 全てカバー。
    l4_candidate_ids: set[str] = set()
    candidate_lookup_failed = False
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
            # fail-closed (P0-3): 候補が特定できないパスでは対象を広げない。
            # 以前はここで l4_only=False (全レース取得) にフォールバックしており、
            # DB 不調時ほど boatrace.jp へのリクエストが激増していた。
            candidate_lookup_failed = True
            logger.warning(
                "L4 candidate lookup failed (%s) → fail-closed: "
                "skipping non-candidate races this pass", e
            )

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
    now = _jst_now_naive()
    for race_id, closed_at in cur.fetchall():
        # L4 フィルタ
        # closed_at は datetime (psycopg) or 文字列 (SQLite)
        close_dt = _coerce_jst_naive(closed_at)
        if close_dt is None:
            continue
        # 締切から 5 分以内はスキップ (まだレース直後で結果ページに反映されてない)
        if now < close_dt + timedelta(minutes=5):
            continue
        # 現時刻より 24h 以上前のものは別途バッチ処理で扱うのでスキップ
        if now > close_dt + timedelta(hours=24):
            continue
        # fail-closed: 候補特定に失敗したパスでは候補以外を一切取得しない
        if candidate_lookup_failed and race_id not in l4_candidate_ids:
            continue
        if (
            l4_only
            and race_id not in l4_candidate_ids
            and now < close_dt + timedelta(minutes=non_candidate_delay_minutes)
        ):
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
    kimarite_rows = cur.fetchall()
    kimarite_attempts = _kimarite_attempt_counts(
        conn, [race_id for race_id, _ in kimarite_rows]
    )
    kimarite_targets: set[str] = set()
    for race_id, closed_at in kimarite_rows:
        if race_id in seen:
            continue
        # 再試行上限 (P0-3): 決まり手が取れないレースを永久に再取得しない
        if kimarite_attempts.get(race_id, 0) >= KIMARITE_MAX_ATTEMPTS:
            continue
        close_dt = _coerce_jst_naive(closed_at)
        if close_dt is None:
            continue
        if now < close_dt + timedelta(minutes=5):
            continue
        if now > close_dt + timedelta(hours=24):
            continue
        if candidate_lookup_failed and race_id not in l4_candidate_ids:
            continue
        if (
            l4_only
            and race_id not in l4_candidate_ids
            and now < close_dt + timedelta(minutes=non_candidate_delay_minutes)
        ):
            continue
        pending.append(race_id)
        seen.add(race_id)
        kimarite_targets.add(race_id)

    if l4_only:
        candidates = [race_id for race_id in pending if race_id in l4_candidate_ids]
        backlog = [race_id for race_id in pending if race_id not in l4_candidate_ids]
    else:
        candidates = []
        backlog = pending

    if max_races > 0 and len(pending) > max_races:
        # Reserve capacity for both signal races and the general backlog. Rotating
        # each group prevents permanently failing old races from starving later ones.
        candidate_limit = min(len(candidates), max(1, max_races // 2))
        backlog_limit = max_races - candidate_limit
        slot = (now.hour * 12) + (now.minute // 5)

        def rotating_slice(items: list[str], limit: int) -> list[str]:
            if limit <= 0 or not items:
                return []
            if len(items) <= limit:
                return items
            start = (slot * limit) % len(items)
            return (items + items)[start:start + limit]

        pending = rotating_slice(candidates, candidate_limit) + rotating_slice(
            backlog,
            backlog_limit,
        )
    elif l4_only:
        pending = candidates + backlog

    results = []
    failed: list[str] = []
    for race_id in pending:
        try:
            payload = scrape_race_result(race_id)
            if payload:
                results.append(payload)
                logger.info("scraped %s (trifecta=%d items)",
                            race_id, len(payload["payouts"].get("trifecta", [])))
            else:
                failed.append(race_id)
            # 決まり手バックフィル対象で決まり手が得られなかった場合のみカウント。
            # 上限 (KIMARITE_MAX_ATTEMPTS) 到達で当日の再取得対象から外れる。
            if race_id in kimarite_targets and (
                not payload or not payload.get("race_kimarite")
            ):
                _record_kimarite_attempt(conn, race_id)
        except Exception as e:
            failed.append(race_id)
            if race_id in kimarite_targets:
                _record_kimarite_attempt(conn, race_id)
            logger.warning("scrape failed for %s: %s", race_id, e)

    return {
        "results": results,
        "target_count": len(pending),
        "failed_count": len(failed),
        "failed_race_ids": failed[:10],
    }
