"""サーバー側アクセス計測の回帰テスト。

守るべき性質は3つ:
  1. リクエスト処理中に DB へ触らない（接続プール枯渇を自分で起こさない）
  2. 計測が失敗してもページを壊さない
  3. 複数ワーカーの数が合算される（上書きで消えない）
"""
from __future__ import annotations

import time

import pytest

from src.access_stats import SOURCE_CLOUDFLARE, SOURCE_SERVER, add_page_views, load_daily_access
from src.db.connection import connect as db_connect
from src.web import app as web_app
from src.web.access_counter import AccessCounter


@pytest.fixture
def conn(tmp_path):
    connection = db_connect(str(tmp_path / "access-counter.db"))
    try:
        yield connection
    finally:
        connection.close()


# ---- 何を数え、何を数えないか ----

@pytest.mark.parametrize("path", ["/", "/guide", "/races", "/legal/terms"])
def test_counts_real_pages(path):
    assert AccessCounter.should_count(path, "GET", 200, "Mozilla/5.0 (Windows NT 10.0)")


@pytest.mark.parametrize("path", [
    "/static/style.css", "/api/market-signals", "/healthz",
    "/favicon.ico", "/robots.txt",
])
def test_ignores_non_pages(path):
    assert not AccessCounter.should_count(path, "GET", 200, "Mozilla/5.0")


@pytest.mark.parametrize("ua", [
    "Googlebot/2.1", "Mozilla/5.0 (compatible; bingbot/2.0)",
    "python-requests/2.31", "curl/8.0", "HeadlessChrome/120", "",
])
def test_ignores_machines(ua):
    assert not AccessCounter.should_count("/", "GET", 200, ua)


def test_ignores_failed_responses():
    """404 や 500 を「見られたページ」に数えない。"""
    assert not AccessCounter.should_count("/", "GET", 404, "Mozilla/5.0")
    assert not AccessCounter.should_count("/", "GET", 500, "Mozilla/5.0")


# ---- 溜めて持ち寄る仕組み ----

def test_take_empties_the_counter():
    c = AccessCounter(flush_interval=0)
    for _ in range(3):
        c.record("/", "GET", 200, "Mozilla/5.0")
    first = c.take()
    assert sum(first.values()) == 3
    assert c.take() == {}, "取り出した分が二重に計上されてはいけない"


def test_failed_flush_returns_the_counts(monkeypatch):
    """DB が落ちている間の数を捨てない。次回に持ち越す。"""
    c = AccessCounter(flush_interval=0)
    c.record("/", "GET", 200, "Mozilla/5.0")

    def boom(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr("src.db.connection.connect", boom)
    assert c.flush() == 0
    assert sum(c.take().values()) == 1, "失敗した分が消えている"


def test_add_page_views_accumulates(conn):
    """gunicorn の 2 ワーカーが別々に持ち寄っても合算されること。"""
    add_page_views(conn, {"2026-09-09": 5})
    add_page_views(conn, {"2026-09-09": 3})

    rows = load_daily_access(conn, start="2026-09-09", end="2026-09-09", source=SOURCE_SERVER)
    assert rows[0]["views"] == 8, "上書きされて片方のワーカー分が消えている"


def test_server_counts_do_not_touch_cloudflare_rows(conn):
    """同じ日を両方が持つので、source を分けて互いを壊さないこと。"""
    from src.access_stats import record_daily_access

    record_daily_access(
        conn, [{"date": "2026-09-09", "page_views": 70, "visits": 0}],
        window_start="2026-09-09", window_end="2026-09-09",
    )
    add_page_views(conn, {"2026-09-09": 12})

    cf = load_daily_access(conn, start="2026-09-09", end="2026-09-09", source=SOURCE_CLOUDFLARE)
    sv = load_daily_access(conn, start="2026-09-09", end="2026-09-09", source=SOURCE_SERVER)
    assert cf[0]["views"] == 70
    assert sv[0]["views"] == 12


def test_add_page_views_skips_empty_days(conn):
    assert add_page_views(conn, {"2026-09-09": 0, "": 5}) == 0


# ---- アプリに取り付けた状態 ----

@pytest.fixture
def app(monkeypatch):
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("BOATRACE_DISABLE_ACCESS_COUNTER", raising=False)
    monkeypatch.setattr(web_app, "_ensure_db_initialized", lambda: None)
    web_app.invalidate_cache()
    application = web_app.create_app(cached_predictions_only=True)
    application.config.update(TESTING=True, SECRET_KEY="counter-test")
    application._system_status_cache = {"ts": time.time(), "warnings": []}
    return application


def test_request_does_not_write_to_the_database(app, monkeypatch):
    """リクエスト中に DB を掴むと、既存の接続予算を食って本番が詰まる。"""
    from src.web import access_counter

    calls = []
    monkeypatch.setattr(access_counter.AccessCounter, "flush",
                        lambda self: calls.append("flush"))

    app.test_client().get("/guide")
    assert calls == [], "リクエストの中で書き出しが走っている"


def test_page_still_renders_when_counting_explodes(app, monkeypatch):
    """計測が壊れてもページは返る。"""
    from src.web import access_counter

    def boom(*_a, **_k):
        raise RuntimeError("counter broken")

    monkeypatch.setattr(access_counter.counter, "record", boom)
    assert app.test_client().get("/guide").status_code == 200


# ---- 外から状態を見られること (2026-09-17: 本番で台帳が空のまま原因が見えなかった) ----

def test_status_reports_recorded_and_pending():
    c = AccessCounter(flush_interval=0)
    c.record("/", "GET", 200, "Mozilla/5.0 (Windows NT 10.0)")
    c.record("/", "GET", 200, "Googlebot/2.1")
    s = c.status()
    assert s["recorded"] == 1 and s["pending"] == 1
    assert s["last_error"] is None and s["started"] is False


def test_failed_flush_is_visible_without_credentials(monkeypatch):
    import src.db.connection as dbc

    def boom(*a, **k):
        raise RuntimeError("could not connect to postgresql://user:secret@db.example:5432/app")

    monkeypatch.setattr(dbc, "connect", boom)
    c = AccessCounter(flush_interval=0)
    c.record("/", "GET", 200, "Mozilla/5.0 (Windows NT 10.0)")
    assert c.flush() == 0
    s = c.status()
    assert s["pending"] == 1                      # 次回に持ち越し
    assert "RuntimeError" in s["last_error"]
    assert "secret" not in s["last_error"] and "***@" in s["last_error"]
    assert s["last_error_at"]


def test_healthz_exposes_counter_status(app):
    body = app.test_client().get("/healthz").get_json()
    assert "access_counter" in body
    assert {"recorded", "pending", "last_error", "installed"} <= set(body["access_counter"])


# ---- 書き込み係が止まっていても自分で起き直す (2026-09-17 本番: timer_alive=false・ticks=0) ----

def test_dead_writer_is_restarted_on_next_page_view():
    import os as _os

    c = AccessCounter(flush_interval=3600)
    c._started = True            # 親プロセスで開始済みの印だけが残った状態
    c._timer = None
    c._timer_pid = _os.getpid() + 999
    assert c.status()["timer_alive"] is False
    try:
        c.record("/", "GET", 200, "Mozilla/5.0 (Windows NT 10.0)")
        s = c.status()
        assert s["timer_alive"] is True and s["timer_pid"] == _os.getpid()
    finally:
        if c._timer:
            c._timer.cancel()


def test_writer_is_not_started_twice():
    c = AccessCounter(flush_interval=3600)
    c.start()
    try:
        first = c._timer
        for _ in range(5):
            c.record("/", "GET", 200, "Mozilla/5.0 (Windows NT 10.0)")
        assert c._timer is first
    finally:
        if c._timer:
            c._timer.cancel()


def test_tick_flushes_and_keeps_running(monkeypatch):
    c = AccessCounter(flush_interval=3600)
    calls = []
    monkeypatch.setattr(c, "flush", lambda: calls.append(1) or 0)
    c._started = True
    try:
        c._tick()
        assert calls == [1] and c.status()["ticks"] == 1 and c.status()["timer_alive"] is True
    finally:
        if c._timer:
            c._timer.cancel()


def test_child_after_fork_starts_clean():
    c = AccessCounter(flush_interval=3600)
    c.record("/", "GET", 200, "Mozilla/5.0 (Windows NT 10.0)")
    c._after_fork_in_child()
    s = c.status()
    assert s["pending"] == 0 and s["timer_pid"] is None
