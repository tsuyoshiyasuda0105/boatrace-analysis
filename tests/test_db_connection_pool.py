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

    def get_stats(self):
        return {"pool_size": 1, "pool_available": 0}


def test_pg_connection_returns_connection_to_pool_once(monkeypatch):
    pool = _FakePool()
    monkeypatch.delenv("BOATRACE_TASK_TRIGGER", raising=False)
    monkeypatch.setattr(connection, "_PG_POOL", pool)

    conn = connection._PgConnection("postgresql://unused")
    assert conn._conn is pool.raw
    assert pool.raw.autocommit is True

    conn.close()
    conn.close()

    assert pool.returned == [pool.raw]


def test_cron_connection_closes_physical_connection_instead_of_pooling(monkeypatch):
    raw = _FakeRawConnection()
    raw.closed = 0
    raw.close = lambda: setattr(raw, "closed", raw.closed + 1)
    monkeypatch.setenv("BOATRACE_TASK_TRIGGER", "render-cron")
    monkeypatch.setattr(connection, "_open_direct_pg_connection", lambda _dsn: raw)
    monkeypatch.setattr(
        connection,
        "_get_pg_pool",
        lambda _dsn: (_ for _ in ()).throw(AssertionError("cron must not create a pool")),
    )

    conn = connection._PgConnection("postgresql://unused")
    conn.close()
    conn.close()

    assert raw.closed == 1


def test_pg_pool_configures_connections_once_on_creation(monkeypatch):
    monkeypatch.delenv("BOATRACE_TASK_TRIGGER", raising=False)
    monkeypatch.delenv("BOATRACE_DB_STATEMENT_TIMEOUT_MS", raising=False)
    raw = _FakeRawConnection()

    connection._configure_pg_connection(raw)

    assert raw.autocommit is True
    assert raw.commands == [
        "SET max_parallel_workers_per_gather = 0",
        "SET work_mem = '16MB'",
        "SET statement_timeout = 8000",
        "SET lock_timeout = '3s'",
        "SET idle_in_transaction_session_timeout = '15s'",
        "SET enable_hashjoin = on",
        "SET enable_mergejoin = off",
    ]


def test_pg_pool_default_has_headroom_for_nested_web_queries():
    source = open(connection.__file__, encoding="utf-8").read()
    assert 'default_pool_size = "1" if trigger else "4"' in source
    assert "default_min_size = 0 if trigger else 1" in source
    assert 'os.getenv("BOATRACE_DB_POOL_MIN_SIZE", str(default_min_size))' in source
    assert 'os.getenv("BOATRACE_DB_POOL_SIZE", default_pool_size)' in source
    assert 'os.getenv("BOATRACE_DB_POOL_TIMEOUT_SEC", "5")' in source


def test_pg_pool_checkout_failure_logs_non_secret_stats(monkeypatch, caplog):
    class _FailingPool(_FakePool):
        def getconn(self):
            raise TimeoutError("busy")

    monkeypatch.delenv("BOATRACE_TASK_TRIGGER", raising=False)
    monkeypatch.setattr(connection, "_PG_POOL", _FailingPool())

    with caplog.at_level("ERROR"):
        try:
            connection._PgConnection("postgresql://unused")
        except TimeoutError:
            pass

    assert "pool_size" in caplog.text
    assert "postgresql://unused" not in caplog.text
