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


class _ClosablePool(_FakePool):
    def __init__(self):
        super().__init__()
        self.closed = 0

    def close(self):
        self.closed += 1


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
    # 2026-08-24 に 4 → 8。gunicorn は 1 プロセス 4 スレッドなので、枠が 4 だと
    # 入れ子に db_connect() する経路で 4 スレッドとも「1 本持って 2 本目を待つ」
    # 状態になり、誰も進めないまま 5 秒の取得待ちを 2 回払って (=10.15 秒)
    # レース詳細が「準備しています」に落ちた。枠はスレッド数の 2 倍を既定にする
    # (この不変条件は tests/test_db_pool_warmth.py が render.yaml と突き合わせる)。
    assert 'default_pool_size = "1" if trigger else "8"' in source
    # min_size は 2026-08-22 に 1 → max_size と同数にした。
    # Render(シンガポール)→Supabase(東京) の新規接続は実測 2.5 秒で、
    # min_size=1 だと 2 本目以降を毎回張り直し、その待ちがリクエスト予算を
    # 食い潰してレース詳細が「準備中」に落ちていた。
    assert "default_min_size = 0 if trigger else 8" in source
    assert 'os.getenv("BOATRACE_DB_POOL_MIN_SIZE", str(default_min_size))' in source
    assert 'os.getenv("BOATRACE_DB_POOL_SIZE", default_pool_size)' in source
    assert 'os.getenv("BOATRACE_DB_POOL_TIMEOUT_SEC", "5")' in source


def test_web_pool_has_finite_wait_queue_matching_configured_max(monkeypatch):
    import psycopg_pool

    captured = {}

    class _CapturingPool:
        check_connection = object()

        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.delenv("BOATRACE_TASK_TRIGGER", raising=False)
    monkeypatch.setenv("BOATRACE_DB_POOL_SIZE", "8")
    monkeypatch.delenv("BOATRACE_DB_POOL_MAX_WAITING", raising=False)
    monkeypatch.setattr(connection, "_PG_POOL", None)
    monkeypatch.setattr(psycopg_pool, "ConnectionPool", _CapturingPool)

    connection._get_pg_pool("postgresql://unused")

    assert captured["max_size"] == 8
    assert captured["max_waiting"] == 8


def test_web_pool_zero_max_waiting_cannot_restore_unbounded_queue(monkeypatch):
    import psycopg_pool

    captured = {}

    class _CapturingPool:
        check_connection = object()

        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.delenv("BOATRACE_TASK_TRIGGER", raising=False)
    monkeypatch.setenv("BOATRACE_DB_POOL_MAX_WAITING", "0")
    monkeypatch.setattr(connection, "_PG_POOL", None)
    monkeypatch.setattr(psycopg_pool, "ConnectionPool", _CapturingPool)

    connection._get_pg_pool("postgresql://unused")

    assert captured["max_waiting"] == 1


def test_cron_pool_configuration_keeps_wait_queue_unbounded(monkeypatch):
    import psycopg_pool

    captured = {}

    class _CapturingPool:
        check_connection = object()

        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setenv("BOATRACE_TASK_TRIGGER", "render-cron")
    monkeypatch.delenv("BOATRACE_DB_POOL_MAX_WAITING", raising=False)
    monkeypatch.setattr(connection, "_PG_POOL", None)
    monkeypatch.setattr(psycopg_pool, "ConnectionPool", _CapturingPool)

    connection._get_pg_pool("postgresql://unused")

    assert captured["max_waiting"] == 0


def test_pool_queue_overflow_is_transient():
    from psycopg_pool import TooManyRequests

    assert connection.is_transient_db_error(TooManyRequests("queue is full"))


def test_pool_queue_overflow_fails_immediately_without_retry_sleep(monkeypatch):
    from psycopg_pool import TooManyRequests

    class _FullQueuePool(_FakePool):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def getconn(self):
            self.calls += 1
            raise TooManyRequests("queue is full")

    pool = _FullQueuePool()
    sleeps = []
    monkeypatch.setattr(connection.time, "sleep", sleeps.append)

    try:
        connection._acquire_pg_connection(
            "postgresql://unused", direct=False, pool=pool
        )
    except TooManyRequests:
        pass
    else:
        raise AssertionError("a full wait queue must fail immediately")

    assert pool.calls == 1
    assert sleeps == []


def test_watchdog_rebuilds_only_after_sustained_failed_exhaustion(monkeypatch):
    pool = _ClosablePool()
    stats = {"pool_available": 0, "requests_waiting": 8}
    monkeypatch.delenv("BOATRACE_TASK_TRIGGER", raising=False)
    monkeypatch.setattr(connection, "_PG_POOL", pool)
    monkeypatch.setattr(connection, "_PG_POOL_EXHAUSTED_SINCE", None)
    monkeypatch.setattr(connection, "_PG_POOL_EXHAUSTION_FAILURES", 0)
    monkeypatch.setattr(connection, "_PG_POOL_LAST_REBUILD_AT", None)

    assert not connection._maybe_rebuild_exhausted_pg_pool(
        pool, stats, now=100.0, exhaustion_sec=90.0, cooldown_sec=60.0
    )
    assert not connection._maybe_rebuild_exhausted_pg_pool(
        pool, stats, now=189.9, exhaustion_sec=90.0, cooldown_sec=60.0
    )
    assert connection._maybe_rebuild_exhausted_pg_pool(
        pool, stats, now=190.0, exhaustion_sec=90.0, cooldown_sec=60.0
    )
    assert connection._PG_POOL is None
    assert pool.closed == 1


def test_watchdog_does_not_rebuild_for_momentary_saturation(monkeypatch):
    pool = _ClosablePool()
    monkeypatch.delenv("BOATRACE_TASK_TRIGGER", raising=False)
    monkeypatch.setattr(connection, "_PG_POOL", pool)
    monkeypatch.setattr(connection, "_PG_POOL_EXHAUSTED_SINCE", None)
    monkeypatch.setattr(connection, "_PG_POOL_EXHAUSTION_FAILURES", 0)
    monkeypatch.setattr(connection, "_PG_POOL_LAST_REBUILD_AT", None)

    assert not connection._maybe_rebuild_exhausted_pg_pool(
        pool,
        {"pool_available": 0, "requests_waiting": 3},
        now=100.0,
        exhaustion_sec=90.0,
        cooldown_sec=60.0,
    )
    connection._note_pg_pool_checkout_success(pool)
    assert not connection._maybe_rebuild_exhausted_pg_pool(
        pool,
        {"pool_available": 0, "requests_waiting": 2},
        now=300.0,
        exhaustion_sec=90.0,
        cooldown_sec=60.0,
    )
    assert connection._PG_POOL is pool
    assert pool.closed == 0


def test_watchdog_respects_rebuild_cooldown(monkeypatch):
    pool = _ClosablePool()
    stats = {"pool_available": 0, "requests_waiting": 4}
    monkeypatch.delenv("BOATRACE_TASK_TRIGGER", raising=False)
    monkeypatch.setattr(connection, "_PG_POOL", pool)
    monkeypatch.setattr(connection, "_PG_POOL_EXHAUSTED_SINCE", 200.0)
    monkeypatch.setattr(connection, "_PG_POOL_EXHAUSTION_FAILURES", 1)
    monkeypatch.setattr(connection, "_PG_POOL_LAST_REBUILD_AT", 190.0)

    assert not connection._maybe_rebuild_exhausted_pg_pool(
        pool, stats, now=220.0, exhaustion_sec=10.0, cooldown_sec=60.0
    )
    assert connection._maybe_rebuild_exhausted_pg_pool(
        pool, stats, now=250.0, exhaustion_sec=10.0, cooldown_sec=60.0
    )
    assert pool.closed == 1


def test_watchdog_is_disabled_for_cron_processes(monkeypatch):
    pool = _ClosablePool()
    monkeypatch.setenv("BOATRACE_TASK_TRIGGER", "render-cron")
    monkeypatch.setattr(connection, "_PG_POOL", pool)
    monkeypatch.setattr(connection, "_PG_POOL_EXHAUSTED_SINCE", 0.0)
    monkeypatch.setattr(connection, "_PG_POOL_EXHAUSTION_FAILURES", 9)

    assert not connection._maybe_rebuild_exhausted_pg_pool(
        pool,
        {"pool_available": 0, "requests_waiting": 99},
        now=999.0,
        exhaustion_sec=1.0,
        cooldown_sec=0.0,
    )
    assert connection._PG_POOL is pool
    assert pool.closed == 0


def test_pg_pool_checkout_failure_logs_non_secret_stats_without_error_handler(monkeypatch, caplog):
    class _FailingPool(_FakePool):
        def getconn(self):
            raise TimeoutError("busy")

    monkeypatch.delenv("BOATRACE_TASK_TRIGGER", raising=False)
    monkeypatch.setattr(connection, "_PG_POOL", _FailingPool())

    with caplog.at_level("WARNING"):
        try:
            connection._PgConnection("postgresql://unused")
        except TimeoutError:
            pass

    assert "pool_size" in caplog.text
    assert "postgresql://unused" not in caplog.text
    assert all(record.levelno < 40 for record in caplog.records)


def test_pg_pool_timeout_retries_twice_with_bounded_backoff(monkeypatch):
    class _TransientPool(_FakePool):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def getconn(self):
            self.calls += 1
            if self.calls <= 2:
                raise TimeoutError("couldn't get a connection after 5.00 sec")
            return self.raw

    pool = _TransientPool()
    sleeps = []
    monkeypatch.delenv("BOATRACE_TASK_TRIGGER", raising=False)
    monkeypatch.delenv("BOATRACE_DB_CONNECT_RETRIES", raising=False)
    monkeypatch.delenv("BOATRACE_DB_CONNECT_RETRY_DELAYS_SEC", raising=False)
    monkeypatch.setattr(connection, "_PG_POOL", pool)
    monkeypatch.setattr(connection.time, "sleep", sleeps.append)

    conn = connection._PgConnection("postgresql://unused")
    event = connection.consume_transient_db_retry_event()
    conn.close()

    assert pool.calls == 3
    assert sleeps == [0.2, 0.5]
    assert event["retry_count"] == 2


def test_pg_connect_retry_configuration_is_hard_capped(monkeypatch):
    monkeypatch.setenv("BOATRACE_DB_CONNECT_RETRIES", "99")
    monkeypatch.setenv("BOATRACE_DB_CONNECT_RETRY_DELAYS_SEC", "10,20,30")

    assert connection._connect_retry_delays() == (0.5, 0.5)


def test_permanent_authentication_error_is_not_retried(monkeypatch):
    class AuthenticationFailure(Exception):
        sqlstate = "28P01"

    class _PermanentPool(_FakePool):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def getconn(self):
            self.calls += 1
            raise AuthenticationFailure("password authentication failed")

    pool = _PermanentPool()
    sleeps = []
    monkeypatch.delenv("BOATRACE_TASK_TRIGGER", raising=False)
    monkeypatch.setattr(connection, "_PG_POOL", pool)
    monkeypatch.setattr(connection.time, "sleep", sleeps.append)

    try:
        connection._PgConnection("postgresql://unused")
    except AuthenticationFailure:
        pass
    else:
        raise AssertionError("permanent authentication failure must be raised")

    assert pool.calls == 1
    assert sleeps == []
    assert connection.consume_transient_db_retry_event() is None
