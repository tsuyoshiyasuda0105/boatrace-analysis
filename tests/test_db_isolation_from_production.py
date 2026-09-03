# -*- coding: utf-8 -*-
"""テストが本番 Postgres に繋がっていないことを固定する回帰テスト。

以前は .env の DATABASE_URL が生きたままテストが走り、`connect()` が
本番 Supabase を返していた。読み取りだけの偶然で助かっていたが、
書き込むテストが 1 つでも入れば本番データを壊す状態だった。
ルート conftest.py で DATABASE_URL を空にすることで塞いだ回帰テスト。
"""
import os

from src.db.connection import connect


def test_database_url_is_cleared_during_tests():
    # 空文字であることを厳密に確認 (未定義でも困る)。
    # 失敗時に接続文字列 (パスワード含む) をログに出さないため、値は
    # 表示せず「本番相当の URL が漏れている」ことだけを伝える。
    value = os.environ.get("DATABASE_URL")
    if value == "":
        return
    leaked = bool(value) and value.startswith(("postgres://", "postgresql://"))
    assert not leaked, (
        "DATABASE_URL contains a production-shaped URL during tests; "
        "the root conftest.py must clear it before any project import "
        "(value is intentionally not printed to keep it out of test logs)."
    )
    assert value == "", (
        "DATABASE_URL should be empty during tests "
        "(non-postgres value found; value not printed)"
    )


def test_connect_does_not_return_production_postgres():
    conn = connect()
    kind = type(conn).__name__
    try:
        conn.close()
    except Exception:  # noqa: BLE001
        pass
    assert "Pg" not in kind, (
        f"tests must not reach production Postgres; connect() returned {kind}"
    )


def test_direct_connect_does_not_return_production_postgres():
    # direct=True 経路 (プールを迂回する scripts/バッチ系) も同じく塞がっている。
    conn = connect(direct=True)
    kind = type(conn).__name__
    try:
        conn.close()
    except Exception:  # noqa: BLE001
        pass
    assert "Pg" not in kind, (
        f"direct connect() must not reach production Postgres; got {kind}"
    )
