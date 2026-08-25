# -*- coding: utf-8 -*-
"""プールの世話でリクエストスレッドを待たせないことの回帰テスト。

2026-08-25 19:12 実障害。ブラックボックスが worker の全リクエストスレッド
(--threads 2 の 2 本とも) を `with _PG_POOL_LOCK:` で捕らえていた。プール
再構築がロックを握ったまま pool.close() を呼び、close() はプールの worker
スレッド終了を待つため何十秒も戻らない。その間リクエストスレッドが枯渇し、
DB を触らないはずの /healthz すら応答できず Render にインスタンスごと
処刑された (この日 10 回以上)。
"""
import threading

import src.db.connection as connection


class _FakePool:
    def __init__(self):
        self.closed = threading.Event()

    def get_stats(self):
        return {"pool_available": 0, "requests_waiting": 3}

    def close(self):
        self.closed.set()


def test_watchdog_gives_up_instead_of_waiting_for_the_lock(monkeypatch):
    pool = _FakePool()
    monkeypatch.setattr(connection, "_PG_POOL", pool)
    connection._PG_POOL_LOCK.acquire()  # 別スレッドが世話中を再現
    try:
        done = threading.Event()

        def _call():
            connection._maybe_rebuild_exhausted_pg_pool(pool, pool.get_stats())
            done.set()

        threading.Thread(target=_call, daemon=True).start()
        assert done.wait(timeout=3), "ロック待ちでリクエストスレッドが縛られている"
    finally:
        connection._PG_POOL_LOCK.release()


def test_checkout_success_bookkeeping_never_waits(monkeypatch):
    pool = _FakePool()
    monkeypatch.setattr(connection, "_PG_POOL", pool)
    connection._PG_POOL_LOCK.acquire()
    try:
        done = threading.Event()

        def _call():
            connection._note_pg_pool_checkout_success(pool)
            done.set()

        threading.Thread(target=_call, daemon=True).start()
        assert done.wait(timeout=3), "成功のたびの帳簿付けで待たされている"
    finally:
        connection._PG_POOL_LOCK.release()


def test_retired_pool_is_closed_outside_the_lock():
    """close() をロック内で呼ぶと、その間ほかの全員が待たされる。"""
    from pathlib import Path

    source = Path("src/db/connection.py").read_text(encoding="utf-8")
    body = source[source.index("def _maybe_rebuild_exhausted_pg_pool"):]
    body = body[: body.index("def _close_retired_pool")]
    assert "pool.close()" not in body, "retire したプールはロックの外で閉じる"
    assert "_close_retired_pool" in body
