import json
from pathlib import Path

from scripts import prewarm_strategy_pages as prewarm
from scripts import render_regular_scheduler as scheduler
from src.roi_contract import ROI_DAILY_CACHE_VERSION


APP_SOURCE = Path("src/web/app.py")


def test_all_roi_workers_share_one_cache_version():
    assert scheduler.ROI_DAILY_CACHE_VERSION == ROI_DAILY_CACHE_VERSION
    assert prewarm.ROI_DAILY_CACHE_VERSION == ROI_DAILY_CACHE_VERSION
    assert "ADOPTED_DAILY_SELECT_VERSION = ROI_DAILY_CACHE_VERSION" in APP_SOURCE.read_text(encoding="utf-8")


def test_scheduler_accepts_current_version_and_strategy_signature(monkeypatch):
    payload = {
        "_adopted_daily_select_version": ROI_DAILY_CACHE_VERSION,
        "_strategy_definition_signature": "strategy-sig",
        "_adopted_market_signals_cache_missing": False,
    }

    class _Cursor:
        def fetchone(self):
            return (json.dumps(payload),)

    class _Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, *_args):
            return _Cursor()

    monkeypatch.setattr(scheduler, "db_connect", lambda: _Connection())
    monkeypatch.setattr(scheduler, "strategy_definition_signature", lambda _repo: "strategy-sig")
    assert scheduler.roi_daily_cache_needs_repair("2026-07-31") is False


def test_roi_uses_stable_snapshot_and_single_adopted_registry():
    source = APP_SOURCE.read_text(encoding="utf-8")
    overlay_start = source.index("def _overlay_market_signal_cache_daily")
    overlay_end = source.index("for row in cur:", overlay_start)
    overlay = source[overlay_start:overlay_end]

    assert "_market_signals_last_good_cache_key(rdate)" in overlay
    assert 'snapshot_source = "last_good"' in overlay
    assert 'day_d["_adopted_snapshot_source"] = "raw_reconstructed"' in overlay
    assert "load_roi_history_daily(" in overlay
    assert 'day_d["_adopted_snapshot_source"] = "race_history"' in overlay
    assert "MARKET_SIGNAL_ADOPTED_LEVELS = ROI_STRATEGY_KEYS" in source
    assert "operational_rows = [" in source
    assert 'bool(r.get("_adopted_from_market_signals_cache"))' in source
