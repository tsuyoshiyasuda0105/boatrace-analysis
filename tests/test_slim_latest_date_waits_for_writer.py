# -*- coding: utf-8 -*-
"""取込中のロックを「データ無し」と読み違えないことの回帰テスト。

2026-08-28: 朝の点検 (06:42) がデルタ適用 (06:30-06:50) の真っ最中に走り、
slim DB が書き込みロック中で読めず latest_race_date=null になった。点検は
それを「昨日のバックテストが取り込まれていない」と判定し、毎朝
preflight warning を出していた。実際のデータは正常 (8/27 まで取込済み)。
"""
from pathlib import Path


def test_web_reader_waits_for_the_writer():
    source = Path("src/web/kachisuji_bp.py").read_text(encoding="utf-8")
    body = source[source.index("def _slim_latest_race_date"):]
    body = body[: body.index("@bp.get")]
    assert "timeout=20" in body, "待たずに読むとロック中が『データ無し』になる"
    assert "busy_timeout" in body


def test_scheduler_reader_waits_for_the_writer():
    source = Path("scripts/render_maintenance_scheduler.py").read_text(encoding="utf-8")
    assert "busy_timeout = 20000" in source
