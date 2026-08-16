from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

import scripts.backfill_original_exhibition as backfill


JST = ZoneInfo("Asia/Tokyo")


def test_plan_mode_finds_true_missing_without_collecting(monkeypatch, capsys):
    find_calls = []
    collect_calls = []

    def fake_find(now, **kwargs):
        find_calls.append((now, kwargs))
        return [("race-5", 5, 1, datetime(2026, 8, 16, 12, tzinfo=JST))]

    monkeypatch.setattr(backfill, "find_missing_original_exhibition_races", fake_find)
    monkeypatch.setattr(
        backfill.original_exhibition,
        "collect_for_races",
        lambda *_args, **_kwargs: collect_calls.append((_args, _kwargs)),
    )

    assert backfill.main(["--date", "2026-08-16", "--stadiums", "5", "--limit", "1"]) == 0

    assert collect_calls == []
    assert find_calls[0][1]["stadiums"] == {5}
    assert find_calls[0][1]["limit"] == 1
    assert "plan only: no HTTP request" in capsys.readouterr().out


def test_execute_is_sequential_bounded_and_uses_existing_collector(monkeypatch):
    due = [
        ("race-5-1", 5, 1, datetime(2026, 8, 16, 12, tzinfo=JST)),
        ("race-13-1", 13, 1, datetime(2026, 8, 16, 12, tzinfo=JST)),
    ]
    calls = []
    monkeypatch.setattr(backfill, "find_targets", lambda *_args: due)

    def fake_collect(target_date, races, **kwargs):
        calls.append((target_date, races, kwargs))
        return {"races_targeted": 2, "pages_fetched": 2, "races_found": 1, "rows_inserted": 6}

    monkeypatch.setattr(backfill.original_exhibition, "collect_for_races", fake_collect)

    assert backfill.main(
        ["--date", "2026-08-16", "--stadiums", "5", "13", "--limit", "2", "--execute"]
    ) == 0

    assert calls == [
        (
            date(2026, 8, 16),
            [("race-5-1", 5, 1), ("race-13-1", 13, 1)],
            {
                "force": False,
                "save_html": True,
                "stadiums": {5, 13},
                "pattern_limit": 1,
            },
        )
    ]


@pytest.mark.parametrize("stadium", [3, 9, 16])
def test_disabled_source_cannot_be_requested(stadium):
    with pytest.raises(SystemExit):
        backfill.main(["--date", "2026-08-16", "--stadiums", str(stadium)])


def test_limits_prevent_unbounded_requests():
    with pytest.raises(SystemExit):
        backfill.main(["--date", "2026-08-16", "--limit", "121"])
    with pytest.raises(SystemExit):
        backfill.main(["--date", "2026-08-16", "--pattern-limit", "3"])
