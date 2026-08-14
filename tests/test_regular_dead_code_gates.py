from datetime import datetime
from pathlib import Path

import pytest

from scripts import render_regular_scheduler as scheduler


ROOT = Path(__file__).resolve().parents[1]
DISABLED_PATHS = (
    "run_morning",
    "run_morning_catchup_if_needed",
    "run_tide_self_heal",
    "run_hourly",
    "run_accident_self_heal",
    "run_nightly",
    "run_roi_history_slot",
)


def test_render_regular_service_enables_daytime_lite():
    blueprint = (ROOT / "render.yaml").read_text(encoding="utf-8")
    regular_service = blueprint.split("\n    name: boatrace-regular-cron\n", 1)[1].split(
        "- type:", 1
    )[0]

    assert "BOATRACE_RENDER_DAYTIME_LITE" in regular_service
    assert 'value: "1"' in regular_service


@pytest.mark.parametrize(
    "now",
    [
        datetime(2026, 8, 14, 7, 0, tzinfo=scheduler.JST),
        datetime(2026, 8, 14, 10, 0, tzinfo=scheduler.JST),
        datetime(2026, 8, 14, 12, 0, tzinfo=scheduler.JST),
        datetime(2026, 8, 14, 23, 35, tzinfo=scheduler.JST),
    ],
)
def test_production_flags_make_legacy_regular_paths_unreachable(monkeypatch, now):
    monkeypatch.setenv("DATABASE_URL", "postgresql://test.invalid/db")
    monkeypatch.setenv("BOATRACE_RENDER_DAYTIME_LITE", "1")
    monkeypatch.setenv("BOATRACE_DEDICATED_PROGRAM_BOOTSTRAP", "1")
    monkeypatch.setattr(scheduler, "jst_now", lambda: now)
    monkeypatch.setattr(scheduler, "ensure_task_runs_table", lambda: None)
    monkeypatch.setattr(scheduler, "run_py", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(scheduler, "run_lite_daytime_bootstrap", lambda _now: True)
    monkeypatch.setattr(scheduler, "run_top_page_snapshot", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(scheduler, "task_success_exists", lambda *_args: False)

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("production-disabled regular path was called")

    for name in DISABLED_PATHS:
        monkeypatch.setattr(scheduler, name, fail_if_called)

    assert scheduler.main.__wrapped__() == 0
