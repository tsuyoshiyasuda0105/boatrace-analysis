"""ログイン/会員確認のDB分離 (PoolTimeout 巻き込まれ防止) の回帰テスト。

- 会員確認 (ensure_profile / get_effective_role) は direct=True の
  専用接続を使い、共有プールの枯渇に巻き込まれないこと。
- connect(direct=True) は Postgres でプールを経由しないこと。
"""
import sqlite3

from src.db import connection
from src.web import membership


class _FakeRawConnection:
    autocommit = False

    def __init__(self):
        self.commands = []
        self.closed = 0

    def cursor(self):
        commands = self.commands

        class _Cursor:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def execute(self, sql):
                commands.append(sql)

        return _Cursor()

    def close(self):
        self.closed += 1


def test_direct_pg_connection_bypasses_pool(monkeypatch):
    raw = _FakeRawConnection()
    monkeypatch.delenv("BOATRACE_TASK_TRIGGER", raising=False)
    monkeypatch.setattr(connection, "_open_direct_pg_connection", lambda _dsn: raw)
    monkeypatch.setattr(
        connection,
        "_get_pg_pool",
        lambda _dsn: (_ for _ in ()).throw(
            AssertionError("direct connection must not touch the shared pool")
        ),
    )

    conn = connection._PgConnection("postgresql://unused", direct=True)
    conn.close()
    conn.close()

    assert raw.closed == 1


def test_connect_signature_accepts_direct_flag():
    source = open(connection.__file__, encoding="utf-8").read()
    assert "def connect(" in source
    assert "direct: bool = False" in source
    assert "_PgConnection(_normalize_pg_url(db_url), direct=direct)" in source


def test_membership_auth_lookups_use_direct_connection(monkeypatch):
    calls = []
    real = sqlite3.connect(":memory:")

    def fake_connect(db_path=None, direct=False):
        calls.append(direct)
        return real

    monkeypatch.setattr(membership, "db_connect", fake_connect)
    monkeypatch.setattr(membership, "_SCHEMA_CHECKED", False)

    membership.ensure_profile("user-1", "user@example.com")
    role = membership.get_effective_role("user-1")

    assert role == "free_member"
    assert calls, "membership lookups must open a DB connection"
    assert all(calls), "auth-critical membership queries must use direct=True"


def test_membership_non_auth_paths_keep_pooled_connection(monkeypatch):
    """課金一覧などの非認証経路は従来どおり共有プールを使う (direct を汚染しない)。"""
    calls = []
    real = sqlite3.connect(":memory:")
    real.execute(
        "CREATE TABLE profiles (id TEXT PRIMARY KEY, email TEXT, "
        "stripe_customer_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )

    def fake_connect(db_path=None, direct=False):
        calls.append(direct)
        return real

    monkeypatch.setattr(membership, "db_connect", fake_connect)

    membership.get_billing_profile("user-1")

    assert calls == [False]
