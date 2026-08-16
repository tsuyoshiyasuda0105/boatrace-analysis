from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from scripts.sync_kachisuji_racers import RACERS_SCHEMA, sync_racers


def _source(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(RACERS_SCHEMA)
        connection.executemany(
            "INSERT INTO racers VALUES (?, ?, ?)",
            [(4320, "峰竜太", "ﾐﾈ ﾘｭｳﾀ"), (4190, "長嶋万記", "ﾅｶﾞｼﾏ ﾏｷ")],
        )
        connection.execute("CREATE TABLE protected (value TEXT)")
        connection.execute("INSERT INTO protected VALUES ('unchanged')")


def test_sync_racers_replaces_only_destination_racer_master(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    destination = tmp_path / "destination.db"
    _source(source)
    with sqlite3.connect(destination) as connection:
        connection.executescript(RACERS_SCHEMA)
        connection.execute("INSERT INTO racers VALUES (9999, 'old', 'old')")
        connection.execute("CREATE TABLE retained (value TEXT)")
        connection.execute("INSERT INTO retained VALUES ('keep')")

    result = sync_racers(source, destination)

    assert result["copied"] == 2
    with sqlite3.connect(destination) as connection:
        assert connection.execute("SELECT * FROM racers ORDER BY racer_number").fetchall() == [
            (4190, "長嶋万記", "ﾅｶﾞｼﾏ ﾏｷ"),
            (4320, "峰竜太", "ﾐﾈ ﾘｭｳﾀ"),
        ]
        assert connection.execute("SELECT value FROM retained").fetchone()[0] == "keep"
    with sqlite3.connect(source) as connection:
        assert connection.execute("SELECT value FROM protected").fetchone()[0] == "unchanged"


def test_sync_racers_rejects_same_source_and_destination(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    _source(source)

    with pytest.raises(ValueError, match="must differ"):
        sync_racers(source, source)
