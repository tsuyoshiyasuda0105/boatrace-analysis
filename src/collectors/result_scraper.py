"""Layer 3: boatrace.jp からレース結果を直接スクレイプ。

Open API (boatraceopenapi.github.io) はバッチ更新で数時間遅延するため、
レース終了直後のリアルタイム結果取得用フォールバック。

使い方:
    from src.collectors.result_scraper import scrape_race_result
    payload = scrape_race_result("20260515-18-02")
    # payload は upsert_results が期待する dict
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional

import config
from src.collectors._http import fetch_html
from src.parsers.result_html import parse_result_html

logger = logging.getLogger(__name__)


# boatrace.jp 結果ページ URL
RESULT_URL = "https://www.boatrace.jp/owpc/pc/race/raceresult?rno={rno}&jcd={jcd:02d}&hd={date}"


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
    }


def scrape_results_for_pending_races(target_date: date, conn,
                                     l4_only: bool = True) -> dict:
    """指定日の「締切後だが race_payouts が無い」レースを抽出し、
    boatrace.jp から結果をスクレイプして upsert_results 互換ペイロードに梱包。

    BAN リスク低減のため、デフォルトでは L4 [A1] 候補レースのみを対象とする
    (~5-10件/日)。条件:
      - 1号艇 A1 (class_number = 1)
      - SG/G1/G2/G3 のみ (grade_number IN 1,2,3,4、一般戦 5 は除外)
      - B 除外会場でない
      - 朝予測 prob_first 0.65-0.85 (≈ 本命500-1000円帯)
    後で Open API のバッチ更新 (~2-3h 遅延) が来ると upsert_results の
    INSERT OR REPLACE で自動的に上書きされる (Open API の数値が「正」)。
    l4_only=False で従来通り全レース対象 (~150件/日).

    Args:
        target_date: 対象日
        conn: DB connection (psycopg or sqlite)
        l4_only: True=L4 [A1] 候補のみ (default), False=全レース
    Returns:
        {"results": [race_dict, ...]} の dict (upsert_results に渡せる形)。
        該当無しなら {"results": []}.
    """
    from datetime import datetime, timedelta

    # === L4 [A1] 候補レース ID 集合 (predictions ベース) ===
    # SG/G1/G2/G3 (採用) + 一般戦 F1 採用ベース (一般×国1≥7×2号40) を対象。
    # 一般戦は数が多いため、F1 条件を SQL に組み込んで絞り込む。
    l4_candidate_ids: set[str] = set()
    if l4_only:
        EXCLUDE_B = (2, 4, 7, 8, 10, 19, 21, 24)
        F1_NATIONAL_TOP1_MIN = 7.0
        F1_BOAT2_TOP2_MIN = 40.0
        try:
            placeholders = ",".join("?" for _ in EXCLUDE_B)
            cur = conn.execute(
                f"""
                SELECT r.race_id
                  FROM races r
                  JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
                  LEFT JOIN race_entries e2 ON e2.race_id = r.race_id AND e2.boat_number = 2
                  JOIN predictions p  ON p.race_id = r.race_id AND p.boat_number = 1
                 WHERE r.race_date = ?
                   AND r.stadium_number NOT IN ({placeholders})
                   AND e.class_number = 1
                   AND p.prob_first BETWEEN ? AND ?
                   AND (
                       r.race_grade_number IN (1, 2, 3, 4)
                       OR (
                           r.race_grade_number = 5
                           AND e.national_top_1_percent >= ?
                           AND e2.national_top_2_percent >= ?
                       )
                   )
                """,
                (target_date.isoformat(), *EXCLUDE_B, 0.65, 0.85,
                 F1_NATIONAL_TOP1_MIN, F1_BOAT2_TOP2_MIN),
            )
            l4_candidate_ids = {row[0] for row in cur.fetchall()}
            logger.info("L4 [A1] candidates for %s: %d races", target_date, len(l4_candidate_ids))
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
