import json
import time

import pytest

from src.db import connection
from src.web import app as web_app


class _RawConnection:
    autocommit = False


class _Clock:
    def __init__(self):
        self.value = 100.0

    def monotonic(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


def _reset_pool_measurements(monkeypatch):
    monkeypatch.setattr(connection, "_PG_POOL_LIFECYCLE_EVENTS", [])
    monkeypatch.setattr(connection, "_PG_POOL_ACTIVE_CHECKOUTS", 0)
    monkeypatch.setattr(connection, "_PG_POOL_PEAK_CHECKOUTS", 0)


def test_web_request_pool_checkout_retries_share_ten_second_budget(monkeypatch):
    clock = _Clock()

    class BusyPool:
        def __init__(self):
            self.timeouts = []

        def getconn(self, *, timeout):
            self.timeouts.append(timeout)
            clock.value += timeout
            raise TimeoutError("couldn't get a connection")

        def get_stats(self):
            return {"pool_size": 4, "pool_available": 0, "requests_waiting": 4}

    pool = BusyPool()
    _reset_pool_measurements(monkeypatch)
    monkeypatch.delenv("BOATRACE_TASK_TRIGGER", raising=False)
    monkeypatch.setattr(connection, "_PG_POOL", pool)
    monkeypatch.setattr(connection.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(connection.time, "sleep", clock.sleep)
    connection.begin_web_request_db_budget(10)
    try:
        with pytest.raises(connection.ConnectionCheckoutBudgetExceeded):
            connection._PgConnection("postgresql://unused")
    finally:
        connection.end_web_request_db_budget()

    assert clock.value - 100.0 <= 10.0
    assert pool.timeouts == pytest.approx([5.0, 4.8])
    events = connection.consume_pg_pool_lifecycle_events()
    assert events[-1]["event"] == "checkout_failed"
    assert events[-1]["wait_ms"] == pytest.approx(10000.0)


def test_pool_lifecycle_measures_wait_hold_and_peak_concurrency(monkeypatch):
    clock = _Clock()

    class Pool:
        def __init__(self):
            self.returned = []

        def getconn(self):
            return _RawConnection()

        def putconn(self, raw):
            self.returned.append(raw)

        def get_stats(self):
            return {"pool_size": 4, "pool_available": 2, "requests_waiting": 0}

    pool = Pool()
    _reset_pool_measurements(monkeypatch)
    monkeypatch.delenv("BOATRACE_TASK_TRIGGER", raising=False)
    monkeypatch.setattr(connection, "_PG_POOL", pool)
    monkeypatch.setattr(connection.time, "monotonic", clock.monotonic)

    first = connection._PgConnection("postgresql://unused")
    second = connection._PgConnection("postgresql://unused")
    clock.value += 2.5
    first.close()
    second.close()

    events = connection.consume_pg_pool_lifecycle_events()
    assert [event["event"] for event in events] == [
        "checkout", "checkout", "release", "release"
    ]
    assert max(int(event["peak_concurrent"]) for event in events) == 2
    assert events[-2]["hold_ms"] == pytest.approx(2500.0)
    assert len(pool.returned) == 2


def test_pool_lifecycle_measurements_persist_to_system_status(monkeypatch):
    statements = []

    class Cursor:
        def fetchone(self):
            return None

    class FakeConnection:
        def execute(self, sql, params=()):
            statements.append((" ".join(sql.split()), params))
            return Cursor()

        def commit(self):
            return None

    monkeypatch.setattr(web_app, "_today_jst_iso", lambda: "2026-08-20")
    ok = web_app._flush_pool_lifecycle_events(
        [
            {"event": "checkout", "wait_ms": 125.0, "concurrent_acquired": 3, "peak_concurrent": 3},
            {"event": "release", "hold_ms": 420.0, "concurrent_acquired": 2, "peak_concurrent": 3},
        ],
        conn=FakeConnection(),
    )

    assert ok is True
    insert_params = next(
        params for sql, params in statements if "INSERT INTO system_status" in sql
    )
    assert insert_params[0] == web_app.POOL_LIFECYCLE_CHECK
    detail = json.loads(insert_params[4])
    assert detail["max_wait_ms"] == 125.0
    assert detail["max_hold_ms"] == 420.0
    assert detail["peak_concurrent"] == 3


def test_member_today_races_pool_timeout_returns_existing_busy_response(monkeypatch):
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("BOATRACE_TASK_TRIGGER", raising=False)
    monkeypatch.setenv("DATABASE_URL", "")
    app = web_app.create_app()
    app.config.update(TESTING=True, SECRET_KEY="test")
    client = app.test_client()
    with client.session_transaction() as session:
        session["is_member"] = True
        session["role"] = "admin"
    class BusyPool:
        def __init__(self):
            self.timeouts = []

        def getconn(self, *, timeout):
            self.timeouts.append(timeout)
            raise TimeoutError("couldn't get a connection")

        def get_stats(self):
            return {"pool_size": 4, "pool_available": 0, "requests_waiting": 4}

    _reset_pool_measurements(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    pool = BusyPool()
    monkeypatch.setattr(connection, "_PG_POOL", pool)
    monkeypatch.setattr(web_app, "_note_transient_db_error", lambda *_a, **_k: None)

    started = time.perf_counter()
    response = client.get("/member/today-races?date=2026-08-20")

    assert response.status_code == 200
    assert response.headers["Retry-After"] == "30"
    assert response.headers["Cache-Control"] == "no-store, max-age=0"
    assert time.perf_counter() - started < 10.0
    assert len(pool.timeouts) <= 3


def test_startup_file_io_occurs_before_shared_connection_scope():
    source = open(web_app.__file__, encoding="utf-8").read()
    body = source.split("def _ensure_db_initialized()", 1)[1].split(
        "def create_app(", 1
    )[0]

    assert body.index("schema_path.read_text") < body.index("with db_connect() as conn")
    assert body.index("stadium_path.read_text") < body.index("with db_connect() as conn")
    assert "with open(" not in body
