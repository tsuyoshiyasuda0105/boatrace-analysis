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
    """途中オッズのみ、keep_days 日以前の odds_trifecta を削除。
    final オッズ (`is_final=1` または `snapshot_label='final'`) は保持する。
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
           AND COALESCE(o.is_final, 0) = 0
           AND COALESCE(o.snapshot_label, '') <> 'final'
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
           AND COALESCE(is_final, 0) = 0
           AND COALESCE(snapshot_label, '') <> 'final'
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


def cleanup_old_raw_data(conn, is_pg: bool, keep_days: int = 90,
                          dry_run: bool = False) -> dict:
    """生データ (race_entries / race_payouts / race_results / race_previews /
    predictions / odds_trifecta) のうち、l4_daily_summary に集計済みの古い
    日付を削除して容量を回復させる。

    安全機構:
      1. 削除前に l4_daily_summary にその日付が存在することを確認
         (集計が無い日付は ROI ダッシュボードで fallback できないので削除しない)
      2. keep_days より新しい日付は触らない (運用継続性確保)
      3. dry_run=True で削除予測のみ表示

    Args:
      keep_days: 保持日数 (default=90 日 ≒ 3 ヶ月)
      dry_run: True なら削除予測のみ

    Returns:
      {'deleted_rows': {table: n}, 'deleted_dates': N, 'cutoff_date': ...}
    """
    cutoff = (date.today() - timedelta(days=keep_days)).isoformat()
    logger.info("aggressive cleanup: cutoff=%s (keep_days=%d)", cutoff, keep_days)

    # 1. 削除候補日付: races の日付 < cutoff かつ l4_daily_summary に集計済み
    cur = conn.execute("""
        SELECT DISTINCT r.race_date
          FROM races r
         WHERE r.race_date < ?
           AND r.race_date IN (SELECT date FROM l4_daily_summary)
         ORDER BY r.race_date
    """, (cutoff,))
    target_dates = [row[0] for row in cur.fetchall()]
    if not target_dates:
        logger.info("削除対象日付なし (l4_daily_summary に集計済の古い日付がない)")
        return {"deleted_rows": {}, "deleted_dates": 0, "cutoff_date": cutoff}

    logger.info("削除対象日付: %d 日 (%s 〜 %s)",
                len(target_dates), target_dates[0], target_dates[-1])

    # 各テーブルの削除行数を見積もり
    TABLES = [
        ("odds_trifecta",    "race_id IN (SELECT race_id FROM races WHERE race_date < ?)"),
        ("race_payouts",     "race_id IN (SELECT race_id FROM races WHERE race_date < ?)"),
        ("race_results",     "race_id IN (SELECT race_id FROM races WHERE race_date < ?)"),
        ("race_previews",    "race_id IN (SELECT race_id FROM races WHERE race_date < ?)"),
        ("predictions",      "race_id IN (SELECT race_id FROM races WHERE race_date < ?)"),
        ("race_entries",     "race_id IN (SELECT race_id FROM races WHERE race_date < ?)"),
        ("races",            "race_date < ?"),  # 親テーブルは最後
    ]

    plan = {}
    for table, where in TABLES:
        try:
            count_sql = f"SELECT COUNT(*) FROM {table} WHERE {where}"
            cur = conn.execute(count_sql, (cutoff,))
            plan[table] = cur.fetchone()[0] or 0
        except Exception as e:
            logger.warning("count failed %s: %s", table, e)
            plan[table] = 0

    total_rows = sum(plan.values())
    logger.info("削除予定 行数:")
    for table, n in plan.items():
        logger.info("  %-20s %s rows", table, f"{n:,}")
    logger.info("  TOTAL: %s rows across %d tables", f"{total_rows:,}", len([t for t,n in plan.items() if n>0]))

    if dry_run:
        return {
            "deleted_rows": {},
            "would_delete": plan,
            "deleted_dates": len(target_dates),
            "cutoff_date": cutoff,
        }

    if total_rows == 0:
        return {"deleted_rows": {}, "deleted_dates": 0, "cutoff_date": cutoff}

    # 削除実行 (依存関係: 子 → 親の順)
    deleted = {}
    for table, where in TABLES:
        if plan[table] == 0:
            continue
        try:
            del_sql = f"DELETE FROM {table} WHERE {where}"
            conn.execute(del_sql, (cutoff,))
            conn.commit()
            deleted[table] = plan[table]
            logger.info("削除完了: %-20s %s rows", table, f"{plan[table]:,}")
        except Exception as e:
            logger.error("削除失敗 %s: %s (継続)", table, e)
            try:
                conn.rollback()
            except Exception:
                pass

    # Postgres は VACUUM で実領域回収。
    # 通常 VACUUM ANALYZE: 統計更新 + dead tuple 回収 (ファイルサイズは縮小しない)
    # VACUUM FULL:        ファイルサイズも縮小 (テーブルロック取得、時間かかる)
    # 大量削除時は VACUUM FULL で物理サイズも縮小する必要がある。
    total_deleted_rows = sum(deleted.values())
    use_vacuum_full = total_deleted_rows >= 50000  # 50,000 行超で VACUUM FULL
    if is_pg:
        try:
            raw = getattr(conn, "_conn", None)
            if raw is None:
                logger.warning("VACUUM スキップ: 内部 psycopg connection が取得できない")
            else:
                try:
                    raw.commit()
                except Exception:
                    pass
                cur = raw.cursor()
                cmd = "VACUUM FULL" if use_vacuum_full else "VACUUM ANALYZE"
                logger.info("%s を実行 (大量削除のため)" if use_vacuum_full
                            else "%s を実行", cmd)
                for table in deleted.keys():
                    try:
                        cur.execute(f"{cmd} {table}")
                        logger.info("  %s %s 完了", cmd, table)
                    except Exception as e:
                        logger.warning("  %s %s 失敗: %s", cmd, table, e)
                cur.close()
        except Exception as e:
            logger.warning("VACUUM 失敗 (削除自体は成功): %s", e)

    return {
        "deleted_rows": deleted,
        "deleted_dates": len(target_dates),
        "cutoff_date": cutoff,
        "total_deleted": sum(deleted.values()),
    }


def _resolve_capacity_limit_mb(is_pg: bool) -> tuple[float, str]:
    """Return the capacity baseline used for db_capacity alerts."""
    raw = (os.getenv("BOATRACE_DB_LIMIT_MB", "") or "").strip()
    if raw:
        try:
            limit = float(raw)
            if limit > 0:
                return limit, "env:BOATRACE_DB_LIMIT_MB"
        except ValueError:
            logger.warning("invalid BOATRACE_DB_LIMIT_MB=%r; fallback defaults will be used", raw)
    if is_pg:
        # Current production uses a managed Postgres plan far above the old 500MB free limit.
        return 8192.0, "default:postgres_8gb"
    # Local SQLite is kept for backtests and can legitimately exceed 500MB.
    return 10240.0, "default:sqlite_10gb"


def update_system_status(conn, total_bytes: int, sizes: list[dict],
                          cleanup_result: dict | None = None,
                          *, is_pg: bool = False):
    """システム状態テーブルに DB 使用量を記録"""
    today_iso = date.today().isoformat()
    total_mb = total_bytes / 1024 / 1024
    limit_mb, limit_source = _resolve_capacity_limit_mb(is_pg)
    db_kind = "postgres" if is_pg else "sqlite"
    db_label = "Supabase/Postgres" if is_pg else "local SQLite"
    pct = total_mb / limit_mb * 100 if limit_mb > 0 else 0
    error_mb = limit_mb * 0.96
    warn_mb = limit_mb * 0.80
    if total_mb >= error_mb:
        status, msg = "error", f"DB 容量逼迫 ({db_label}): {total_mb:.0f} MB / {limit_mb:.0f} MB ({pct:.0f}%)"
    elif total_mb >= warn_mb:
        status, msg = "warning", f"DB 容量 80% 超 ({db_label}): {total_mb:.0f} MB / {limit_mb:.0f} MB ({pct:.0f}%)"
    else:
        status, msg = "ok", f"DB 容量 ({db_label}): {total_mb:.0f} MB / {limit_mb:.0f} MB ({pct:.0f}%)"
    detail = {
        "total_bytes": total_bytes, "total_mb": round(total_mb, 1),
        "limit_mb": limit_mb, "pct": round(pct, 1),
        "db_kind": db_kind,
        "db_label": db_label,
        "limit_source": limit_source,
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
    parser.add_argument("--cleanup-raw", action="store_true",
                        help="古い生データ (race_entries 等) を削除 "
                             "(l4_daily_summary に集計済の日付のみ、--keep-days より古い)")
    parser.add_argument("--keep-days", type=int, default=60,
                        help="odds_trifecta の保持日数 (default=60)")
    parser.add_argument("--keep-raw-days", type=int, default=90,
                        help="生データの保持日数 (default=90、--cleanup-raw 時のみ有効)")
    parser.add_argument("--auto", action="store_true",
                        help="DB 使用量 >=80%% で生データを自動クリーンアップ "
                             "(BOATRACE_AUTO_CLEANUP=1 でも有効化)")
    parser.add_argument("--dry-run", action="store_true",
                        help="削除予測のみ、実際には削除しない")
    args = parser.parse_args()
    # 環境変数で自動クリーンアップを有効化
    auto_enabled = args.auto or os.environ.get("BOATRACE_AUTO_CLEANUP") == "1"

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
    if args.cleanup or (args.dry_run and not args.cleanup_raw):
        print(f"\n=== 古い odds_trifecta 削除 (keep_days={args.keep_days}, dry_run={args.dry_run}) ===")
        cleanup_result = cleanup_old_odds(conn, is_pg, args.keep_days, args.dry_run)
        print(f"  {cleanup_result}")

    # 生データの aggressive クリーンアップ
    raw_cleanup_result = None
    # 自動トリガー: 使用量 >= 80% かつ --auto/環境変数有効
    trigger_auto = (auto_enabled and total_mb >= 400)
    if args.cleanup_raw or trigger_auto:
        if trigger_auto and not args.cleanup_raw:
            print(f"\n⚠ 自動クリーンアップ起動 (使用量 {total_mb:.0f} MB >= 400 MB)")
        print(f"\n=== 古い生データ削除 (keep_raw_days={args.keep_raw_days}, dry_run={args.dry_run}) ===")
        raw_cleanup_result = cleanup_old_raw_data(
            conn, is_pg, args.keep_raw_days, args.dry_run
        )
        print(f"  {raw_cleanup_result}")
        # 削除後のサイズ再取得 (現状把握のため)
        if not args.dry_run and raw_cleanup_result.get("total_deleted", 0) > 0:
            if is_pg:
                total_bytes = total_size_postgres(conn)
                total_mb = total_bytes / 1024 / 1024
                print(f"  クリーンアップ後の DB 容量: {total_mb:.1f} MB")

    # system_status 更新 (両方のクリーンアップ結果を統合)
    merged_cleanup = {}
    if cleanup_result:
        merged_cleanup["odds_trifecta"] = cleanup_result
    if raw_cleanup_result:
        merged_cleanup["raw_data"] = raw_cleanup_result
    status, msg = update_system_status(
        conn, total_bytes, sizes, merged_cleanup if merged_cleanup else None, is_pg=is_pg
    )
    print(f"\n[{status.upper()}] {msg}")

    conn.close()
    if status == "error":
        sys.exit(2)
    if status == "warning":
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
