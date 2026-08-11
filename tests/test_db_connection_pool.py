from src.db import connection


class _FakeRawConnection:
    autocommit = False

    def __init__(self):
        self.commands = []

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


class _FakePool:
    def __init__(self):
        self.raw = _FakeRawConnection()
        self.returned = []

    def getconn(self):
        return self.raw

    def putconn(self, conn):
        self.returned.append(conn)


def test_pg_connection_returns_connection_to_pool_once(monkeypatch):
    pool = _FakePool()
    monkeypatch.setattr(connection, "_PG_POOL", pool)

    conn = connection._PgConnection("postgresql://unused")
    assert conn._conn is pool.raw
    assert pool.raw.autocommit is True

    conn.close()
    conn.close()

    assert pool.returned == [pool.raw]


def test_pg_pool_configures_connections_once_on_creation():
    raw = _FakeRawConnection()

    connection._configure_pg_connection(raw)

    assert raw.autocommit is True
    assert raw.commands == [
        "SET max_parallel_workers_per_gather = 0",
        "SET work_mem = '64MB'",
        "SET enable_hashjoin = on",
        "SET enable_mergejoin = off",
    ]
