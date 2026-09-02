# -*- coding: utf-8 -*-
"""TOP スナップショットが空のまま焼き付く障害の回帰テスト。

2026-09-02 の実障害: OpenAPI が 3 会場欠けてソースゲートが通らず、
`run_lite_daytime_bootstrap` が早期 return して TOP スナップショットが
当日一度も作り直されなかった。前夜 22:04 の翌日先回り生成が
(番組表取込 23:33 より前だったため) 空で焼いたものが残り続け、
公式ソース経由でレースが 156 件揃っていたのに画面は
「この日のデータはありません」のまま一日固まった。

守るべきことは 2 つ:
  1. レース 0 件のスナップショットは保存しない (毒を作らない)
  2. ソースゲートが通らなくても、当日のレースがあれば画面だけは作り直す
     (ただしゲート自体は緩めず、重い prewarm は止めたまま)
"""
from datetime import datetime, timedelta, timezone

import pytest

import scripts.build_top_page_snapshot as builder
import scripts.render_regular_scheduler as scheduler


JST = timezone(timedelta(hours=9))


# --------------------------------------------------------------- 修正1: 保存抑止

def _payload(n_races: int) -> dict:
    groups = []
    if n_races:
        groups = [{"stadium_number": 1, "races": [{"race_id": f"r{i}"} for i in range(n_races)]}]
    return {
        "version": "v3",
        "date": "2026-09-02",
        "stadium_groups": groups,
        "empty": not bool(groups),
        "initial_market_signals": {"race_badges": {}},
    }


@pytest.fixture
def builder_env(monkeypatch):
    written = []
    monkeypatch.setattr(
        builder.web_app, "_write_top_page_snapshot",
        lambda date, payload: written.append((date, payload)),
    )
    return written


def _run_builder(monkeypatch, n_races: int, *extra_args: str) -> int:
    monkeypatch.setattr(
        builder.web_app, "_build_top_page_snapshot_payload",
        lambda date, **kw: _payload(n_races),
    )
    monkeypatch.setattr(
        builder.sys, "argv",
        ["build_top_page_snapshot.py", "--date", "2026-09-02", *extra_args],
    )
    return builder.main()


def test_empty_snapshot_is_not_written(monkeypatch, builder_env):
    _run_builder(monkeypatch, 0)
    assert builder_env == [], "レース0件のスナップショットを保存してはいけない"


def test_empty_snapshot_still_exits_zero(monkeypatch, builder_env):
    code = _run_builder(monkeypatch, 0)
    assert code == 0, "『まだ早い』だけなので失敗扱いにしない (cron を赤くしない)"


def test_snapshot_with_races_is_written(monkeypatch, builder_env):
    code = _run_builder(monkeypatch, 12)
    assert code == 0
    assert len(builder_env) == 1, "レースがあれば従来どおり保存する"
    assert builder_env[0][0] == "2026-09-02"


def test_lightweight_mode_also_skips_empty(monkeypatch, builder_env):
    _run_builder(monkeypatch, 0, "--lightweight")
    assert builder_env == [], "--lightweight でも空なら保存しない"


# ------------------------------------------------- 修正2: ゲート不成立時のTOP生成

@pytest.fixture
def gate_blocked(monkeypatch):
    """ソースゲートだけが通らない状態を作る。"""
    calls = {"snapshot": [], "recorded": []}
    monkeypatch.setattr(scheduler, "run_yesterday_results_backfill", lambda now: True)
    monkeypatch.setattr(scheduler, "task_success_exists", lambda *a: False)
    monkeypatch.setattr(scheduler, "task_attempt_exists", lambda *a: False)
    monkeypatch.setattr(scheduler, "daily_source_counts", lambda d: {"races": 156})
    monkeypatch.setattr(scheduler, "daily_source_complete", lambda c: True)
    monkeypatch.setattr(scheduler, "run_program_source_gate", lambda *a, **kw: False)
    monkeypatch.setattr(
        scheduler, "record_task",
        lambda name, date, status, detail=None: calls["recorded"].append((name, status, detail)),
    )
    monkeypatch.setattr(
        scheduler, "run_top_page_snapshot",
        lambda now, **kw: calls["snapshot"].append(kw) or True,
    )
    return calls


def test_gate_blocked_still_rebuilds_top_when_races_exist(monkeypatch, gate_blocked):
    monkeypatch.setattr(scheduler, "race_count_for_date", lambda d: 156)

    scheduler.run_lite_daytime_bootstrap(datetime(2026, 9, 2, 8, 5, tzinfo=JST))

    assert len(gate_blocked["snapshot"]) == 1, "レースがあるなら画面だけは作り直す"
    assert gate_blocked["snapshot"][0]["lightweight"] is True
    assert any("top_snapshot_rebuilt" in str(d) for _, _, d in gate_blocked["recorded"])


def test_gate_blocked_skips_rebuild_when_no_races(monkeypatch, gate_blocked):
    monkeypatch.setattr(scheduler, "race_count_for_date", lambda d: 0)

    scheduler.run_lite_daytime_bootstrap(datetime(2026, 9, 2, 8, 5, tzinfo=JST))

    assert gate_blocked["snapshot"] == [], "レースが無いなら作るものが無い"
    assert any(d == "source_gate_not_ready" for _, _, d in gate_blocked["recorded"])


def test_gate_blocked_still_returns_false(monkeypatch, gate_blocked):
    monkeypatch.setattr(scheduler, "race_count_for_date", lambda d: 156)

    ok = scheduler.run_lite_daytime_bootstrap(datetime(2026, 9, 2, 8, 5, tzinfo=JST))

    assert ok is False, "画面を作り直してもゲート不成立という事実は変えない"
    assert all(s == "failure" for _, s, _ in gate_blocked["recorded"]), (
        "ゲート不成立は失敗のまま記録する (成功に見せかけない)"
    )


def test_gate_ready_path_is_unchanged(monkeypatch, gate_blocked):
    """ゲートが通る従来の流れを壊していないこと。"""
    monkeypatch.setattr(scheduler, "run_program_source_gate", lambda *a, **kw: True)
    monkeypatch.setattr(scheduler, "race_count_for_date", lambda d: 156)
    reached = []
    monkeypatch.setattr(
        scheduler, "run_signal_refresh_slot",
        lambda now, **kw: reached.append("signal") or True,
    )
    monkeypatch.setattr(
        scheduler, "run_detail_pages_selfheal",
        lambda now: reached.append("selfheal") or True,
    )

    ok = scheduler.run_lite_daytime_bootstrap(datetime(2026, 9, 2, 8, 5, tzinfo=JST))

    assert ok is True
    assert reached == ["signal", "selfheal"], "ゲート成立時は従来どおり後続まで進む"
    assert len(gate_blocked["snapshot"]) == 1, "末尾の TOP 生成は従来どおり 1 回"
