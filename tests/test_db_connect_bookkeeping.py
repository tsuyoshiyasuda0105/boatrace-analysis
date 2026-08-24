# -*- coding: utf-8 -*-
"""接続の「ついでの記録」が失敗しても接続を失わないことの回帰テスト。

2026-08-24 実障害: db_connect() は接続を取った直後に、プール統計や一時エラーを
DB へ書き出す。この記録処理が例外を投げると接続は呼び出し元に渡らず、プールにも
戻らないまま消える。記録処理はどれも DB へ書きに行くので、DB が不調なときほど
失敗しやすく、まさにその時に worker の接続を 1 本ずつ削っていく。
"""
from src.web import app as app_module


class _Sentinel:
    pass


def test_bookkeeping_failure_never_costs_a_connection(monkeypatch):
    sentinel = _Sentinel()
    monkeypatch.setattr(app_module, "_raw_db_connect", lambda *a, **k: sentinel)
    monkeypatch.setattr(
        app_module, "consume_pg_pool_lifecycle_events", lambda: [{"event": "x"}]
    )

    def _boom(*_a, **_k):
        raise RuntimeError("system_status write failed")

    monkeypatch.setattr(app_module, "_note_pool_lifecycle_events", _boom)
    monkeypatch.setattr(app_module, "_flush_pending_transient_db_errors", _boom)

    conn = app_module.db_connect()

    assert conn is sentinel, "記録の失敗で接続が呼び出し元に渡らないと 1 本失われる"


def test_healthy_bookkeeping_still_runs(monkeypatch):
    sentinel = _Sentinel()
    seen = []
    monkeypatch.setattr(app_module, "_raw_db_connect", lambda *a, **k: sentinel)
    monkeypatch.setattr(app_module, "consume_pg_pool_lifecycle_events", lambda: [])
    monkeypatch.setattr(app_module, "consume_transient_db_retry_event", lambda: None)
    monkeypatch.setattr(
        app_module, "_flush_pending_transient_db_errors", lambda c: seen.append(c)
    )

    assert app_module.db_connect() is sentinel
    assert seen == [sentinel], "正常時は従来どおり記録する"
