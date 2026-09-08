# -*- coding: utf-8 -*-
"""当日の合致レースを出すための夜間段取り (2026-09-08) の回帰テスト。

1. 夜間バッチは 履歴 → 完成日を rebuild → 当日を forward の順で回す
2. 差分名は backfill_ で始まり、本番側で既存行を置き換える判定になる
3. slim への書き込みは forward/rebuild のとき OR REPLACE、通常は OR IGNORE のまま
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from scripts import pc_nightly_prepare as nightly
from scripts import refresh_kachisuji_daily as refresh
from src.kachisuji import delta_transport as dt


def test_nightly_runs_history_then_rebuild_then_forward(monkeypatch, tmp_path):
    calls: list[list[str]] = []
    monkeypatch.setattr(nightly, "ROOT", tmp_path)
    monkeypatch.setattr(
        nightly, "_run_local",
        lambda args, allow_prod_sync=False: calls.append(list(args)) or True,
    )

    assert nightly._run_kachisuji_daily("2026-09-07", "2026-09-08") is True

    scripts = [c[0] for c in calls]
    # 履歴 4 手順が最初
    assert scripts[:4] == [
        "scripts/backfill_official.py",
        "scripts/restore_accident_history.py",
        "scripts/restore_start_timing.py",
        "scripts/sync_kachisuji_racers.py",
    ]
    assert calls[0][1:] == ["--start", "2026-09-07", "--end", "2026-09-07", "--local", "--targets", "k"]
    assert "--skip-existing" not in calls[0], "空ファイルを掴んだ日を取り直せなくなる"
    # 完成日は rebuild、当日は forward。それぞれ直後にアップロード
    rebuild = next(c for c in calls if c[0].endswith("refresh_kachisuji_daily.py") and "--rebuild" in c)
    forward = next(c for c in calls if c[0].endswith("refresh_kachisuji_daily.py") and "--forward" in c)
    assert rebuild[1:3] == ["--date", "2026-09-07"]
    assert forward[1:3] == ["--date", "2026-09-08"]
    assert calls.index(rebuild) < calls.index(forward)
    uploads = [c for c in calls if c[0].endswith("upload_kachisuji_delta_pg.py")]
    assert [Path(u[2]).name for u in uploads] == [
        "backfill_day_20260907.db", "backfill_fwd_20260908.db",
    ]


def test_nightly_skips_forward_when_it_is_not_after_completed(monkeypatch, tmp_path):
    calls: list[list[str]] = []
    monkeypatch.setattr(nightly, "ROOT", tmp_path)
    monkeypatch.setattr(
        nightly, "_run_local",
        lambda args, allow_prod_sync=False: calls.append(list(args)) or True,
    )
    nightly._run_kachisuji_daily("2026-09-07", "2026-09-07")
    assert not any("--forward" in c for c in calls)


def test_nightly_continues_when_a_history_step_fails(monkeypatch, tmp_path):
    """履歴が一日ぶん古くなるだけなので、照合の材料づくりは止めない。"""
    monkeypatch.setattr(nightly, "ROOT", tmp_path)
    monkeypatch.setattr(
        nightly, "_run_local",
        lambda args, allow_prod_sync=False: not args[0].endswith("restore_start_timing.py"),
    )
    assert nightly._run_kachisuji_daily("2026-09-07", "2026-09-08") is True


def test_delta_names_are_replace_deltas_and_survive_transport(tmp_path):
    for kind, day in (("day", "2026-09-07"), ("fwd", "2026-09-08")):
        path = nightly._kachisuji_delta_path(kind, day)
        name = dt.canonical_delta_name(path)
        assert name == path.name, "backfill_ 名は正規化で潰されない"
        assert dt._delta_wants_replace(name), "本番で既存行 (前夜の forward 行) を置き換える"
    # 従来の毎晩デルタは追加専用のまま
    assert not dt._delta_wants_replace(dt.canonical_delta_name(tmp_path / "kachisuji_delta_20260907.db"))


def _tiny_db(path: Path, rows: list[tuple[str, str, int | None]]) -> None:
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE asof_race_features (race_id TEXT PRIMARY KEY, race_date TEXT NOT NULL, hit INTEGER)")
    c.execute("CREATE TABLE racers (racer_number INTEGER PRIMARY KEY, name TEXT, name_kana TEXT)")
    c.executemany("INSERT INTO asof_race_features VALUES (?,?,?)", rows)
    c.commit(); c.close()


def test_slim_append_replace_overwrites_forward_rows_but_ignore_keeps_them(tmp_path):
    search = tmp_path / "search.db"
    slim = tmp_path / "slim.db"
    # slim には前夜の forward 行 (結果 NULL)、検索DBには完成行 (結果あり)
    _tiny_db(slim, [("20260907-01-01", "2026-09-07", None)])
    _tiny_db(search, [("20260907-01-01", "2026-09-07", 1), ("20260907-01-02", "2026-09-07", 0)])

    kept = refresh._append_to_slim(search, slim, "2026-09-07", "2026-09-07")
    c = sqlite3.connect(slim)
    assert c.execute("SELECT hit FROM asof_race_features WHERE race_id='20260907-01-01'").fetchone()[0] is None
    assert kept["asof_added"] == 1 and kept["asof_in_range"] == 2
    c.close()

    replaced = refresh._append_to_slim(search, slim, "2026-09-07", "2026-09-07", replace=True)
    c = sqlite3.connect(slim)
    assert c.execute("SELECT hit FROM asof_race_features WHERE race_id='20260907-01-01'").fetchone()[0] == 1
    assert replaced["asof_added"] == 0 and replaced["asof_in_range"] == 2
    c.close()


def test_refresh_cli_exposes_forward_and_rebuild():
    source = Path("scripts/refresh_kachisuji_daily.py").read_text(encoding="utf-8")
    assert '"--forward"' in source and '"--rebuild"' in source
    assert "rebuild=args.rebuild" in source
    assert "replace=replace" in source


def test_emit_delta_refuses_to_overwrite_an_existing_delta(tmp_path):
    """同じ差分名を再利用すると古い差分を本番へ送りかねないので拒否する。"""
    search = tmp_path / "search.db"
    _tiny_db(search, [("20260908-01-01", "2026-09-08", None)])
    delta = tmp_path / "backfill_fwd_20260908.db"
    assert refresh._emit_delta(search, delta, "2026-09-08", "2026-09-08") == 1
    import pytest
    with pytest.raises(FileExistsError):
        refresh._emit_delta(search, delta, "2026-09-08", "2026-09-08")


def test_rebuild_that_loses_rows_is_refused_before_touching_slim(tmp_path, monkeypatch, capsys):
    """元データが欠けたまま作り直したときは slim と差分に伝播させない。

    rebuild は範囲を消してから作り直す。元データが一時的に欠けていると
    「良い行を消して少ない行で置き換える」ことになるので、そこで止める。
    """
    import sys as _sys
    from scripts import refresh_kachisuji_daily as r

    search = tmp_path / "search.db"
    _tiny_db(search, [("A", "2026-09-07", 1), ("B", "2026-09-07", 1)])
    monkeypatch.setattr(r, "SEARCH_DB", search)
    monkeypatch.setattr(r, "SLIM_DB", tmp_path / "slim.db")
    monkeypatch.setattr(r, "connect", lambda _path: sqlite3.connect(":memory:"))

    def shrinking_build(_source, output, date_from, date_to, rebuild=False):
        c = sqlite3.connect(output)
        c.execute("DELETE FROM asof_race_features WHERE race_date BETWEEN ? AND ?", (date_from, date_to))
        c.execute("INSERT INTO asof_race_features VALUES ('A','2026-09-07',1)")
        c.commit(); c.close()
        return {"selected": 1, "inserted": 1, "skipped_existing": 0, "warnings": 0}

    monkeypatch.setattr(r, "build_features", shrinking_build)
    monkeypatch.setattr(_sys, "argv", ["x", "--date", "2026-09-07", "--rebuild",
                                       "--emit-delta", str(tmp_path / "backfill_day_20260907.db")])
    assert r.main() == 4
    assert "fewer rows" in capsys.readouterr().err
    assert not (tmp_path / "slim.db").exists(), "slim には触れない"
    assert not (tmp_path / "backfill_day_20260907.db").exists(), "差分も作らない"
