# -*- coding: utf-8 -*-
"""接続プールが fork をまたいで共有されないことの回帰テスト。

2026-08-26 に判明した、今週の不安定さの根本原因。gunicorn は親でアプリを
読み込んでから worker を fork するため、親で作られた ConnectionPool が
そのまま子に渡る。プールの背景スレッドは fork を越えないので、接続を新しく
張る者が誰もいないまま available が 0 に落ち、さらに親と子が同じ TCP 接続を
共有することになる。

決定的な証拠: 別 pid の 2 worker が connections_num=3 / connections_ms=1282 と
一字一句同じ統計を報告していた。独立したプールならあり得ない。
"""
import os

import src.db.connection as connection


class _FakePool:
    def __init__(self, **kwargs):
        self.closed = False

    def close(self):
        self.closed = True

    def get_stats(self):
        return {}


def _install_fake_pool(monkeypatch):
    import psycopg_pool

    created = []

    class _Capturing(_FakePool):
        check_connection = object()

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            created.append(self)

    monkeypatch.setattr(psycopg_pool, "ConnectionPool", _Capturing)
    monkeypatch.delenv("BOATRACE_TASK_TRIGGER", raising=False)
    return created


def test_same_process_reuses_the_pool(monkeypatch):
    created = _install_fake_pool(monkeypatch)
    monkeypatch.setattr(connection, "_PG_POOL", None)
    monkeypatch.setattr(connection, "_PG_POOL_OWNER_PID", 0)

    first = connection._get_pg_pool("postgresql://unused")
    second = connection._get_pg_pool("postgresql://unused")

    assert first is second
    assert len(created) == 1


def test_inherited_pool_is_replaced_not_reused(monkeypatch):
    """別プロセスが作ったプールを受け継いだら、自分のものを作り直す。"""
    created = _install_fake_pool(monkeypatch)
    inherited = _FakePool()
    monkeypatch.setattr(connection, "_PG_POOL", inherited)
    monkeypatch.setattr(connection, "_PG_POOL_OWNER_PID", os.getpid() + 1)

    pool = connection._get_pg_pool("postgresql://unused")

    assert pool is not inherited, "親のプールを使い続けると接続を奪い合う"
    assert len(created) == 1
    assert connection._PG_POOL_OWNER_PID == os.getpid()


def test_inherited_pool_is_not_closed(monkeypatch):
    """受け継いだプールは閉じない (親と共有する fd に protocol を流さない)。"""
    _install_fake_pool(monkeypatch)
    inherited = _FakePool()
    monkeypatch.setattr(connection, "_PG_POOL", inherited)
    monkeypatch.setattr(connection, "_PG_POOL_OWNER_PID", os.getpid() + 1)

    connection._get_pg_pool("postgresql://unused")

    assert not inherited.closed
