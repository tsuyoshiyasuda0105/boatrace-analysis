import os

os.environ.setdefault("DATABASE_URL", "")

from src.db import connection
from src.web import app as web_app


def _member_client(monkeypatch):
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("BOATRACE_TASK_TRIGGER", raising=False)
    web_app.invalidate_cache()
    app = web_app.create_app()
    app.config.update(TESTING=True, SECRET_KEY="test")
    client = app.test_client()
    with client.session_transaction() as session:
        session["is_member"] = True
        session["role"] = "admin"
    return app, client


def _snapshot():
    return {
        "version": web_app.TOP_PAGE_SNAPSHOT_VERSION,
        "date": "2026-08-15",
        "generated_at": "2026-08-15T16:00:00+09:00",
        "stadium_groups": [
            {
                "stadium_number": 1,
                "stadium_name": "桐生",
                "environment": {},
                "races": [
                    {
                        "race_id": "202608150101",
                        "race_date": "2026-08-15",
                        "race_number": 1,
                        "race_closed_at": "2026-08-15 17:00:00",
                        "stadium_number": 1,
                        "stadium_name": "桐生",
                        "results_count": 0,
                    }
                ],
            }
        ],
        "initial_market_signals": {
            "date": "2026-08-15",
            "signals": {},
            "race_badges": {},
            "accident_watch": {},
        },
        "empty": False,
    }


def test_races_returns_stale_snapshot_when_live_db_is_unavailable(monkeypatch):
    app, client = _member_client(monkeypatch)
    reads = iter([None, _snapshot()])
    monkeypatch.setattr(web_app, "_today_jst_iso", lambda: "2026-08-15")
    monkeypatch.setattr(web_app, "_read_top_page_snapshot", lambda *_args: next(reads))
    monkeypatch.setattr(
        web_app,
        "db_connect",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("pool timeout")),
    )
    noted = []
    monkeypatch.setattr(web_app, "_note_transient_db_error", lambda context, *_a, **_k: noted.append(context))
    setattr(app, "_system_status_cache", {"ts": web_app.time.time(), "warnings": []})

    response = client.get("/races?date=2026-08-15")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert response.headers["X-Boatrace-Data-Stale"] == "1"
    assert "桐生" in body
    assert "最新ではない可能性があります" in body
    assert "OR Error" not in body
    assert "races" in noted


def test_races_without_stale_snapshot_returns_calm_retry_page(monkeypatch):
    _app, client = _member_client(monkeypatch)
    monkeypatch.setattr(web_app, "_read_top_page_snapshot", lambda *_args: None)
    monkeypatch.setattr(
        web_app,
        "db_connect",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("pool timeout")),
    )
    monkeypatch.setattr(web_app, "_note_transient_db_error", lambda *_a, **_k: None)

    response = client.get("/races?date=2026-08-15")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert response.headers["Retry-After"] == "30"
    assert "ただいま混み合っています" in body
    assert '<meta http-equiv="refresh" content="30">' in body
    assert "OR Error" not in body
    assert "0: ERROR" not in body


def test_race_detail_transient_error_uses_stale_then_preparing(monkeypatch):
    _app, client = _member_client(monkeypatch)
    # Keep the fixed race IDs on the route's "today" path on every run date.
    monkeypatch.setattr(web_app, "_today_jst_iso", lambda: "2026-08-15")
    monkeypatch.setattr(
        web_app,
        "_read_page_html_cache",
        lambda *_args: (_ for _ in ()).throw(TimeoutError("pool timeout")),
    )
    monkeypatch.setattr(
        web_app,
        "_read_page_html_cache_stale",
        lambda *_args: "<html><body><main>saved detail</main></body></html>",
    )
    monkeypatch.setattr(web_app, "_note_transient_db_error", lambda *_a, **_k: None)

    stale_response = client.get("/race/20260815-01-01")

    assert stale_response.status_code == 200
    assert stale_response.headers["X-Boatrace-Data-Stale"] == "1"
    assert "saved detail" in stale_response.get_data(as_text=True)
    assert "最新ではない可能性があります" in stale_response.get_data(as_text=True)

    monkeypatch.setattr(web_app, "_read_page_html_cache_stale", lambda *_args: None)
    preparing_response = client.get("/race/20260815-01-02")

    assert preparing_response.status_code == 200
    assert preparing_response.headers["Retry-After"] == "30"
    assert "レース詳細を準備しています" in preparing_response.get_data(as_text=True)
    assert "OR Error" not in preparing_response.get_data(as_text=True)


def test_500_template_is_db_free_and_keeps_monitoring_status(monkeypatch):
    app, _client = _member_client(monkeypatch)

    @app.route("/_test/boom")
    def boom():
        raise RuntimeError("unexpected failure")

    monkeypatch.setattr(
        web_app,
        "db_connect",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("500 rendering must not query DB")
        ),
    )
    app.config["PROPAGATE_EXCEPTIONS"] = False
    response = app.test_client().get("/_test/boom")
    body = response.get_data(as_text=True)

    assert response.status_code == 500
    assert response.headers["Retry-After"] == "30"
    assert "ただいま混み合っています" in body
    assert "OR Error" not in body
    assert "0: ERROR" not in body


def test_transient_db_status_uses_existing_table_best_effort(monkeypatch):
    statements = []

    class Cursor:
        def __init__(self, row=None):
            self.row = row

        def fetchone(self):
            return self.row

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql, params=()):
            statements.append((" ".join(sql.split()), params))
            return Cursor(None)

        def commit(self):
            statements.append(("COMMIT", ()))

    monkeypatch.setattr(web_app, "_raw_db_connect", lambda **kwargs: FakeConnection())
    monkeypatch.setattr(web_app, "_today_jst_iso", lambda: "2026-08-15")

    ok = web_app._flush_transient_db_errors(
        [{"at": "2026-08-15T18:00:00+09:00", "context": "races", "error_type": "PoolTimeout", "retry_count": 2}]
    )

    assert ok is True
    assert any("INSERT INTO system_status" in sql for sql, _params in statements)
    assert not any("CREATE TABLE" in sql for sql, _params in statements)
    insert_params = next(params for sql, params in statements if "INSERT INTO system_status" in sql)
    assert insert_params[0] == web_app.TRANSIENT_DB_ERROR_CHECK
    assert '"count":1' in insert_params[4]


def test_races_renders_normally_after_one_pool_timeout_retry(monkeypatch):
    class RawConnection:
        autocommit = False

    class Pool:
        def __init__(self):
            self.calls = 0
            self.raw = RawConnection()

        def getconn(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("couldn't get a connection after 5.00 sec")
            return self.raw

        def putconn(self, _conn):
            return None

        def get_stats(self):
            return {"pool_size": 1, "pool_available": 0}

    app, client = _member_client(monkeypatch)
    pool = Pool()
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(connection, "_PG_POOL", pool)
    monkeypatch.setattr(connection.time, "sleep", lambda _delay: None)
    monkeypatch.setattr(web_app, "_read_top_page_snapshot", lambda *_args: None)
    monkeypatch.setattr(web_app, "_races_for_date", lambda *_args, **_kwargs: _snapshot()["stadium_groups"][0]["races"])
    monkeypatch.setattr(web_app, "_venue_environment_summaries_for_date", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(web_app, "_race_grid_badges_payload", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(web_app, "_write_top_page_snapshot", lambda *_args, **_kwargs: None)
    recovered = []
    monkeypatch.setattr(web_app, "_note_transient_db_error", lambda context, *_a, **_k: recovered.append(context))
    setattr(app, "_system_status_cache", {"ts": web_app.time.time(), "warnings": []})

    response = client.get("/races?date=2026-08-15")

    assert response.status_code == 200
    assert "桐生" in response.get_data(as_text=True)
    assert pool.calls == 1 + 1
    assert recovered == ["connection_retry_recovered"]
