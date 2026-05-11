"""
ローカル SQLite → Supabase Postgres へデータ同期

使い方:
  $env:DATABASE_URL = "postgresql://postgres.npjxlqkbytgdxrvebnjr:..."
  python scripts/sync_to_supabase.py --start 2026-01-01 --end 2026-05-12
  python scripts/sync_to_supabase.py --tables races,race_entries,race_results
"""
import argparse
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from src.db.connection import connect as db_connect


def sync_table(src: sqlite3.Connection, dst, table: str, where: str = "1=1",
               params: tuple = (), batch_size: int = 500, verbose: bool = False):
    """1テーブルを SQLite → Postgres にコピー"""
    # カラム一覧取得
    cur = src.execute(f"PRAGMA table_info({table})")
    cols = [r[1] for r in cur.fetchall()]
    if not cols:
        print(f"  [{table}] テーブルなし")
        return 0
    col_list = ", ".join(cols)
    placeholders = ", ".join(["?"] * len(cols))

    # 主キー特定 (UPSERT 用)
    pk_cols = [r[1] for r in src.execute(f"PRAGMA table_info({table})").fetchall() if r[5] > 0]
    if not pk_cols:
        # スキーマから主キー推定
        from src.db.connection import _TABLE_PRIMARY_KEYS
        pk_cols = _TABLE_PRIMARY_KEYS.get(table, [])

    # SQLite から行を読み出し
    cur = src.execute(f"SELECT {col_list} FROM {table} WHERE {where}", params)
    total = 0
    batch = []
    for row in cur:
        batch.append(tuple(row))
        if len(batch) >= batch_size:
            sql = f"INSERT OR REPLACE INTO {table} ({col_list}) VALUES ({placeholders})"
            dst.executemany(sql, batch)
            total += len(batch)
            batch.clear()
            if verbose:
                print(f"  [{table}] {total:,} rows synced")
    if batch:
        sql = f"INSERT OR REPLACE INTO {table} ({col_list}) VALUES ({placeholders})"
        dst.executemany(sql, batch)
        total += len(batch)
    if verbose:
        print(f"  [{table}] 完了: {total:,} rows")
    return total


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", type=str, default="2026-01-01")
    p.add_argument("--end", type=str, default=datetime.now().strftime("%Y-%m-%d"))
    p.add_argument("--tables", type=str, default="races,race_entries,race_previews,race_results,race_payouts",
                   help="同期するテーブル (カンマ区切り)")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    # DATABASE_URL チェック
    db_url = os.getenv("DATABASE_URL", "").strip()
    if not db_url or not db_url.startswith(("postgres://", "postgresql://")):
        print("ERROR: DATABASE_URL が Postgres URL に設定されていません")
        print("  例: $env:DATABASE_URL = 'postgresql://postgres.xxx:pass@aws-1-...pooler.supabase.com:5432/postgres'")
        sys.exit(1)

    print(f"=== ローカル SQLite → Supabase Postgres 同期 ===")
    print(f"  期間: {args.start} ~ {args.end}")
    print(f"  対象テーブル: {args.tables}")
    print()

    # ソース (ローカル SQLite)
    src = sqlite3.connect(config.DB_PATH)

    # 対象 race_id 一覧 (期間内)
    cur = src.execute(
        "SELECT race_id FROM races WHERE race_date >= ? AND race_date <= ?",
        (args.start, args.end),
    )
    race_ids = [r[0] for r in cur.fetchall()]
    print(f"  対象レース数: {len(race_ids):,}")
    if not race_ids:
        print("  該当レースなし")
        return

    # 宛先 (Supabase Postgres、DATABASE_URL から自動接続)
    dst = db_connect()

    tables = [t.strip() for t in args.tables.split(",")]

    # まず stadiums マスタも同期
    if "stadiums" not in tables:
        n = sync_table(src, dst, "stadiums", verbose=args.verbose)
        print(f"stadiums: {n} rows")

    for table in tables:
        print(f"\n[{table}]")
        if table == "races":
            n = sync_table(src, dst, "races",
                          "race_date >= ? AND race_date <= ?",
                          (args.start, args.end),
                          verbose=args.verbose)
        else:
            # race_id ベースで絞る
            # SQLite では parameter limit 999 なので分割
            CHUNK = 500
            n = 0
            for i in range(0, len(race_ids), CHUNK):
                chunk = race_ids[i:i+CHUNK]
                placeholders = ",".join(["?"] * len(chunk))
                n += sync_table(src, dst, table,
                              f"race_id IN ({placeholders})",
                              tuple(chunk),
                              verbose=args.verbose)
        print(f"  → 完了: {n:,} rows")

    src.close()
    dst.close()
    print("\n=== 同期完了 ===")


if __name__ == "__main__":
    main()
