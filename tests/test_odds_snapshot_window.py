"""Render版オッズ取得の T-5min 取りこぼし修正の回帰テスト (2026-08-12 障害)。

render.yaml のオッズ cron は 5 分間隔。締切5分前を狙う T-5min の許容窓 (tolerance)
が cron 間隔の半分 (2.5分) 以上あれば、各レースの捕捉窓 [close-7.5, close-2.5] に
必ず1回だけ tick が入り、取りこぼしがゼロになることを検証する。
"""
from datetime import datetime, timedelta

import pytest

from scripts import odds_scheduler_render as render


def _catches_per_race(close_offset_sec: int, mins_before: float, tol: float) -> int:
    """5分間隔の tick 列に対し、1レースが due になる回数を数える。

    find_due_snapshots と同じ判定: target = close - mins_before,
    delta(min) = (now - target)/60, -tol <= delta <= tol なら due。
    (実運用は (race_id,label) dedup で1回に落ちるが、まず「窓に入る tick が
    最低1つある」ことが必要。ここでは窓に入る tick 数を数える。)
    """
    base = datetime(2026, 8, 14, 10, 0, 0)
    close = base + timedelta(seconds=close_offset_sec)
    target = close - timedelta(minutes=mins_before)
    catches = 0
    # 実運用の tick は終日連続なので、target の前後に十分広いグリッドを回す
    # (グリッド端で人工的に取りこぼさないよう -30分〜+30分)。
    for k in range(-30, 30, 5):
        now = base + timedelta(minutes=k)
        delta_min = (now - target).total_seconds() / 60.0
        if -tol <= delta_min <= tol:
            catches += 1
    return catches


def test_render_rule_uses_widened_tolerance():
    # 修正後: T-5min の tolerance は 2.5 分 (旧 0.5 は取りこぼしの原因)
    labels = {r[0]: r for r in render.RENDER_SNAPSHOT_RULES}
    assert "T-5min" in labels
    assert labels["T-5min"][2] >= 2.5


def test_old_tolerance_missed_most_races():
    # 旧 tol=0.5 は、締切時刻がずれると 5分tick の間に落ちて 0 回捕捉になる
    misses = sum(
        1 for off in range(0, 300, 15)
        if _catches_per_race(off, mins_before=5, tol=0.5) == 0
    )
    assert misses > 0  # 取りこぼしが存在した (障害の再現)


def test_widened_tolerance_catches_every_race():
    # 修正後 tol=2.5: どの締切秒オフセットでも必ず 1 回以上 tick が窓に入る
    for off in range(0, 300, 5):
        catches = _catches_per_race(off, mins_before=5, tol=2.5)
        assert catches >= 1, f"close offset {off}s で捕捉0 (取りこぼし)"


def test_widened_tolerance_at_most_two_catches():
    # 窓幅5分は cron間隔と同じなので、多くても 2 tick。dedup で最終1回に収束する。
    for off in range(0, 300, 5):
        assert _catches_per_race(off, mins_before=5, tol=2.5) <= 2
