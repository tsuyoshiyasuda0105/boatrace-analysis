# -*- coding: utf-8 -*-
"""P0-3 タスク6: パーツ交換情報の破壊的 DELETE 防止の回帰テスト。

HTML 構造変化等でパース結果が空になったとき、既存の race_parts を
DELETE で消して空にしない (再取得ループと データ喪失の防止)。
"""
import sqlite3

from src.collectors.beforeinfo import _upsert_parts


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE race_parts (
            race_id TEXT,
            boat_number INTEGER,
            part_code TEXT,
            PRIMARY KEY (race_id, boat_number, part_code)
        )
        """
    )
    return conn


def test_empty_parse_keeps_existing_rows():
    conn = _conn()
    conn.execute("INSERT INTO race_parts VALUES ('20260812-01-01', 1, 'P')")
    conn.execute("INSERT INTO race_parts VALUES ('20260812-01-01', 1, 'C')")

    n = _upsert_parts(conn, "20260812-01-01", 1, [])

    assert n == 0
    rows = conn.execute(
        "SELECT part_code FROM race_parts WHERE race_id='20260812-01-01' AND boat_number=1 ORDER BY part_code"
    ).fetchall()
    assert rows == [("C",), ("P",)]


def test_empty_parse_on_empty_table_is_noop():
    conn = _conn()

    n = _upsert_parts(conn, "20260812-01-01", 1, [])

    assert n == 0
    assert conn.execute("SELECT COUNT(*) FROM race_parts").fetchone()[0] == 0


def test_non_empty_parse_still_replaces_rows():
    conn = _conn()
    conn.execute("INSERT INTO race_parts VALUES ('20260812-01-01', 1, 'OLD')")

    n = _upsert_parts(conn, "20260812-01-01", 1, ["P", "C", "P"])  # 重複は除去

    assert n == 2
    rows = conn.execute(
        "SELECT part_code FROM race_parts WHERE race_id='20260812-01-01' AND boat_number=1 ORDER BY part_code"
    ).fetchall()
    assert rows == [("C",), ("P",)]


def test_other_boats_are_untouched():
    conn = _conn()
    conn.execute("INSERT INTO race_parts VALUES ('20260812-01-01', 2, 'P')")

    _upsert_parts(conn, "20260812-01-01", 1, ["C"])

    rows = conn.execute(
        "SELECT boat_number, part_code FROM race_parts ORDER BY boat_number"
    ).fetchall()
    assert rows == [(1, "C"), (2, "P")]
