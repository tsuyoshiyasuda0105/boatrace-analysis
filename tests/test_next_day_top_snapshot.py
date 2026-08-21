# -*- coding: utf-8 -*-
"""翌日分 TOP スナップショットの先回り生成の回帰テスト。

2026-08-22 未明の実障害: スナップショットは「その日の 04:00-07:00 メンテ窓」で
しか作られず、00:00 に日付が変わってから朝まで本番に当日分が存在しなかった。
その間 / と /races は毎リクエストで 156 レースをフル描画し (20秒超)、
重なってワーカーが詰まり、サイトが断続的に落ちた。
"""
from datetime import datetime, timedelta, timezone

import scripts.render_regular_scheduler as scheduler


JST = timezone(timedelta(hours=9))


def test_builds_tomorrow_snapshot_at_22h(monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler, "top_page_snapshot_exists", lambda d: False)
    monkeypatch.setattr(
        scheduler,
        "run_top_page_snapshot",
        lambda now, **kw: calls.append(kw) or True,
    )

    ok = scheduler.run_next_day_top_snapshot(datetime(2026, 8, 21, 22, 5, tzinfo=JST))

    assert ok is True
    assert len(calls) == 1
    assert calls[0]["target_date"] == "2026-08-22", "翌日分を作ること"
    assert calls[0]["lightweight"] is False


def test_skips_when_tomorrow_snapshot_already_exists(monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler, "top_page_snapshot_exists", lambda d: True)
    monkeypatch.setattr(
        scheduler, "run_top_page_snapshot", lambda now, **kw: calls.append(kw) or True
    )

    ok = scheduler.run_next_day_top_snapshot(datetime(2026, 8, 21, 22, 30, tzinfo=JST))

    assert ok is True
    assert calls == [], "既にあるなら作り直さない"


def test_does_not_build_outside_22h(monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler, "top_page_snapshot_exists", lambda d: False)
    monkeypatch.setattr(
        scheduler, "run_top_page_snapshot", lambda now, **kw: calls.append(kw) or True
    )

    for hour in (8, 15, 21, 23):
        scheduler.run_next_day_top_snapshot(
            datetime(2026, 8, 21, hour, 0, tzinfo=JST)
        )

    assert calls == [], "22時台以外は作らない"


def test_existence_check_failure_falls_back_to_building(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(scheduler, "db_connect", boom)

    assert scheduler.top_page_snapshot_exists("2026-08-22") is False, (
        "判定できないときは作りに行く (無いまま朝を迎えるより安全)"
    )
