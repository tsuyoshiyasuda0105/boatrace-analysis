# -*- coding: utf-8 -*-
"""backfill デルタだけ既存行を上書きすることを固定する回帰テスト。

2026-08-29: 過去の選手情報欠測 (福岡2016-24・多摩川2016-20 約3万レース) を
本番へ届けるため、名前が "backfill" で始まるデルタに限り INSERT OR REPLACE で
既存行を置き換えるようにした。通常の毎晩デルタ (追加専用) の安全は不変。
"""
import sqlite3
from pathlib import Path

from src.kachisuji import delta_transport as dt


def _make_slim(path: Path) -> None:
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE asof_race_features (race_id TEXT PRIMARY KEY, b1_racer_id INTEGER)")
    c.execute("CREATE TABLE racers (racer_id INTEGER PRIMARY KEY, name TEXT)")
    c.execute("CREATE TABLE applied_deltas (name TEXT PRIMARY KEY, applied_at TEXT)")
    # 既存: 選手情報が NULL の古い行
    c.execute("INSERT INTO asof_race_features VALUES ('R1', NULL)")
    c.commit()
    c.close()


def _make_delta(path: Path) -> None:
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE asof_race_features (race_id TEXT PRIMARY KEY, b1_racer_id INTEGER)")
    c.execute("CREATE TABLE racers (racer_id INTEGER PRIMARY KEY, name TEXT)")
    # 同じ race_id を、正しい選手情報付きで
    c.execute("INSERT INTO asof_race_features VALUES ('R1', 4848)")
    c.commit()
    c.close()


def test_backfill_named_delta_overwrites_existing_row(tmp_path):
    slim = tmp_path / "slim.db"
    delta = tmp_path / "backfill_20260829.db"
    _make_slim(slim)
    _make_delta(delta)
    conn = sqlite3.connect(slim)
    dt._apply_one(conn, delta, "backfill_20260829")
    conn.commit()
    val = conn.execute("SELECT b1_racer_id FROM asof_race_features WHERE race_id='R1'").fetchone()[0]
    conn.close()
    assert val == 4848, "backfill デルタは既存の NULL 行を正しい値で上書きする"


def test_normal_delta_does_not_overwrite_existing_row(tmp_path):
    slim = tmp_path / "slim.db"
    delta = tmp_path / "kachisuji_delta_20260829.db"
    _make_slim(slim)
    _make_delta(delta)
    conn = sqlite3.connect(slim)
    dt._apply_one(conn, delta, "kachisuji_delta_20260829")
    conn.commit()
    val = conn.execute("SELECT b1_racer_id FROM asof_race_features WHERE race_id='R1'").fetchone()[0]
    conn.close()
    assert val is None, "通常デルタは既存行を上書きしない (追加専用の安全は不変)"


def test_replace_marker_is_prefix_only():
    assert dt._delta_wants_replace("backfill_20260829")
    assert dt._delta_wants_replace("BACKFILL_x")
    assert not dt._delta_wants_replace("kachisuji_delta_20260829")
    assert not dt._delta_wants_replace("delta_backfill_20260829")  # 途中に含むだけは不可
