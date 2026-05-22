"""ファン手帳 TXT を racers テーブルに INSERT/UPDATE。

使い方:
  python scripts/ingest_fan_handbook.py                     # 既存 LZH をすべて取り込み
  python scripts/ingest_fan_handbook.py --file data/raw/fan/fan2604.txt
  python scripts/ingest_fan_handbook.py --dry-run           # SQL 実行せず統計だけ表示
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from src.db.connection import connect
from src.parsers.official_f import parse_fan_file

import config


def ingest_one(conn, rows: list[dict], dry_run: bool = False) -> dict:
    """rows を racers テーブルに INSERT OR REPLACE。"""
    today = date.today().isoformat()
    stats = {"inserted": 0, "updated": 0, "skipped": 0, "errors": 0}
    for r in rows:
        if r["racer_number"] is None:
            stats["skipped"] += 1
            continue
        if dry_run:
            stats["inserted"] += 1
            continue
        try:
            # branch_number は将来マップ。今は name にそのまま入れない。
            conn.execute(
                """INSERT OR REPLACE INTO racers
                   (racer_number, name, name_kana, gender, birth_date, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    r["racer_number"],
                    r["name"] or None,
                    r["name_kana"] or None,
                    r["gender"],
                    r["birth_date"],
                    today,
                ),
            )
            stats["inserted"] += 1
        except Exception as e:
            stats["errors"] += 1
            print(f"  ERR toban={r['racer_number']}: {e}")
    return stats


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--file", help="ファン手帳 TXT ファイルパス")
    p.add_argument("--dir", default=str(config.OFFICIAL_FAN_DIR),
                   help=f"ファン手帳ディレクトリ (default={config.OFFICIAL_FAN_DIR})")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--local", action="store_true",
                   help="ローカル SQLite に書き込む (DATABASE_URL を無視)。"
                        "未指定時は connect() に従い Supabase へ書込。")
    args = p.parse_args()

    # 対象ファイル一覧
    if args.file:
        files = [Path(args.file)]
    else:
        files = sorted(Path(args.dir).glob("fan*.txt"))
        if not files:
            files = sorted(Path(args.dir).glob("fan*.TXT"))

    if not files:
        print(f"ファイルが見つかりません: {args.dir}")
        return

    print(f"対象ファイル: {len(files)} 件")

    if args.local:
        # load_dotenv が DATABASE_URL を再読込するため、connect() だと
        # Supabase に行ってしまう。ローカル SQLite を明示指定。
        import sqlite3
        conn = sqlite3.connect(config.DB_PATH)
        print(f"  書込先: ローカル SQLite ({config.DB_PATH})")
    else:
        conn = connect()
        print("  書込先: connect() に従う (DATABASE_URL 設定時は Supabase)")

    # 統合: 全ファイル parse → 最新の更新日時順に INSERT OR REPLACE
    # 同じ toban が複数ファイルに出る場合は、ファイル名が新しい方 (より最近) が優先される
    grand = {"inserted": 0, "skipped": 0, "errors": 0}
    for fp in files:
        print(f"\n--- {fp.name} ---")
        rows = parse_fan_file(fp)
        print(f"  parsed: {len(rows)} 行")
        from collections import Counter
        gc = Counter(r["gender"] for r in rows if r["gender"])
        print(f"  gender: 男={gc.get(1,0):,} 女={gc.get(2,0):,}")
        if args.dry_run:
            continue
        s = ingest_one(conn, rows, dry_run=args.dry_run)
        print(f"  inserted: {s['inserted']:,}, errors: {s['errors']}")
        for k in grand:
            grand[k] += s.get(k, 0)

    if not args.dry_run:
        conn.commit()
        # 最終確認
        cur = conn.execute("SELECT gender, COUNT(*) FROM racers GROUP BY gender ORDER BY gender")
        print("\n=== DB 最終状態 ===")
        for g, n in cur.fetchall():
            label = {1: "男", 2: "女"}.get(g, "?")
            print(f"  gender={g} ({label}): {n:,} 名")

    print()
    print(f"合計 inserted: {grand['inserted']:,}, errors: {grand['errors']:,}")


if __name__ == "__main__":
    main()
