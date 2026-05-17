"""DB 容量監視 + 古いオッズデータ削除 (backlog item 12)

Supabase Free プラン 500MB 制限を意識し:
  1. 各テーブルのサイズを取得 → system_status に記録 → Web で確認可
  2. 古い odds_trifecta (確定済レース × 60 日以前) を削除して圧縮
  3. 容量逼迫 (>400MB 使用) で warning、>480MB で error

使い方:
    python scripts/db_size_check.py                # サイズ確認のみ
    python scripts/db_size_check.py --cleanup      # + 古いオッズ削除
    python scripts/db_size_check.py --cleanup --keep-days 30  # 30日以前削除
    python scripts/db_size_check.py --dry-run --cleanup        # 削除予測のみ
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.db.connection import connect as db_connect

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")


def get_table_sizes_postgres(conn) -> list[dict]:
    """Postgres: pg_total_relation_size で各テーブルサイズ取得"""
    cur = conn.execute("""
        SELECT
            tablename AS name,
            pg_total_relation_size('public.' || tablename) AS bytes,
            pg_size_pretty(pg_total_relation_size('public.' || tablename)) AS pretty
        FROM pg_tables
        WHERE schemaname = 'public'
        ORDER BY pg_total_relation_size('public.' || tablename) DESC
    """)
    return [{"name": r[0], "bytes": r[1], "pretty": r[2]} for r in cur.fetchall()]


def get_table_sizes_sqlite(conn) -> list[dict]:
    """SQLite: dbstat があれば使う、無ければファイルサイズで代替"""
    try:
        cur = conn.execute("""
            SELECT name, SUM(pgsize) AS bytes FROM dbstat
             WHERE name NOT LIKE 'sqlite_%'
             GROUP BY name ORDER BY bytes DESC
        """)
        return [{"name": r[0], "bytes": r[1], "pretty": f"{(r[1] or 0)/1024/1024:.1f}MB"}
                for r in cur.fetchall()]
    except Exception:
        return []


def total_size_postgres(conn) -> int:
    """Postgres: DB 全体のサイズ (bytes)"""
    cur = conn.execute("SELECT pg_database_size(current_database())")
    return cur.fetchone()[0] or 0


def cleanup_old_odds(conn, is_pg: bool, keep_days: int = 60,
                     dry_run: bool = False) -> dict:
    """確定済レース × keep_days 日以前の odds_trifecta を削除。
    現在進行中のレース (race_date >= 今日) は削除対象外。

    返り値: {'deleted_rows': N, 'cutoff_date': 'YYYY-MM-DD'}
    """
    cutoff = (date.today() - timedelta(days=keep_days)).isoformat()
    # 削除候補数を先にカウント
    count_sql = """
        SELECT COUNT(*) FROM odds_trifecta o
         WHERE o.race_id IN (
            SELECT race_id FROM races WHERE race_date < ?
         )
    """
    cur = conn.execute(count_sql, (cutoff,))
    n_target = cur.fetchone()[0]

    if dry_run:
        logger.info("[dry-run] cutoff=%s, 削除予定 %s 行", cutoff, f"{n_target:,}")
        return {"deleted_rows": 0, "would_delete": n_target, "cutoff_date": cutoff}

    if n_target == 0:
        logger.info("削除対象なし (cutoff=%s)", cutoff)
        return {"deleted_rows": 0, "cutoff_date": cutoff}

    logger.info("delete: cutoff=%s, target=%s rows", cutoff, f"{n_target:,}")
    del_sql = """
        DELETE FROM odds_trifecta
         WHERE race_id IN (
            SELECT race_id FROM races WHERE race_date < ?
         )
    """
    conn.execute(del_sql, (cutoff,))
    conn.commit()
    logger.info("削除完了: %s 行", f"{n_target:,}")

    # Postgres は VACUUM で実領域回収
    if is_pg:
        try:
            # psycopg は VACUUM をトランザクション内で許さないので autocommit に
            conn.commit()  # 念のためトランザクション閉じる
            # autocommit モードで VACUUM
            old_autocommit = conn.autocommit
            conn.autocommit = True
            conn.execute("VACUUM ANALYZE odds_trifecta")
            conn.autocommit = old_autocommit
            logger.info("VACUUM ANALYZE odds_trifecta 完了")
        except Exception as e:
            logger.warning("VACUUM 失敗 (削除自体は成功): %s", e)
    return {"deleted_rows": n_target, "cutoff_date": cutoff}


def update_system_status(conn, total_bytes: int, sizes: list[dict],
                          cleanup_result: dict | None = None):
    """システム状態テーブルに DB 使用量を記録"""
    today_iso = date.today().isoformat()
    total_mb = total_bytes / 1024 / 1024
    # Supabase Free 500MB
    LIMIT_MB = 500
    pct = total_mb / LIMIT_MB * 100
    if total_mb >= 480:
        status, msg = "error", f"DB 容量逼迫: {total_mb:.0f} MB / 500 MB ({pct:.0f}%)"
    elif total_mb >= 400:
        status, msg = "warning", f"DB 容量 80% 超: {total_mb:.0f} MB / 500 MB ({pct:.0f}%)"
    else:
        status, msg = "ok", f"DB 容量 {total_mb:.0f} MB / 500 MB ({pct:.0f}%)"
    detail = {
        "total_bytes": total_bytes, "total_mb": round(total_mb, 1),
        "limit_mb": LIMIT_MB, "pct": round(pct, 1),
        "top_tables": [{"name": s["name"], "pretty": s["pretty"]} for s in sizes[:5]],
    }
    if cleanup_result:
        detail["cleanup"] = cleanup_result
    detail_json = json.dumps(detail, ensure_ascii=False)
    now_iso = datetime.now().isoformat(timespec="seconds")
    # UPSERT (Postgres + SQLite 両対応の素直な書き方)
    cur = conn.execute(
        "SELECT 1 FROM system_status WHERE check_name=? AND check_date=?",
        ("db_capacity", today_iso),
    )
    if cur.fetchone():
        conn.execute(
            "UPDATE system_status SET status=?, message=?, detail_json=?, checked_at=? "
            "WHERE check_name=? AND check_date=?",
            (status, msg, detail_json, now_iso, "db_capacity", today_iso),
        )
    else:
        conn.execute(
            "INSERT INTO system_status (check_name, check_date, status, message, detail_json, checked_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("db_capacity", today_iso, status, msg, detail_json, now_iso),
        )
    conn.commit()
    return status, msg


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cleanup", action="store_true",
                        help="古い odds_trifecta を削除")
    parser.add_argument("--keep-days", type=int, default=60,
                        help="保持日数 (default=60)")
    parser.add_argument("--dry-run", action="store_true",
                        help="削除予測のみ、実際には削除しない")
    args = parser.parse_args()

    db_url = os.getenv("DATABASE_URL", "").strip()
    is_pg = db_url.startswith(("postgres://", "postgresql://"))

    print(f"=== DB 容量チェック ({'Postgres' if is_pg else 'SQLite'}) ===")
    conn = db_connect()

    # サイズ取得
    if is_pg:
        sizes = get_table_sizes_postgres(conn)
        total_bytes = total_size_postgres(conn)
    else:
        sizes = get_table_sizes_sqlite(conn)
        # SQLite はファイルサイズが目安
        try:
            import config
            total_bytes = os.path.getsize(config.DB_PATH)
        except Exception:
            total_bytes = sum(s.get("bytes", 0) or 0 for s in sizes)

    total_mb = total_bytes / 1024 / 1024
    print(f"\n全体: {total_mb:.1f} MB")
    print("\nテーブル別サイズ (Top 10):")
    for s in sizes[:10]:
        print(f"  {s['name']:30s} {s['pretty']}")

    # クリーンアップ
    cleanup_result = None
    if args.cleanup or args.dry_run:
        print(f"\n=== 古い odds_trifecta 削除 (keep_days={args.keep_days}, dry_run={args.dry_run}) ===")
        cleanup_result = cleanup_old_odds(conn, is_pg, args.keep_days, args.dry_run)
        print(f"  {cleanup_result}")

    # system_status 更新
    status, msg = update_system_status(conn, total_bytes, sizes, cleanup_result)
    print(f"\n[{status.upper()}] {msg}")

    conn.close()
    if status == "error":
        sys.exit(2)
    if status == "warning":
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
