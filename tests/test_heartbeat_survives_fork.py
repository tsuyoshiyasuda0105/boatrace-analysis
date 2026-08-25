# -*- coding: utf-8 -*-
"""鼓動が fork 後の worker でも動くことの回帰テスト。

2026-08-25: worker が凍る現場を押さえるために鼓動を仕込んだのに、残ったのは
親プロセスの姿だけだった。原因は「起動済み」を真偽値で覚えていたこと。
fork の子はモジュール変数を受け継ぐがスレッドは受け継がないので、worker は
「親が起動済み」という記憶だけを持って自分の鼓動を一度も打たなかった。
"""
import os
import threading

import src.db.connection as connection


def _heartbeat_thread_count() -> int:
    return sum(1 for t in threading.enumerate() if t.name == "process-heartbeat")


def test_second_call_in_the_same_process_is_a_no_op(monkeypatch):
    monkeypatch.setattr(connection, "_HEARTBEAT_OWNER_PID", 0)
    before = _heartbeat_thread_count()

    connection.start_process_heartbeat()
    connection.start_process_heartbeat()

    assert _heartbeat_thread_count() == before + 1, "同一プロセスで二重起動しない"


def test_a_forked_child_starts_its_own_heartbeat(monkeypatch):
    """親から受け継いだ「起動済み」の記憶で、子が黙り込まないこと。"""
    # 親が起動済みの状態を、別 pid が所有している形で再現する
    monkeypatch.setattr(connection, "_HEARTBEAT_OWNER_PID", os.getpid() + 1)
    before = _heartbeat_thread_count()

    connection.start_process_heartbeat()

    assert _heartbeat_thread_count() == before + 1, (
        "所有者が別プロセスなら、自分の鼓動を起動しなければならない"
    )
    assert connection._HEARTBEAT_OWNER_PID == os.getpid()


def test_start_guard_is_not_a_plain_boolean():
    """真偽値に戻すと fork の子が黙る。ソースで固定する。"""
    from pathlib import Path

    source = Path("src/db/connection.py").read_text(encoding="utf-8")
    assert "_HEARTBEAT_STARTED" not in source
    assert "_HEARTBEAT_OWNER_PID == os.getpid()" in source
