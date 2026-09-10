# -*- coding: utf-8 -*-
"""値だけを運ぶ「継ぎ当て」デルタ (patch_*.db)。

後から足した列の値を過去の全行へ届けるのに、行を丸ごと運ぶと 638MB になる。
継ぎ当ては race_id と対象の列だけを運ぶので 56MB で済む。

行は作らない・消さない。書き換えるのは名指しした列だけ。
"""
from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from src.kachisuji.delta_transport import (
    PATCH_TABLE,
    _apply_one,
    canonical_delta_name,
)

SLIM = ["race_id", "race_date", "b1_class", "b1_entry_change_rate"]


def _slim(tmp_path: Path, rows: list[tuple], columns: list[str] = SLIM) -> Path:
    path = tmp_path / "slim.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE asof_race_features ("
            + ",".join(f"{name} TEXT" for name in columns)
            + ", PRIMARY KEY (race_id))"
        )
        conn.execute("CREATE TABLE racers (racer_number TEXT PRIMARY KEY, name TEXT)")
        conn.execute(
            "CREATE TABLE applied_deltas (name TEXT PRIMARY KEY, applied_at TEXT)"
        )
        conn.executemany(
            f"INSERT INTO asof_race_features VALUES ({','.join('?' * len(columns))})",
            rows,
        )
    return path


def _quoted(column: str) -> str:
    """テスト側で作る表の列名を引用する。二重引用符は "" で逃がす。"""
    escaped = column.replace('"', '""')
    return f'"{escaped}"'


def _patch(
    tmp_path: Path, columns: list[str], rows: list[tuple], name: str = "patch_a.db"
) -> Path:
    path = tmp_path / name
    with sqlite3.connect(path) as conn:
        conn.execute(
            f"CREATE TABLE {PATCH_TABLE} ("
            + ",".join(_quoted(column) for column in columns)
            + ")"
        )
        conn.executemany(
            f"INSERT INTO {PATCH_TABLE} VALUES ({','.join('?' * len(columns))})", rows
        )
    return path


def _apply(slim: Path, patch: Path, name: str | None = None) -> None:
    connection = sqlite3.connect(slim)
    try:
        _apply_one(connection, patch, name or patch.name)
        connection.commit()
    finally:
        connection.close()


def _read(slim: Path, column: str) -> dict[str, object]:
    with sqlite3.connect(slim) as conn:
        return dict(conn.execute(f"SELECT race_id, {column} FROM asof_race_features"))


def test_the_patch_writes_only_the_named_column(tmp_path: Path) -> None:
    slim = _slim(tmp_path, [("a", "2026-09-01", "A1", None), ("b", "2026-09-02", "B1", None)])
    patch = _patch(tmp_path, ["race_id", "b1_entry_change_rate"], [("a", "3.5"), ("b", "9.0")])

    _apply(slim, patch)

    assert _read(slim, "b1_entry_change_rate") == {"a": "3.5", "b": "9.0"}
    assert _read(slim, "b1_class") == {"a": "A1", "b": "B1"}
    assert _read(slim, "race_date") == {"a": "2026-09-01", "b": "2026-09-02"}


def test_the_patch_never_creates_a_row(tmp_path: Path) -> None:
    """slim に無い race_id は黙って無視する。行を増やさない。"""
    slim = _slim(tmp_path, [("a", "2026-09-01", "A1", None)])
    patch = _patch(
        tmp_path, ["race_id", "b1_entry_change_rate"], [("a", "3.5"), ("unknown", "9.9")]
    )

    _apply(slim, patch)

    with sqlite3.connect(slim) as conn:
        assert conn.execute("SELECT COUNT(*) FROM asof_race_features").fetchone()[0] == 1
    assert _read(slim, "b1_entry_change_rate") == {"a": "3.5"}


def test_a_missing_column_is_added_before_writing(tmp_path: Path) -> None:
    """継ぎ当てが先に届いても動くように、無い列は足してから書く。"""
    slim = _slim(tmp_path, [("a", "2026-09-01", "A1")], columns=SLIM[:3])
    patch = _patch(tmp_path, ["race_id", "b2_entry_change_rate"], [("a", "4.5")])

    _apply(slim, patch)

    with sqlite3.connect(slim) as conn:
        columns = [r[1] for r in conn.execute("PRAGMA table_info(asof_race_features)")]
    assert columns == [*SLIM[:3], "b2_entry_change_rate"]
    assert _read(slim, "b2_entry_change_rate") == {"a": "4.5"}


def test_applying_twice_gives_the_same_result(tmp_path: Path) -> None:
    slim = _slim(tmp_path, [("a", "2026-09-01", "A1", None)])
    patch = _patch(tmp_path, ["race_id", "b1_entry_change_rate"], [("a", "3.5")])

    _apply(slim, patch)
    _apply(slim, patch)

    assert _read(slim, "b1_entry_change_rate") == {"a": "3.5"}
    with sqlite3.connect(slim) as conn:
        assert conn.execute("SELECT COUNT(*) FROM applied_deltas").fetchone()[0] == 1


def test_a_null_in_the_patch_clears_that_cell(tmp_path: Path) -> None:
    """判定できない選手は NULL のまま届く。古い値を残さない。"""
    slim = _slim(tmp_path, [("a", "2026-09-01", "A1", "99.9")])
    patch = _patch(tmp_path, ["race_id", "b1_entry_change_rate"], [("a", None)])

    _apply(slim, patch)

    assert _read(slim, "b1_entry_change_rate") == {"a": None}


def test_several_columns_travel_together(tmp_path: Path) -> None:
    slim = _slim(tmp_path, [("a", "2026-09-01", "A1", None)])
    patch = _patch(
        tmp_path,
        ["race_id", "b1_entry_change_rate", "b2_entry_change_rate"],
        [("a", "1.0", "2.0")],
    )

    _apply(slim, patch)

    assert _read(slim, "b1_entry_change_rate") == {"a": "1.0"}
    assert _read(slim, "b2_entry_change_rate") == {"a": "2.0"}


# --- 受け取ってはいけないもの ---------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        'x"; DROP TABLE racers; --',
        "b1 rate",
        "B1_Rate",
        "1_rate",
        "b1_rate; DELETE FROM asof_race_features",
        "race_date; --",
    ],
)
def test_a_hostile_column_name_never_reaches_the_sql(tmp_path: Path, bad: str) -> None:
    slim = _slim(tmp_path, [("a", "2026-09-01", "A1", None)])
    patch = _patch(tmp_path, ["race_id", bad], [("a", "1.0")])

    with pytest.raises(ValueError, match="unsafe column name"):
        _apply(slim, patch)

    with sqlite3.connect(slim) as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert "racers" in tables
        assert conn.execute("SELECT COUNT(*) FROM asof_race_features").fetchone()[0] == 1
    assert _read(slim, "b1_class") == {"a": "A1"}


def test_a_patch_without_race_id_first_is_refused(tmp_path: Path) -> None:
    slim = _slim(tmp_path, [("a", "2026-09-01", "A1", None)])
    patch = _patch(tmp_path, ["b1_entry_change_rate", "race_id"], [("1.0", "a")])

    with pytest.raises(ValueError, match="race_id"):
        _apply(slim, patch)


def test_a_patch_with_nothing_to_write_is_refused(tmp_path: Path) -> None:
    slim = _slim(tmp_path, [("a", "2026-09-01", "A1", None)])
    patch = _patch(tmp_path, ["race_id"], [("a",)])

    with pytest.raises(ValueError, match="no columns to write"):
        _apply(slim, patch)


def test_a_patch_without_its_table_is_refused(tmp_path: Path) -> None:
    slim = _slim(tmp_path, [("a", "2026-09-01", "A1", None)])
    path = tmp_path / "patch_empty.db"
    sqlite3.connect(path).execute("CREATE TABLE other (x TEXT)")

    with pytest.raises(ValueError, match="missing required table"):
        _apply(slim, path)


def test_a_normal_delta_is_still_applied_as_whole_rows(tmp_path: Path) -> None:
    """名前が patch_ で始まらないものは、これまでどおり行ごと運ぶ。"""
    slim = _slim(tmp_path, [("a", "2026-09-01", "A1", None)])
    path = tmp_path / "backfill_x.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE asof_race_features ("
            + ",".join(f"{name} TEXT" for name in SLIM)
            + ", PRIMARY KEY (race_id))"
        )
        conn.execute("CREATE TABLE racers (racer_number TEXT PRIMARY KEY, name TEXT)")
        conn.execute(
            "INSERT INTO asof_race_features VALUES ('b','2026-09-02','B1','7.0')"
        )

    _apply(slim, path)

    with sqlite3.connect(slim) as conn:
        assert conn.execute("SELECT COUNT(*) FROM asof_race_features").fetchone()[0] == 2


def test_the_patch_name_survives_the_transport(tmp_path: Path) -> None:
    """名前で判定するので、輸送で名前が変わっては困る。"""
    assert canonical_delta_name(Path("patch_entry_change_20260910.db")) == (
        "patch_entry_change_20260910.db"
    )
    with pytest.raises(ValueError):
        canonical_delta_name(Path("patch bad name.db"))


def test_the_patch_tool_keeps_each_column_s_own_type(tmp_path):
    """数値以外の列を運んでも型が崩れないこと。

    以前は全列を REAL として作っていた。着順 "1-2-3" は運良く文字のまま
    残るが、単勝の "1" のような数字だけの文字列や潮の名前は型がずれうる。
    """
    import subprocess
    import sys

    source = tmp_path / "search.db"
    with sqlite3.connect(source) as conn:
        conn.execute(
            "CREATE TABLE asof_race_features (race_id TEXT PRIMARY KEY, race_date TEXT, "
            "result_sanrentan TEXT, payout_sanrentan INTEGER, tide_phase TEXT, "
            "b1_entry_change_rate REAL)"
        )
        conn.execute(
            "INSERT INTO asof_race_features VALUES "
            "('a','2026-09-01','1-2-3',1230,'満潮前後',3.5)"
        )
    out = tmp_path / "patch_types.db"
    done = subprocess.run(
        [sys.executable, "scripts/emit_column_patch.py", "--source", str(source),
         "--columns", "result_sanrentan,payout_sanrentan,tide_phase,b1_entry_change_rate",
         "--out", str(out)],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert done.returncode == 0, done.stderr
    with sqlite3.connect(out) as conn:
        types = {r[1]: r[2] for r in conn.execute(f"PRAGMA table_info({PATCH_TABLE})")}
        row = conn.execute(
            f"SELECT result_sanrentan, payout_sanrentan, tide_phase, "
            f"typeof(payout_sanrentan) FROM {PATCH_TABLE}"
        ).fetchone()
    assert types == {"race_id": "TEXT", "result_sanrentan": "TEXT",
                     "payout_sanrentan": "INTEGER", "tide_phase": "TEXT",
                     "b1_entry_change_rate": "REAL"}
    assert row == ("1-2-3", 1230, "満潮前後", "integer")
