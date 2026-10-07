"""バックテスト検索の同時実行の制限（最小構成・2026-10-07）。"""
import threading
import time

import pytest

from src.search import roi_search as rs


def test_without_the_setting_searches_are_not_limited(monkeypatch):
    monkeypatch.delenv(rs.SEARCH_CONCURRENCY_ENV, raising=False)
    monkeypatch.setattr(rs, "_search_roi", lambda *a, **k: {"ok": True})
    assert rs.search_roi("db", {}) == {"ok": True}
    assert rs._slots() is None


def test_with_one_slot_the_second_search_waits_for_the_first(monkeypatch):
    monkeypatch.setenv(rs.SEARCH_CONCURRENCY_ENV, "1")
    running = []
    peak = []

    def slow(*a, **k):
        running.append(1)
        peak.append(len(running))
        time.sleep(0.15)
        running.pop()
        return {"ok": True}

    monkeypatch.setattr(rs, "_search_roi", slow)
    threads = [threading.Thread(target=rs.search_roi, args=("db", {})) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert max(peak) == 1


def test_a_search_that_waits_too_long_gets_a_busy_message(monkeypatch):
    monkeypatch.setenv(rs.SEARCH_CONCURRENCY_ENV, "1")
    monkeypatch.setattr(rs, "SEARCH_WAIT_SECONDS", 0.05)
    monkeypatch.setattr(rs, "_search_roi", lambda *a, **k: {"ok": True})
    slots = rs._slots()
    slots.acquire()
    try:
        with pytest.raises(ValueError, match="^ただいま検索が混み合っています"):
            rs.search_roi("db", {})
    finally:
        slots.release()


def test_the_busy_message_reaches_the_member_as_is():
    from src.web.kachisuji_bp import _user_validation_message

    assert _user_validation_message(ValueError(rs.BUSY_MESSAGE)) == rs.BUSY_MESSAGE


def test_a_failing_search_frees_its_slot(monkeypatch):
    monkeypatch.setenv(rs.SEARCH_CONCURRENCY_ENV, "1")

    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(rs, "_search_roi", boom)
    with pytest.raises(RuntimeError):
        rs.search_roi("db", {})
    monkeypatch.setattr(rs, "_search_roi", lambda *a, **k: {"ok": True})
    assert rs.search_roi("db", {}) == {"ok": True}
