# -*- coding: utf-8 -*-
"""タグを「元データより古いまま」放置しないことの回帰テスト。

2026-08-24 判明: レース詳細タグの生成は朝 5:50、進入変更スナップショットが
書かれるのは 6:30。タグは常に 40 分前の状態で焼き付けられ、「もう保存済み」と
いう理由で作り直されないため、「進入注意 !」が 4 日連続で 1 件も表示されて
いなかった (該当選手は 2026-08-24 だけで 21 レースに乗っていた)。
実行順に頼らず、元データが新しければ作り直す。
"""
import importlib

prewarm = importlib.import_module("scripts.prewarm_race_detail_tags")


class _Conn:
    def __init__(self, rows, source_written_at):
        self._rows = rows
        self._source = source_written_at
        self.closed = False

    def execute(self, sql, params=None):
        if "racer_entry_change_snapshots" in sql:
            return _Result([(self._source,)])
        return _Result(self._rows)

    def close(self):
        self.closed = True


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


def _key(race_id):
    return prewarm._race_detail_tag_cache_key(race_id)


def test_cached_tags_older_than_the_source_are_rebuilt():
    conn = _Conn(
        rows=[(_key("20260824-06-01"), 1000.0)],
        source_written_at="2026-08-24T00:20:00",  # キャッシュより新しい
    )

    missing = prewarm._missing_cached_race_ids(
        ["20260824-06-01"], conn=conn, target_date="2026-08-24"
    )

    assert missing == ["20260824-06-01"], "元データが新しいなら作り直す"


def test_cached_tags_newer_than_the_source_are_kept():
    source_iso = "2026-08-24T00:20:00"
    from datetime import datetime

    newer = datetime.fromisoformat(source_iso).timestamp() + 60
    conn = _Conn(rows=[(_key("20260824-06-01"), newer)], source_written_at=source_iso)

    missing = prewarm._missing_cached_race_ids(
        ["20260824-06-01"], conn=conn, target_date="2026-08-24"
    )

    assert missing == [], "新しいタグを毎回作り直すと朝の予算を無駄にする"


def test_uncached_races_are_always_rebuilt():
    conn = _Conn(rows=[], source_written_at="2026-08-24T00:20:00")

    missing = prewarm._missing_cached_race_ids(
        ["20260824-06-01"], conn=conn, target_date="2026-08-24"
    )

    assert missing == ["20260824-06-01"]


def test_tag_prefetch_does_not_bake_empty_tags_on_load_failure(monkeypatch):
    """進入変更の読み込み失敗は、run の失敗として伝わること。

    2026-08-25: 朝 5:40 の一括生成が一時的な DB 不調に当たり、読み込み失敗が
    黙って {} になって全 156 レースに「進入注意なし」が焼かれた。鮮度チェックは
    それを完成品と見なすので、出るべき 17 レースが終日 0 件のままになった。
    """
    import pytest

    from src.web import app as app_module

    def _boom(*_a, **k):
        if k.get("strict"):
            raise RuntimeError("transient connection failure")
        return {}

    monkeypatch.setattr(app_module, "_load_entry_change_snapshot_stats", _boom)
    monkeypatch.setattr(
        app_module, "_prefetch_race_detail_common",
        lambda ids, conn: {
            "race_info": {i: {"race_date": "2026-08-25", "stadium_number": 1} for i in ids},
            "tag_entries": {i: [] for i in ids},
        },
    )
    monkeypatch.setattr(app_module, "_accident_watch_map", lambda *a: {})
    monkeypatch.setattr(app_module, "_ace_motor_threshold", lambda *a: None)

    with pytest.raises(RuntimeError):
        app_module._prefetch_race_detail_tag_inputs(["20260825-01-12"], conn=None)
