# -*- coding: utf-8 -*-
"""入手経路の遅れとデータの欠落を区別することの回帰テスト。

2026-08-28: Open API の当日ファイルの公開が、取得を試みる時間帯 (23-9時) を
過ぎてからになった。scheduler は 11 回失敗を記録し、「cron 失敗が続いている」
警報が 595 回鳴り、朝の点検まで落とした。しかし公式ダウンロード側で 144
レース全部そろっており、データの欠落はゼロだった。

番組データがそろっているなら degraded (入手経路の遅れ)、そろっていない時だけ
failure (データの欠落)。監視はどちらも status='failure' を見ているので、
この区別がそのまま警報の有無になる。
"""
import importlib
from datetime import date, datetime
from pathlib import Path

import pytest

scheduler = importlib.import_module("scripts.render_program_bootstrap_scheduler")


def _capture_writes(monkeypatch):
    written = []
    monkeypatch.setattr(
        scheduler,
        "_write_task",
        lambda task, target, status, detail: written.append((task, status, detail)),
    )
    return written


def test_delay_with_complete_official_data_is_degraded(monkeypatch):
    written = _capture_writes(monkeypatch)
    monkeypatch.setattr(scheduler, "_official_already_covers", lambda _t: True)

    scheduler._record_phase_failure(
        "render_program_bootstrap_openapi_v1",
        date(2026, 8, 28),
        datetime(2026, 8, 28, 9, 50),
        {},
        source_host="example",
        reason="openapi_unavailable",
    )

    task, status, detail = written[0]
    assert status == "degraded", "データがそろっているなら失敗ではない"
    assert detail["covered_by_official"] is True


def test_missing_data_is_still_a_failure(monkeypatch):
    written = _capture_writes(monkeypatch)
    monkeypatch.setattr(scheduler, "_official_already_covers", lambda _t: False)

    scheduler._record_phase_failure(
        "render_program_bootstrap_openapi_v1",
        date(2026, 8, 28),
        datetime(2026, 8, 28, 9, 50),
        {},
        source_host="example",
        reason="openapi_unavailable",
    )

    _, status, detail = written[0]
    assert status == "failure", "本当にデータが無い日は今まで通り鳴らす"
    assert detail["covered_by_official"] is False


def test_watchdogs_only_count_real_failures():
    """監視側が status='failure' だけを数えていること (degraded を拾わない)。"""
    regular = Path("scripts/render_regular_scheduler.py").read_text(encoding="utf-8")
    maintenance = Path("scripts/render_maintenance_scheduler.py").read_text(encoding="utf-8")
    assert "status = 'failure'" in regular
    assert "status = 'failure'" in maintenance
    for source in (regular, maintenance):
        assert "status <> 'success'" not in source, (
            "非成功をすべて失敗とみなすと degraded まで警報になる"
        )
