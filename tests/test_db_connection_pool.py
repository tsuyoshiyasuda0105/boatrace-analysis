import os

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
    monkeypatch.setattr(connection, "_PG_POOL_OWNER_PID", os.getpid())

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
    # 上限はスレッド数の 2 倍 (入れ子接続の余地)。この不変条件は
    # tests/test_db_pool_warmth.py が render.yaml の --threads と突き合わせる。
    assert 'default_pool_size = "1" if trigger else "6"' in source
    # min_size (常時確保) は 4。2026-08-24 に 8 へ上げたら 2 worker x 8 = 16 本が
    # Supabase 側のクライアント枠を超え、片方の worker が 1 本も取れないまま
    # 固まった (pool_available=0 が復帰しない)。worker 数を掛けて収まる値にする。
    assert "default_min_size = 0 if trigger else 3" in source
    assert 'os.getenv("BOATRACE_DB_POOL_MIN_SIZE", str(default_min_size))' in source
    assert 'os.getenv("BOATRACE_DB_POOL_SIZE", default_pool_size)' in source
    assert 'os.getenv("BOATRACE_DB_POOL_TIMEOUT_SEC", "5")' in source


def test_wait_queue_limit_is_still_configurable(monkeypatch):
    """既定は無制限だが、env で明示すれば上限を掛けられること。

    2026-08-24 に既定を「max_size と同数」から 0 (無制限) に変えた。
    psycopg_pool の待ち件数カウンタが減らずに張り付き、上限に達した時点で
    永久に取得できなくなったため。上限が要るときのために口は残す。
    """
def test_web_pool_has_finite_wait_queue_matching_configured_max(monkeypatch):
    import psycopg_pool

    captured = {}

    class _CapturingPool:
        check_connection = object()

        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.delenv("BOATRACE_TASK_TRIGGER", raising=False)
    monkeypatch.setenv("BOATRACE_DB_POOL_SIZE", "8")
    monkeypatch.setenv("BOATRACE_DB_POOL_MAX_WAITING", "8")
    monkeypatch.setattr(connection, "_PG_POOL", None)
    monkeypatch.setattr(psycopg_pool, "ConnectionPool", _CapturingPool)

    connection._get_pg_pool("postgresql://unused")

    assert captured["max_size"] == 8
    assert captured["max_waiting"] == 8


def test_web_pool_keeps_its_wait_queue_unbounded(monkeypatch):
    """待ち行列に上限を置かない。

    2026-08-24 実障害: psycopg_pool の requests_waiting は一度増えると減らない
    ことがあり、スレッド 3 本の worker で 6 (=上限) に張り付いた。上限に達した
    瞬間から取得はすべて TooManyRequests で即座に弾かれ、待っても直らない。
    再起動直後から 10 ページ連続で仮ページに落ちた。上限を外しても待ち手は
    各自 5 秒でタイムアウトするので行列は伸び続けない。
    """

    import psycopg_pool

    captured = {}

    class _CapturingPool:
        check_connection = object()

        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.delenv("BOATRACE_TASK_TRIGGER", raising=False)
    monkeypatch.delenv("BOATRACE_DB_POOL_MAX_WAITING", raising=False)
    monkeypatch.setattr(connection, "_PG_POOL", None)
    monkeypatch.setattr(psycopg_pool, "ConnectionPool", _CapturingPool)

    connection._get_pg_pool("postgresql://unused")

    assert captured["max_waiting"] == 0


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
    # 作り直しは 2026-08-26 に既定オフ (番犬が健全なプールを壊した)。
    # この 2 つは有効化した時の作法を確かめるテストなので明示的に有効化する。
    monkeypatch.setenv("BOATRACE_DB_POOL_REBUILD", "1")
    pool = _ClosablePool()
    stats = {"pool_available": 0, "requests_waiting": 8}
    monkeypatch.delenv("BOATRACE_TASK_TRIGGER", raising=False)
    monkeypatch.setattr(connection, "_PG_POOL", pool)
    monkeypatch.setattr(connection, "_PG_POOL_OWNER_PID", os.getpid())
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
    monkeypatch.setattr(connection, "_PG_POOL_OWNER_PID", os.getpid())
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
    # 作り直しは 2026-08-26 に既定オフ (番犬が健全なプールを壊した)。
    # この 2 つは有効化した時の作法を確かめるテストなので明示的に有効化する。
    monkeypatch.setenv("BOATRACE_DB_POOL_REBUILD", "1")
    pool = _ClosablePool()
    stats = {"pool_available": 0, "requests_waiting": 4}
    monkeypatch.delenv("BOATRACE_TASK_TRIGGER", raising=False)
    monkeypatch.setattr(connection, "_PG_POOL", pool)
    monkeypatch.setattr(connection, "_PG_POOL_OWNER_PID", os.getpid())
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
    monkeypatch.setattr(connection, "_PG_POOL_OWNER_PID", os.getpid())
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
    monkeypatch.setattr(connection, "_PG_POOL_OWNER_PID", os.getpid())

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
    monkeypatch.setattr(connection, "_PG_POOL_OWNER_PID", os.getpid())
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
    monkeypatch.setattr(connection, "_PG_POOL_OWNER_PID", os.getpid())
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


def test_pool_bounds_how_long_establishing_a_connection_may_take(monkeypatch):
    """プールの接続作成にも制限時間を掛ける。

    2026-08-24 実障害: プールの `timeout` は「空き接続を待つ時間」であって
    「接続を張る時間」ではない。conninfo に connect_timeout が無かったため、
    Render(シンガポール) から Supabase(東京) への TCP/TLS が応答を返さない間、
    接続作成が無期限に刺さってプールが永久に空のままになり、Web だけが数時間
    DB を掴めずレース詳細が「準備しています」に落ち続けた。cron は直接接続で
    connect_timeout があったため無事で、それが切り分けを難しくした。
    """
    import psycopg_pool

    captured = {}

    class _CapturingPool:
        check_connection = object()

        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.delenv("BOATRACE_TASK_TRIGGER", raising=False)
    monkeypatch.setenv("BOATRACE_DB_CONNECT_TIMEOUT_SEC", "7")
    monkeypatch.setattr(connection, "_PG_POOL", None)
    monkeypatch.setattr(psycopg_pool, "ConnectionPool", _CapturingPool)

    connection._get_pg_pool("postgresql://unused")

    assert captured["kwargs"]["connect_timeout"] == 7, (
        "接続の確立に制限時間が無いとプールは永久に空のまま復帰しない"
    )


def test_connect_timeout_falls_back_to_a_sane_default(monkeypatch):
    monkeypatch.setenv("BOATRACE_DB_CONNECT_TIMEOUT_SEC", "not-a-number")
    assert connection._pg_connect_timeout_seconds() == 5
    monkeypatch.setenv("BOATRACE_DB_CONNECT_TIMEOUT_SEC", "0")
    assert connection._pg_connect_timeout_seconds() >= 1


def test_pool_enables_socket_keepalives(monkeypatch):
    """黙って切られた接続を OS に検知させる (永久に待たせない)。"""
    import psycopg_pool

    captured = {}

    class _CapturingPool:
        check_connection = object()

        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.delenv("BOATRACE_TASK_TRIGGER", raising=False)
    monkeypatch.setattr(connection, "_PG_POOL", None)
    monkeypatch.setattr(psycopg_pool, "ConnectionPool", _CapturingPool)

    connection._get_pg_pool("postgresql://unused")

    kwargs = captured["kwargs"]
    assert kwargs["keepalives"] == 1
    assert kwargs["keepalives_idle"] > 0
    assert kwargs["keepalives_interval"] > 0
    assert kwargs["keepalives_count"] > 0


def test_pool_report_is_read_only(monkeypatch):
    """調査用の報告は環境変数もプールも書き換えない。

    2026-08-24: 以前の調査用エンドポイントは os.environ["BOATRACE_TASK_TRIGGER"]
    を一時的に書き換えて pooled と direct を比べていた。環境変数は worker 内の
    全スレッドに効くので、調査中の無関係なリクエストまで接続の取り方が変わる。
    調査の道具が本番を壊しうる状態だったため撤去した。二度と入れないよう縛る。
    """
    import os

    before = dict(os.environ)
    monkeypatch.setattr(connection, "_PG_POOL", None)

    report = connection.pg_pool_report()

    assert dict(os.environ) == before, "報告のために環境変数を触ってはいけない"
    assert connection._PG_POOL is None, "報告のためにプールを作ってはいけない"
    assert report["pool_exists"] is False
    assert "pid" in report, "どの worker が答えたか分からないと片肺状態を掴めない"


def test_dangerous_env_mutating_probe_is_gone():
    from pathlib import Path

    source = Path("src/web/kachisuji_bp.py").read_text(encoding="utf-8")
    assert 'os.environ["BOATRACE_TASK_TRIGGER"] = "diagnostic-probe"' not in source


class _DyingCheckedOutConn:
    """autocommit を立てようとすると壊れる接続 (取得直後に死ぬ接続の再現)。"""

    def __init__(self):
        self.closed = False

    def __setattr__(self, name, value):
        if name == "autocommit":
            raise RuntimeError("connection died right after checkout")
        object.__setattr__(self, name, value)


class _ReturnTrackingPool:
    def __init__(self):
        self.returned = []
        self._conn = _DyingCheckedOutConn()

    def getconn(self, timeout=None):
        return self._conn

    def putconn(self, conn):
        self.returned.append(conn)

    def get_stats(self):
        return {}


def test_connection_is_returned_when_setup_fails_after_checkout(monkeypatch):
    """取得直後の初期化で落ちても、接続は必ずプールへ返す。

    2026-08-24 実障害: 貸し出された接続が呼び出し元に渡らないまま参照を失うと、
    psycopg_pool は GC で回収しないのでその 1 本は永久に失われる。1 本ずつ
    積み上がった結果 pid 82 が pool_size=9 / available=0 で固まり、リクエストの
    約半分が 10 秒待って「準備しています」に落ちた。
    """
    fake = _ReturnTrackingPool()
    monkeypatch.delenv("BOATRACE_TASK_TRIGGER", raising=False)
    monkeypatch.setattr(connection, "_get_pg_pool", lambda dsn: fake)

    try:
        connection._PgConnection("postgresql://unused")
    except RuntimeError:
        pass
    else:  # pragma: no cover - 失敗が起きない実装なら設計が変わっている
        raise AssertionError("初期化の失敗が伝わっていない")

    assert fake.returned == [fake._conn], "接続を返さずに例外を投げると 1 本失われる"


def test_forgotten_connection_is_returned_when_garbage_collected(monkeypatch):
    """close() を通らない経路が残っていても、参照が消えたら必ず返す。

    2026-08-24 実障害: psycopg_pool は貸し出した接続を GC で回収しない。
    どこか 1 箇所でも返し忘れがあると 1 本ずつ永久に失われ、worker が
    pool_available=0 のまま復帰しなくなる。経路を塞ぐのとは別に、最後の砦を置く。
    """
    import gc

    class _Conn:
        autocommit = False

    class _Pool:
        def __init__(self):
            self.returned = []

        def getconn(self, timeout=None):
            return _Conn()

        def putconn(self, conn):
            self.returned.append(conn)

        def get_stats(self):
            return {}

    pool = _Pool()
    monkeypatch.delenv("BOATRACE_TASK_TRIGGER", raising=False)
    monkeypatch.setattr(connection, "_get_pg_pool", lambda dsn: pool)

    connection._PgConnection("postgresql://unused")  # 参照を持たずに捨てる
    gc.collect()

    assert len(pool.returned) == 1, "参照が消えた接続がプールに戻っていない"
