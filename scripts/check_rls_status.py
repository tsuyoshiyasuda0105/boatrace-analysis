"""Supabase テーブルの RLS 状態を確認するスクリプト。

確認内容:
  1. public スキーマの全テーブルの rowsecurity (RLS が ON か OFF か)
  2. 各テーブルに設定されている POLICY の一覧
  3. anon / authenticated ロールに付与されている権限 (SELECT/INSERT/UPDATE/DELETE)

実行: python scripts/check_rls_status.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from src.db.connection import connect

conn = connect()

# 1. RLS 有効/無効の状態
print("=" * 80)
print("【1. public スキーマ全テーブルの RLS 状態】")
print("=" * 80)
cur = conn.execute("""
    SELECT n.nspname, c.relname, c.relrowsecurity, c.relforcerowsecurity
      FROM pg_class c
      JOIN pg_namespace n ON c.relnamespace = n.oid
     WHERE n.nspname = 'public' AND c.relkind = 'r'
     ORDER BY c.relname
""")
rows = cur.fetchall()
print(f"{'table':<35} {'RLS_ON':<10} {'FORCED':<10}")
print("-" * 60)
n_on = 0
n_off = 0
for schema, table, rls, forced in rows:
    state = "ON OK" if rls else "OFF WARN"
    fstate = "YES" if forced else "no"
    print(f"{table:<35} {state:<10} {fstate:<10}")
    if rls:
        n_on += 1
    else:
        n_off += 1
print(f"\n合計: {len(rows)} テーブル (RLS有効: {n_on}, RLS無効: {n_off})")

# 2. POLICY 一覧
print()
print("=" * 80)
print("【2. POLICY 一覧】")
print("=" * 80)
cur = conn.execute("""
    SELECT schemaname, tablename, policyname, cmd, roles, qual
      FROM pg_policies
     WHERE schemaname = 'public'
     ORDER BY tablename, policyname
""")
rows = cur.fetchall()
if not rows:
    print("WARN POLICY は 1 つも設定されていません")
else:
    for schema, table, pol, cmd, roles, qual in rows:
        print(f"  {table}: [{cmd}] {pol} roles={roles} qual={qual}")

# 3. anon / authenticated に付与された権限
print()
print("=" * 80)
print("【3. anon / authenticated ロールへの権限付与】")
print("=" * 80)
cur = conn.execute("""
    SELECT grantee, table_name, privilege_type
      FROM information_schema.role_table_grants
     WHERE table_schema = 'public'
       AND grantee IN ('anon', 'authenticated', 'service_role', 'PUBLIC')
     ORDER BY table_name, grantee, privilege_type
""")
rows = cur.fetchall()
from collections import defaultdict
by_table = defaultdict(lambda: defaultdict(list))
for grantee, table, priv in rows:
    by_table[table][grantee].append(priv)

if not by_table:
    print("(anon/authenticated に直接付与された権限なし)")
else:
    for table in sorted(by_table.keys()):
        for grantee in sorted(by_table[table].keys()):
            privs = ", ".join(sorted(by_table[table][grantee]))
            warn = " WARN" if grantee in ("anon", "PUBLIC") else ""
            print(f"  {table:<32} → {grantee:<15} : {privs}{warn}")

# 4. サマリ
print()
print("=" * 80)
print("【4. 総合判定】")
print("=" * 80)
risks = []
if n_off > 0:
    risks.append(f"RLS無効テーブルが {n_off} 件 → anon ロールで全件読取可能の可能性")
if not [r for r in cur.fetchall()]:
    pass  # cur exhausted, no second pass

# 再度クエリ: anon に SELECT がある & RLS 無効のテーブル数
cur = conn.execute("""
    SELECT t.tablename
      FROM pg_tables t
      LEFT JOIN information_schema.role_table_grants g
             ON g.table_name = t.tablename
            AND g.table_schema = t.schemaname
            AND g.grantee = 'anon'
            AND g.privilege_type = 'SELECT'
     WHERE t.schemaname = 'public'
       AND t.rowsecurity = false
       AND g.table_name IS NOT NULL
""")
exposed = [r[0] for r in cur.fetchall()]
if exposed:
    risks.append(f"anon に SELECT 権限がありかつ RLS 無効: {len(exposed)} テーブル → 外部から閲覧可能")
    for t in exposed[:10]:
        print(f"    !!! {t}")
    if len(exposed) > 10:
        print(f"    ... 他 {len(exposed)-10} テーブル")

if risks:
    print()
    print("WARN リスク:")
    for r in risks:
        print(f"  - {r}")
else:
    print("OK 直接的なリスクは検出されませんでした")
