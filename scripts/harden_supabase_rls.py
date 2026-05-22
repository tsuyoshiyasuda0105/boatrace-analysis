"""Supabase テーブルのセキュリティハードニング (RLS + REVOKE)

目的:
  Supabase は新規プロジェクト作成時、public スキーマの全テーブルに対して
  anon / authenticated ロールに ALL 権限 (SELECT/INSERT/UPDATE/DELETE/TRUNCATE) を
  自動付与する。RLS が無効のままだと、anon key を知る誰でも全データを
  読み書き可能になる致命的な状態。

  本アプリは PostgREST (REST API) を一切使わず、Flask が DATABASE_URL の
  postgres ロールで直接 SQL を実行しているだけ。したがって anon /
  authenticated ロールの権限を全削除しても何も壊れない。

実行内容:
  1. public スキーマの全テーブルに対して
       REVOKE ALL ON <table> FROM anon, authenticated, PUBLIC
  2. 念の為 RLS も有効化 (deny-by-default; POLICY なし = 誰もアクセス不可)
  3. 将来追加されるテーブルも自動で REVOKE 対象になるよう DEFAULT PRIVILEGES を変更

確認:
  実行後 scripts/check_rls_status.py を再実行して RLS_ON と anon 権限が
  「OK」になっていることを確認。

実行: python scripts/harden_supabase_rls.py [--dry-run]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from src.db.connection import connect


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="SQL を表示するだけで実行しない")
    args = parser.parse_args()

    conn = connect()

    # public スキーマの全テーブル取得
    cur = conn.execute("""
        SELECT c.relname
          FROM pg_class c
          JOIN pg_namespace n ON c.relnamespace = n.oid
         WHERE n.nspname = 'public' AND c.relkind = 'r'
         ORDER BY c.relname
    """)
    tables = [r[0] for r in cur.fetchall()]
    print(f"対象テーブル: {len(tables)} 件")
    for t in tables:
        print(f"  - {t}")

    # 構築する SQL 群
    statements = []

    # 1. 既存テーブルから anon / authenticated / PUBLIC の権限を全削除
    for t in tables:
        # PUBLIC は擬似ロール、SQLでは PUBLIC キーワード
        statements.append(
            f'REVOKE ALL ON TABLE public."{t}" FROM anon, authenticated, PUBLIC'
        )

    # 2. RLS を有効化 (deny-by-default で二重防御)
    for t in tables:
        statements.append(
            f'ALTER TABLE public."{t}" ENABLE ROW LEVEL SECURITY'
        )

    # 3. 将来追加されるテーブルも自動で REVOKE 対象に
    statements.append(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "REVOKE ALL ON TABLES FROM anon, authenticated, PUBLIC"
    )

    print()
    print(f"実行予定 SQL: {len(statements)} 文")
    for s in statements[:5]:
        print(f"  {s}")
    if len(statements) > 5:
        print(f"  ... 他 {len(statements) - 5} 文")

    if args.dry_run:
        print()
        print("[DRY-RUN] 実行スキップ")
        return

    print()
    print("実行中...")
    n_ok = 0
    n_err = 0
    for s in statements:
        try:
            conn.execute(s)
            n_ok += 1
        except Exception as e:
            n_err += 1
            print(f"  ERR: {s[:80]}... -> {e}")

    print()
    print(f"完了: 成功 {n_ok}, 失敗 {n_err}")
    print()
    print("確認: python scripts/check_rls_status.py を実行してください")


if __name__ == "__main__":
    main()
