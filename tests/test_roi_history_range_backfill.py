import pytest

from scripts import backfill_roi_history_range as backfill_range


def test_roi_history_range_backfill_runs_recompute_then_import(monkeypatch):
    calls = []

    monkeypatch.setattr(backfill_range, "_today", lambda: backfill_range.date(2026, 8, 1))
    monkeypatch.setattr(
        backfill_range,
        "_run_py",
        lambda args, *, env: calls.append(args),
    )

    monkeypatch.setattr(
        "sys.argv",
        [
            "backfill_roi_history_range.py",
            "--from",
            "2026-07-30",
            "--to",
            "2026-07-31",
        ],
    )

    assert backfill_range.main() == 0
    assert calls == [
        ["scripts/prewarm_strategy_pages.py", "--mode", "signals", "--date", "2026-07-30"],
        ["scripts/backfill_roi_race_history.py", "--from", "2026-07-30", "--to", "2026-07-30"],
        ["scripts/prewarm_strategy_pages.py", "--mode", "signals", "--date", "2026-07-31"],
        ["scripts/backfill_roi_race_history.py", "--from", "2026-07-31", "--to", "2026-07-31"],
    ]


def test_roi_history_range_backfill_rejects_today_and_future(monkeypatch):
    monkeypatch.setattr(backfill_range, "_today", lambda: backfill_range.date(2026, 8, 1))
    monkeypatch.setattr(
        "sys.argv",
        [
            "backfill_roi_history_range.py",
            "--from",
            "2026-07-31",
            "--to",
            "2026-08-01",
        ],
    )

    with pytest.raises(SystemExit, match="past-only"):
        backfill_range.main()
