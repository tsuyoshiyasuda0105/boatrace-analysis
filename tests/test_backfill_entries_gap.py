# -*- coding: utf-8 -*-
"""race_entries が丸ごと欠けた事故 (2026-08-29 発覚) を固定する回帰テスト。

実害: バックテスト検索で福岡2016-24・多摩川2016-20 の選手が「不能判定」に
落ち、約 3.1 万レースぶんの race_entries が空だった。原因は
scripts/backfill_official.py の --skip-existing 判定が **結果(K)の件数しか
見ていなかった**こと。結果だけ先に入り選手情報(B)が未取得の日を「もう十分」と
誤判定して以後ずっと素通りしていた。修正後は取得対象ごとに揃っているかを見る:
b を取るなら race_entries、k を取るなら race_results を要件にする。
"""
import sqlite3
from datetime import date

import scripts.backfill_official as bf


def _seed(conn: sqlite3.Connection, *, with_entries: bool) -> None:
    conn.execute("CREATE TABLE races (race_id TEXT PRIMARY KEY, race_date TEXT)")
    conn.execute("CREATE TABLE race_results (race_id TEXT)")
    conn.execute("CREATE TABLE race_entries (race_id TEXT)")
    d = date(2018, 6, 2).isoformat()
    # 1 日ぶん (>200 の結果) を用意。選手情報は with_entries でだけ入れる。
    for i in range(240):
        rid = f"20180602-fukuoka-{i:04d}"
        conn.execute("INSERT INTO races VALUES (?, ?)", (rid, d))
        conn.execute("INSERT INTO race_results VALUES (?)", (rid,))
        if with_entries:
            conn.execute("INSERT INTO race_entries VALUES (?)", (rid,))
    conn.commit()


def _run(conn, monkeypatch, targets):
    # ネットワークと解凍を止める。fetch_one が None を返せば process_day は
    # 「スキップ判定」だけを通り、B/K の投入はしない。
    monkeypatch.setattr(bf.official_dl, "fetch_one", lambda *a, **k: None)

    class _NullLog:
        def exception(self, *a, **k):
            pass

    return bf.process_day(
        date(2018, 6, 2), conn, _NullLog(),
        skip_existing=True, targets=targets,
    )


def test_results_present_but_entries_missing_is_not_skipped(monkeypatch):
    """結果だけ入って選手情報が空の日を「済み」と誤判定しない (バグ本体)。"""
    conn = sqlite3.connect(":memory:")
    _seed(conn, with_entries=False)
    summary = _run(conn, monkeypatch, targets="b")
    assert summary["skipped"] is False, (
        "結果だけで選手情報が無い日をスキップすると race_entries が永久に欠ける"
    )


def test_both_present_is_skipped(monkeypatch):
    """結果も選手情報も揃っていれば従来どおりスキップする (無駄な再取得を防ぐ)。"""
    conn = sqlite3.connect(":memory:")
    _seed(conn, with_entries=True)
    summary = _run(conn, monkeypatch, targets="b")
    assert summary["skipped"] is True


def test_targets_b_leaves_results_untouched(monkeypatch):
    """--targets b では K(結果)の取得を行わない (再DLを省いて半減させる狙い)。"""
    conn = sqlite3.connect(":memory:")
    _seed(conn, with_entries=False)
    summary = _run(conn, monkeypatch, targets="b")
    assert summary["k_results"] == 0


def test_one_missing_stadium_on_a_multi_stadium_day_is_not_skipped(monkeypatch):
    """1日に複数会場ある日で、ある1会場だけ選手情報が欠けていても処理する。

    2026-08-29 第2次修正で塞いだ本体バグ。当初は「その日の entries 合計 > 200」で
    スキップ判定していたため、福岡が丸ごと欠けていても他会場ぶんで合計が 200 を超え、
    福岡が永久に埋まらなかった。合計ではなく「欠けたレースが1件でもあるか」で見る。
    """
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE races (race_id TEXT PRIMARY KEY, race_date TEXT)")
    conn.execute("CREATE TABLE race_results (race_id TEXT)")
    conn.execute("CREATE TABLE race_entries (race_id TEXT)")
    d = date(2018, 6, 2).isoformat()
    # 会場5 (多摩川): 結果も選手情報も揃っている。240 > 200 なので合計判定だと
    # この1会場だけで「済み」に見えてしまう。
    for i in range(240):
        rid = f"20180602-05-{i:04d}"
        conn.execute("INSERT INTO races VALUES (?, ?)", (rid, d))
        conn.execute("INSERT INTO race_results VALUES (?)", (rid,))
        conn.execute("INSERT INTO race_entries VALUES (?)", (rid,))
    # 会場22 (福岡): 結果はあるが選手情報が丸ごと欠けている。
    for i in range(12):
        rid = f"20180602-22-{i:04d}"
        conn.execute("INSERT INTO races VALUES (?, ?)", (rid, d))
        conn.execute("INSERT INTO race_results VALUES (?)", (rid,))
    conn.commit()

    summary = _run(conn, monkeypatch, targets="b")
    assert summary["skipped"] is False, (
        "他会場が揃っていても、欠けた会場が残る日はスキップしてはいけない"
    )
