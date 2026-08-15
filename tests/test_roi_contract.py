import json
from pathlib import Path
import re
import shutil

import pytest

from scripts import prewarm_strategy_pages as prewarm
from scripts import render_regular_scheduler as scheduler
from src import roi_contract
from src.roi_contract import ROI_DAILY_CACHE_VERSION


APP_SOURCE = Path("src/web/app.py")


def _copy_strategy_sources(destination: Path) -> None:
    for relative_path in roi_contract.STRATEGY_DEFINITION_SOURCE_PATHS:
        target = destination / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(relative_path, target)


def test_all_roi_workers_share_one_cache_version():
    assert scheduler.ROI_DAILY_CACHE_VERSION == ROI_DAILY_CACHE_VERSION
    assert prewarm.ROI_DAILY_CACHE_VERSION == ROI_DAILY_CACHE_VERSION
    assert "ADOPTED_DAILY_SELECT_VERSION = ROI_DAILY_CACHE_VERSION" in APP_SOURCE.read_text(encoding="utf-8")


@pytest.mark.parametrize("relative_path", roi_contract.STRATEGY_DEFINITION_SOURCE_PATHS)
def test_strategy_signature_changes_with_strategy_source(tmp_path, relative_path):
    original_root = tmp_path / "original"
    changed_root = tmp_path / "changed"
    _copy_strategy_sources(original_root)
    _copy_strategy_sources(changed_root)

    target = changed_root / relative_path
    target.write_bytes(target.read_bytes() + b"\n# signature test change\n")

    assert roi_contract.strategy_definition_signature(
        original_root
    ) != roi_contract.strategy_definition_signature(changed_root)


@pytest.mark.parametrize(
    "constant_name",
    (
        "ROI_DAILY_CACHE_VERSION",
        "MARKET_SIGNALS_CACHE_VERSION",
        "STRATEGY_PAGE_CACHE_VERSION",
    ),
)
def test_strategy_signature_changes_with_cache_version(monkeypatch, tmp_path, constant_name):
    _copy_strategy_sources(tmp_path)
    before = roi_contract.strategy_definition_signature(tmp_path)

    monkeypatch.setattr(roi_contract, constant_name, "signature-test-version")

    assert roi_contract.strategy_definition_signature(tmp_path) != before


def test_strategy_signature_ignores_adopted_strategy_document(tmp_path):
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    _copy_strategy_sources(first_root)
    _copy_strategy_sources(second_root)
    (first_root / "adopted_strategies.md").write_text("first", encoding="utf-8")
    (second_root / "adopted_strategies.md").write_text("second", encoding="utf-8")

    assert roi_contract.strategy_definition_signature(
        first_root
    ) == roi_contract.strategy_definition_signature(second_root)


def test_strategy_signature_is_deterministic_when_sources_are_missing(tmp_path):
    first = roi_contract.strategy_definition_signature(tmp_path)
    second = roi_contract.strategy_definition_signature(tmp_path)

    assert first == second
    assert first != "nosig"
    assert re.fullmatch(r"[0-9a-f]{10}", first)


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
    cache_only_start = source.index("def _l4_daily_stats_cache_only")
    cache_only_end = source.index("def _l4_daily_stats(", cache_only_start)
    cache_only = source[cache_only_start:cache_only_end]
    assert "load_roi_history_daily(" in cache_only
    assert 'day_d["_adopted_snapshot_source"] = "race_history"' in cache_only
    assert "MARKET_SIGNAL_ADOPTED_LEVELS = ROI_STRATEGY_KEYS" in source
    assert "operational_rows = [" in source
    assert 'bool(r.get("_adopted_from_market_signals_cache"))' in source
