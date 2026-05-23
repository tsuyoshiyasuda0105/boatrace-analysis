"""データ品質チェック (backlog item 3)

朝/時間別バッチの完了後に呼ぶ。以下を確認して system_status テーブルに記録:

  1. races_count           : 今日のレース数 (期待: 60-120 件)
  2. entries_complete      : race_entries で racer_number null が無いか
  3. predictions_count     : predictions が今日の全レース分あるか
  4. previews_count        : race_previews (直前情報) が取れてるか
  5. results_count         : 終了レースの結果が取り込めてるか

異常 (warning/error) があれば status を更新、Web UI バナーで通知。
将来的にはメール通知も追加可能。

使い方:
    python scripts/check_data_quality.py
    python scripts/check_data_quality.py --date 2026-05-17
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.db.connection import connect as db_connect

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")


def upsert_status(conn, check_name: str, check_date: str,
                  status: str, message: str, detail: dict | None = None):
    """system_status に UPSERT (Postgres + SQLite 両対応)"""
    now_iso = datetime.now().isoformat(timespec="seconds")
    detail_json = json.dumps(detail or {}, ensure_ascii=False)
    # 既存判定 → INSERT / UPDATE 切り替え (Postgres/SQLite 共通でシンプル)
    cur = conn.execute(
        "SELECT 1 FROM system_status WHERE check_name=? AND check_date=?",
        (check_name, check_date),
    )
    row = cur.fetchone()
    if row:
        conn.execute(
            "UPDATE system_status SET status=?, message=?, detail_json=?, checked_at=? "
            "WHERE check_name=? AND check_date=?",
            (status, message, detail_json, now_iso, check_name, check_date),
        )
    else:
        conn.execute(
            "INSERT INTO system_status (check_name, check_date, status, message, detail_json, checked_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (check_name, check_date, status, message, detail_json, now_iso),
        )
    conn.commit()


def check_races_count(conn, target_date: str) -> tuple[str, str, dict]:
    """今日のレース数チェック。
    国内 24 会場 × 最大 12R = 288 が理論上限。
    通常は 10-13 会場開催 (120-156 件)。
    重複チェックは件数でなく (stadium, race_number) の重複有無で行う。
    """
    cur = conn.execute("SELECT COUNT(*) FROM races WHERE race_date=?", (target_date,))
    n = cur.fetchone()[0]
    # 重複登録チェック: 同一会場×レース番号が複数存在するか
    cur_dup = conn.execute("""
        SELECT COUNT(*) FROM (
            SELECT stadium_number, race_number
              FROM races WHERE race_date=?
             GROUP BY stadium_number, race_number
            HAVING COUNT(*) > 1
        )
    """, (target_date,))
    n_dup = cur_dup.fetchone()[0]
    detail = {"race_count": n, "duplicate_slots": n_dup}
    if n_dup > 0:
        return "error", f"重複登録あり ({n_dup} スロット)。DB 調査必要", detail
    if n < 30:
        return "error", f"レース数が異常に少ない ({n} 件)。バッチ失敗の可能性", detail
    if n < 60:
        return "warning", f"レース数が少なめ ({n} 件)。会場の途中休催かも", detail
    if n > 288:  # 24 会場 × 12R = 288 が理論上限
        return "error", f"レース数が理論上限超 ({n} 件)。重複以外の異常", detail
    return "ok", f"{n} 件 / 重複なし (正常)", detail


def check_entries_complete(conn, target_date: str) -> tuple[str, str, dict]:
    """race_entries で racer_number 欠損があるか"""
    # 今日のレースに対して 6 艇分の entry が揃っているか
    cur = conn.execute("""
        SELECT r.race_id, r.stadium_number, r.race_number,
               COUNT(e.racer_number) AS n_entries
          FROM races r
          LEFT JOIN race_entries e ON r.race_id = e.race_id
         WHERE r.race_date = ?
         GROUP BY r.race_id, r.stadium_number, r.race_number
         HAVING COUNT(e.racer_number) < 6
         ORDER BY r.stadium_number, r.race_number
    """, (target_date,))
    incomplete = cur.fetchall()
    detail = {"incomplete_races": [
        {"race_id": r[0], "stadium": r[1], "race_no": r[2], "n": r[3]}
        for r in incomplete
    ]}
    n_inc = len(incomplete)
    if n_inc == 0:
        return "ok", "全レース 6 艇分の選手情報あり", detail
    if n_inc <= 5:
        return "warning", f"{n_inc} レースで選手情報未確定。hourly で再取得待ち", detail
    return "error", f"{n_inc} レースで選手情報欠損。バッチ調査必要", detail


def check_predictions_count(conn, target_date: str) -> tuple[str, str, dict]:
    """予測が今日の全レース分あるか (6 艇 × N レース)"""
    # 確定 race_entries が 6 艇分揃ったレースのみを母数とする
    cur = conn.execute("""
        SELECT COUNT(DISTINCT r.race_id)
          FROM races r
          JOIN race_entries e ON r.race_id = e.race_id
         WHERE r.race_date = ?
         GROUP BY r.race_id
        HAVING COUNT(e.racer_number) = 6
    """, (target_date,))
    rows = cur.fetchall()
    n_complete = len(rows)
    cur = conn.execute("""
        SELECT COUNT(DISTINCT p.race_id) FROM predictions p
          JOIN races r ON p.race_id = r.race_id
         WHERE r.race_date = ?
    """, (target_date,))
    n_pred = cur.fetchone()[0]
    detail = {"complete_races": n_complete, "predicted_races": n_pred}
    if n_complete == 0:
        return "warning", "整合レースゼロ (まだ取り込み中?)", detail
    coverage = n_pred / n_complete * 100 if n_complete else 0
    if coverage < 30:
        return "error", f"予測未生成 ({n_pred}/{n_complete}、{coverage:.0f}%)", detail
    if coverage < 80:
        return "warning", f"予測カバレッジ低 ({n_pred}/{n_complete}、{coverage:.0f}%)", detail
    return "ok", f"予測 {n_pred}/{n_complete} ({coverage:.0f}%)", detail


def check_previews_count(conn, target_date: str) -> tuple[str, str, dict]:
    """直前情報 (race_previews) が取れているか"""
    cur = conn.execute("""
        SELECT COUNT(DISTINCT pv.race_id) FROM race_previews pv
          JOIN races r ON pv.race_id = r.race_id
         WHERE r.race_date = ?
    """, (target_date,))
    n_pv = cur.fetchone()[0]
    cur = conn.execute("SELECT COUNT(*) FROM races WHERE race_date=?", (target_date,))
    n_races = cur.fetchone()[0]
    detail = {"preview_races": n_pv, "total_races": n_races}
    if n_races == 0:
        return "warning", "本日レースなし", detail
    cov = n_pv / n_races * 100
    # 朝早い時間帯は preview が無いのが正常 (午前 8 時以前等)
    # シビアな判定は午後にする
    now_hour = datetime.now().hour
    if now_hour < 11:
        if cov < 30:
            return "ok", f"直前情報 {n_pv}/{n_races} (朝のため未取得は正常)", detail
        return "ok", f"直前情報 {n_pv}/{n_races} ({cov:.0f}%)", detail
    # 午後以降は半分以上揃ってるべき
    if cov < 30:
        return "warning", f"直前情報少なめ ({n_pv}/{n_races}、{cov:.0f}%)", detail
    return "ok", f"直前情報 {n_pv}/{n_races} ({cov:.0f}%)", detail


def check_results_count(conn, target_date: str) -> tuple[str, str, dict]:
    """終了レースの結果が取れているか"""
    # race_closed_at は TEXT 'YYYY-MM-DD HH:MM:SS' (スペース区切り) で格納される。
    # datetime.now().isoformat() は 'YYYY-MM-DDThh:mm:ss' (T 区切り) を返すため、
    # 文字列比較で空白(0x20) < 'T'(0x54) となり「同日の全レースが締切済」と
    # 誤判定する (2026-05-22 障害)。同じスペース区切り形式で比較する。
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.execute("""
        SELECT COUNT(DISTINCT r.race_id) FROM races r
         WHERE r.race_date = ?
           AND r.race_closed_at IS NOT NULL
           AND r.race_closed_at < ?
    """, (target_date, now_str))
    n_closed = cur.fetchone()[0]
    cur = conn.execute("""
        SELECT COUNT(DISTINCT res.race_id) FROM race_results res
          JOIN races r ON res.race_id = r.race_id
         WHERE r.race_date = ?
           AND res.finishing_position IS NOT NULL
    """, (target_date,))
    n_results = cur.fetchone()[0]
    detail = {"closed_races": n_closed, "result_races": n_results}
    if n_closed == 0:
        return "ok", "本日まだ確定レースなし", detail
    cov = n_results / n_closed * 100
    if cov < 50:
        # 締切済レース数が少ない (<=5) うちは結果バッチが未回収なだけ → ok
        if n_closed <= 5:
            return "ok", f"結果取り込み待ち ({n_results}/{n_closed}、バッチ未回収の可能性)", detail
        return "warning", f"結果取り込み遅延 ({n_results}/{n_closed}、{cov:.0f}%)", detail
    if cov < 90:
        return "ok", f"結果 {n_results}/{n_closed} ({cov:.0f}%)", detail
    return "ok", f"結果 {n_results}/{n_closed} 取り込み済", detail


CHECKS = [
    ("races_count", check_races_count),
    ("entries_complete", check_entries_complete),
    ("predictions_count", check_predictions_count),
    ("previews_count", check_previews_count),
    ("results_count", check_results_count),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, default=date.today().isoformat())
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    target_date = args.date
    print(f"=== データ品質チェック ({target_date}) ===")

    conn = db_connect()
    n_warn = n_err = 0
    for check_name, check_fn in CHECKS:
        try:
            status, message, detail = check_fn(conn, target_date)
        except Exception as e:
            logger.exception("check failed: %s", check_name)
            status, message, detail = "error", f"チェック失敗: {e}", {"error": str(e)}
        upsert_status(conn, check_name, target_date, status, message, detail)
        icon = {"ok": "OK", "warning": "WARN", "error": "ERR"}[status]
        print(f"  [{icon:4}] {check_name:24} {message}")
        if status == "warning":
            n_warn += 1
        if status == "error":
            n_err += 1
    conn.close()

    print()
    if n_err:
        print(f"完了: ERROR {n_err} / WARNING {n_warn}")
        sys.exit(2)
    elif n_warn:
        print(f"完了: WARNING {n_warn}")
        sys.exit(1)
    else:
        print("完了: すべて OK")
        sys.exit(0)


if __name__ == "__main__":
    main()
