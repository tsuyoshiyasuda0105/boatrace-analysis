# -*- coding: utf-8 -*-
"""オッズ取得の結果を、成功も失敗も残すこと。

取れれば行が増え、取れなければ何も起きない作りだったので、止まっていても
気づけなかった。2026 年に 3 回起きている。この表はその沈黙を埋める。
"""
from __future__ import annotations

import sqlite3

import pytest

from src import odds_fetch_status as status


@pytest.fixture
def conn(tmp_path):
    # 素の sqlite3 接続で足りる。_kind を持たないので SQLite 側の DDL が選ばれる。
    connection = sqlite3.connect(tmp_path / "odds.db")
    try:
        yield connection
    finally:
        connection.close()


def _rows(conn):
    return conn.execute(
        f"SELECT race_id, snapshot_label, state, detail, combination_count, "
        f"attempts, last_success_at FROM {status.TABLE} ORDER BY race_id"
    ).fetchall()


# --- 結果の読み取り --------------------------------------------------------


def test_odds_that_arrived_are_recorded_as_ok():
    state, detail, count = status.outcome(
        {"race_id": "r1", "snapshot_label": "T-5min", "odds_inserted": 120}
    )
    assert (state, count) == (status.STATE_OK, 120)


@pytest.mark.parametrize("reason", ["no html", "no odds parsed"])
def test_the_collector_s_own_reason_is_kept(reason):
    """collect_one_race は理由を返していたのに、呼び出し側が捨てていた。"""
    state, detail, count = status.outcome(
        {"race_id": "r1", "odds_inserted": 0, "error": reason}
    )
    assert state == status.STATE_ERROR
    assert detail == reason
    assert count == 0


def test_zero_odds_without_a_reason_is_empty_not_an_error():
    """狙ったが 0 点。故障とは別物として数える。"""
    state, detail, _ = status.outcome({"race_id": "r1", "odds_inserted": 0})
    assert state == status.STATE_EMPTY
    assert detail


def test_an_exception_keeps_its_type_and_message():
    state, detail, _ = status.outcome(None, ValueError("締切が読めない"))
    assert state == status.STATE_ERROR
    assert "ValueError" in detail and "締切が読めない" in detail


def test_a_missing_summary_is_an_error_not_a_silent_pass():
    state, _, _ = status.outcome(None)
    assert state == status.STATE_ERROR


def test_a_very_long_message_is_trimmed():
    state, detail, _ = status.outcome(None, ValueError("x" * 5000))
    assert len(detail) <= 200


# --- 記録 ------------------------------------------------------------------


def test_a_success_and_a_failure_both_leave_a_row(conn):
    status.record(
        [
            ("r1", "T-5min", status.STATE_OK, "", 120),
            ("r2", "T-5min", status.STATE_ERROR, "no html", 0),
        ],
        conn=conn,
    )
    rows = _rows(conn)
    assert [(r[0], r[2], r[4]) for r in rows] == [
        ("r1", status.STATE_OK, 120),
        ("r2", status.STATE_ERROR, 0),
    ]


def test_trying_again_counts_up_without_losing_the_last_success(conn):
    """3 回試して 1 回も取れていない、が読み取れること。"""
    status.record([("r1", "T-5min", status.STATE_OK, "", 120)], conn=conn)
    first_success = _rows(conn)[0][6]
    assert first_success

    status.record([("r1", "T-5min", status.STATE_ERROR, "no html", 0)], conn=conn)
    status.record([("r1", "T-5min", status.STATE_ERROR, "no html", 0)], conn=conn)

    race_id, _label, state, detail, count, attempts, last_success = _rows(conn)[0]
    assert state == status.STATE_ERROR
    assert attempts == 3
    assert count == 0
    assert last_success == first_success


def test_a_later_success_moves_the_last_success_forward(conn):
    status.record([("r1", "T-5min", status.STATE_ERROR, "no html", 0)], conn=conn)
    assert _rows(conn)[0][6] is None
    status.record([("r1", "T-5min", status.STATE_OK, "", 90)], conn=conn)
    assert _rows(conn)[0][6] is not None


def test_the_same_race_at_two_snapshots_is_two_rows(conn):
    status.record(
        [
            ("r1", "T-5min", status.STATE_OK, "", 120),
            ("r1", "T-1d", status.STATE_ERROR, "no html", 0),
        ],
        conn=conn,
    )
    assert len(_rows(conn)) == 2


def test_nothing_to_record_is_not_an_error(conn):
    assert status.record([], conn=conn) == 0


def test_a_broken_database_never_stops_the_collection(tmp_path):
    """記録のために本業を止めない。書けなくても例外を外に出さない。"""
    class Broken:
        _kind = ""

        def executescript(self, sql):
            raise sqlite3.OperationalError("disk I/O error")

    assert status.record([("r1", "T-5min", status.STATE_OK, "", 1)], conn=Broken()) == 0


# --- 読み出し（朝の確認用） ------------------------------------------------


def test_only_the_ones_that_did_not_arrive_come_back(conn):
    status.record(
        [
            ("ok1", "T-5min", status.STATE_OK, "", 120),
            ("bad1", "T-5min", status.STATE_ERROR, "no html", 0),
            ("bad2", "T-5min", status.STATE_EMPTY, "no odds inserted", 0),
        ],
        conn=conn,
    )
    failures = status.recent_failures(conn=conn)
    assert {f["race_id"] for f in failures} == {"bad1", "bad2"}


def test_the_summary_counts_each_state(conn):
    status.record(
        [
            ("a", "T-5min", status.STATE_OK, "", 120),
            ("b", "T-5min", status.STATE_OK, "", 118),
            ("c", "T-5min", status.STATE_ERROR, "no html", 0),
        ],
        conn=conn,
    )
    assert status.summarize(conn=conn) == {status.STATE_OK: 2, status.STATE_ERROR: 1}


def test_an_empty_table_reads_as_nothing_not_as_a_crash(conn):
    assert status.summarize(conn=conn) == {}
    assert status.recent_failures(conn=conn) == []


def test_older_entries_fall_outside_the_window(conn):
    status.record([("old", "T-5min", status.STATE_ERROR, "no html", 0)], conn=conn)
    conn.execute(
        f"UPDATE {status.TABLE} SET checked_at = '2020-01-01T00:00:00+00:00'"
    )
    conn.commit()
    assert status.recent_failures(hours=24, conn=conn) == []
    assert status.summarize(hours=24, conn=conn) == {}


# --- 取得の流れに入っていること --------------------------------------------


def test_the_pass_records_every_race_it_aimed_at(monkeypatch, tmp_path):
    """成功も失敗も 1 行ずつ。以前は失敗が verbose のときしか出なかった。"""
    from scripts import odds_scheduler

    monkeypatch.setattr(
        odds_scheduler, "find_due_snapshots",
        lambda now, lookahead_min=30: [("ok1", "T-5min"), ("bad1", "T-5min"), ("boom", "T-5min")],
    )

    def fake_collect(race_id, snapshot_label):
        if race_id == "ok1":
            return {"race_id": race_id, "snapshot_label": snapshot_label, "odds_inserted": 120}
        if race_id == "bad1":
            return {"race_id": race_id, "snapshot_label": snapshot_label,
                    "odds_inserted": 0, "error": "no html"}
        raise RuntimeError("接続できません")

    monkeypatch.setattr(odds_scheduler, "collect_one_race", fake_collect)
    captured: list = []
    monkeypatch.setattr(
        odds_scheduler.odds_fetch_status, "record",
        lambda rows, **kw: captured.extend(rows) or len(rows),
    )

    summary = odds_scheduler.run_one_pass()

    assert summary["n_due"] == 3
    assert summary["n_done"] == 1
    assert summary["n_failed"] == 2
    assert {row[0] for row in captured} == {"ok1", "bad1", "boom"}
    states = {row[0]: row[2] for row in captured}
    assert states == {
        "ok1": status.STATE_OK,
        "bad1": status.STATE_ERROR,
        "boom": status.STATE_ERROR,
    }
    assert "接続できません" in dict((r[0], r[3]) for r in captured)["boom"]


def test_a_pass_that_aimed_at_nothing_records_nothing(monkeypatch):
    from scripts import odds_scheduler

    monkeypatch.setattr(
        odds_scheduler, "find_due_snapshots", lambda now, lookahead_min=30: []
    )
    calls: list = []
    monkeypatch.setattr(
        odds_scheduler.odds_fetch_status, "record",
        lambda rows, **kw: calls.append(rows) or 0,
    )

    summary = odds_scheduler.run_one_pass()

    assert summary["n_due"] == 0
    assert summary["n_failed"] == 0
    assert calls == [[]]


def test_every_connection_avoids_the_shared_pool(monkeypatch):
    """監視のために本番を重くしない。必ず直結で繋ぐ。

    2026-09-10 に、この確認をプール経由で走らせて枠 (15) が満杯になった。
    """
    from src import odds_fetch_status

    seen: list[bool] = []

    class Fake:
        _kind = ""

        def executescript(self, sql): pass
        def executemany(self, sql, rows): pass
        def execute(self, sql, params=None): return self
        def fetchall(self): return []
        def commit(self): pass
        def close(self): pass

    def fake_connect(db_path=None, direct=False):
        seen.append(direct)
        return Fake()

    monkeypatch.setattr(odds_fetch_status, "db_connect", fake_connect)
    odds_fetch_status.record([("r1", "T-5min", status.STATE_OK, "", 1)])
    odds_fetch_status.summarize()
    odds_fetch_status.recent_failures()

    assert seen == [True, True, True]
