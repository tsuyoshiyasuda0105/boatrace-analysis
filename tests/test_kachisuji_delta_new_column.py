# -*- coding: utf-8 -*-
"""PC 側で特徴量に列が増えても、本番への配信が止まらないこと。

配信は列名を並び順で突き合わせる。列が 1 本増えただけでデルタが丸ごと
拒否され、本番の更新が黙って止まる (2026-09-10 に進入変更率を足して気づいた)。
末尾に足すだけで並びがそろう場合に限り、slim 側へ列を足してから受け取る。
"""
from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from src.kachisuji.delta_transport import _apply_one

OLD = ["race_id", "race_date", "b1_class"]
NEW = [*OLD, "b1_entry_change_rate", "b2_entry_change_rate"]


def _build(path: Path, columns: list[str], rows: list[tuple]) -> Path:
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE asof_race_features ("
            + ",".join(f"{name} TEXT" for name in columns)
            + ", PRIMARY KEY (race_id))"
        )
        conn.execute("CREATE TABLE racers (racer_number TEXT PRIMARY KEY, name TEXT)")
        placeholders = ",".join("?" for _ in columns)
        conn.executemany(
            f"INSERT INTO asof_race_features VALUES ({placeholders})", rows
        )
    return path


def _apply(slim: Path, delta: Path, name: str) -> None:
    """本番と同じく、適用のあとに確定させる (_apply_one は commit しない)。"""
    connection = sqlite3.connect(slim)
    try:
        _apply_one(connection, delta, name)
        connection.commit()
    finally:
        connection.close()


def _columns(path: Path, table: str = "asof_race_features") -> list[str]:
    with sqlite3.connect(path) as conn:
        return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def _slim(tmp_path: Path, columns: list[str], rows: list[tuple]) -> Path:
    path = _build(tmp_path / "slim.db", columns, rows)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE applied_deltas (name TEXT PRIMARY KEY, applied_at TEXT)"
        )
    return path


def test_a_delta_with_a_new_column_is_accepted(tmp_path: Path) -> None:
    slim = _slim(tmp_path, OLD, [("old", "2026-09-01", "A1")])
    delta = _build(tmp_path / "backfill_x.db", NEW, [("new", "2026-09-10", "A1", "3.5", "9.0")])

    _apply(slim, delta, "backfill_x.db")

    assert _columns(slim) == NEW
    with sqlite3.connect(slim) as conn:
        stored = dict(
            conn.execute("SELECT race_id, b1_entry_change_rate FROM asof_race_features")
        )
    assert stored == {"old": None, "new": "3.5"}


def test_the_existing_rows_keep_their_values(tmp_path: Path) -> None:
    """列を足すだけ。もとから入っている値を消さない。"""
    slim = _slim(tmp_path, OLD, [("old", "2026-09-01", "B1")])
    delta = _build(tmp_path / "d.db", NEW, [("new", "2026-09-10", "A1", "3.5", "9.0")])

    _apply(slim, delta, "d.db")

    with sqlite3.connect(slim) as conn:
        assert conn.execute(
            "SELECT b1_class FROM asof_race_features WHERE race_id='old'"
        ).fetchone()[0] == "B1"


def test_a_column_inserted_in_the_middle_is_still_refused(tmp_path: Path) -> None:
    """末尾に足すだけで並びがそろわないなら、本当の食い違い。受け取らない。"""
    slim = _slim(tmp_path, OLD, [("old", "2026-09-01", "A1")])
    middle = ["race_id", "race_date", "b1_entry_change_rate", "b1_class"]
    delta = _build(tmp_path / "mid.db", middle, [("new", "2026-09-10", "3.5", "A1")])

    with pytest.raises(ValueError, match="schema mismatch"):
        _apply(slim, delta, "mid.db")
    assert _columns(slim) == OLD


def test_a_delta_missing_a_column_is_refused(tmp_path: Path) -> None:
    """列が減るのは配信の取り違え。黙って受け取らない。"""
    slim = _slim(tmp_path, NEW, [("old", "2026-09-01", "A1", "1.0", "2.0")])
    delta = _build(tmp_path / "short.db", OLD, [("new", "2026-09-10", "A1")])

    with pytest.raises(ValueError, match="schema mismatch"):
        _apply(slim, delta, "short.db")


@pytest.mark.parametrize(
    "bad",
    [
        'b1_rate"; DROP TABLE racers; --',
        "b1 rate",
        "B1_Rate",
        "1_rate",
        "b1_rate; DELETE FROM asof_race_features",
    ],
)
def test_a_hostile_column_name_never_reaches_the_ddl(tmp_path: Path, bad: str) -> None:
    """列名はデルタファイル由来。そのまま DDL へ入れない。"""
    slim = _slim(tmp_path, OLD, [("old", "2026-09-01", "A1")])
    path = tmp_path / "evil.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE asof_race_features (race_id TEXT PRIMARY KEY, "
            f'race_date TEXT, b1_class TEXT, "{bad.replace(chr(34), chr(34) * 2)}" TEXT)'
        )
        conn.execute("CREATE TABLE racers (racer_number TEXT PRIMARY KEY, name TEXT)")

    with pytest.raises(ValueError):
        _apply(slim, path, "evil.db")
    assert _columns(slim) == OLD
    with sqlite3.connect(slim) as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert "racers" in tables
    with sqlite3.connect(slim) as conn:
        assert conn.execute("SELECT COUNT(*) FROM asof_race_features").fetchone()[0] == 1


def test_the_same_shape_applies_without_touching_the_schema(tmp_path: Path) -> None:
    slim = _slim(tmp_path, NEW, [("old", "2026-09-01", "A1", "1.0", "2.0")])
    delta = _build(tmp_path / "same.db", NEW, [("new", "2026-09-10", "A1", "3.5", "9.0")])

    _apply(slim, delta, "same.db")

    assert _columns(slim) == NEW
    with sqlite3.connect(slim) as conn:
        assert conn.execute("SELECT COUNT(*) FROM asof_race_features").fetchone()[0] == 2
