from datetime import datetime, timedelta

from scripts import render_regular_scheduler as scheduler
from scripts import scrape_beforeinfo_live as live
from src.collectors import original_exhibition, tide


def test_beforeinfo_persists_progress_in_small_batches(monkeypatch):
    now = datetime(2026, 7, 22, 12, 0, tzinfo=scheduler.JST)
    due = [
        (f"20260722-01-{race_no:02d}", 1, race_no, now + timedelta(minutes=5))
        for race_no in range(1, 8)
    ]
    batches = []

    monkeypatch.setattr(live, "find_due_races", lambda *_args, **_kwargs: due)
    monkeypatch.setattr(live, "find_recent_incomplete_races", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        live,
        "scrape_one_race",
        lambda _stadium, race_no, _date: {"boats": [{"boat_number": race_no}]},
    )

    def fake_write_updates(updates, _now_iso, also_local):
        assert also_local is False
        batches.append([race_id for race_id, _page in updates])
        return {"supabase_rows": len(updates) * 6, "local_rows": 0, "races": len(updates)}

    monkeypatch.setattr(live, "write_updates", fake_write_updates)
    monkeypatch.setattr(tide, "refresh_tides_for_races", lambda _race_ids: {})
    monkeypatch.setattr(original_exhibition, "collect_for_races", lambda *_args, **_kwargs: {
        "races_targeted": 0,
        "pages_fetched": 0,
        "races_found": 0,
        "rows_inserted": 0,
    })
    monkeypatch.setattr(scheduler, "run_py", lambda *_args, **_kwargs: True)

    assert scheduler.run_beforeinfo(now) is True
    assert [len(batch) for batch in batches] == [6, 1]


def test_beforeinfo_fails_when_due_pages_cannot_be_saved(monkeypatch):
    now = datetime(2026, 7, 22, 12, 0, tzinfo=scheduler.JST)
    due = [("20260722-01-01", 1, 1, now + timedelta(minutes=5))]

    monkeypatch.setattr(live, "find_due_races", lambda *_args, **_kwargs: due)
    monkeypatch.setattr(live, "find_recent_incomplete_races", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(live, "scrape_one_race", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tide, "refresh_tides_for_races", lambda _race_ids: {})
    monkeypatch.setattr(original_exhibition, "collect_for_races", lambda *_args, **_kwargs: {
        "races_targeted": 0,
        "pages_fetched": 0,
        "races_found": 0,
        "rows_inserted": 0,
    })

    assert scheduler.run_beforeinfo(now) is False


def test_exhibition_refresh_daytime_lite_skips_heavy_prediction_rebuild(monkeypatch):
    from scripts import refresh_race_detail_after_exhibition as exhibition

    now = datetime(2026, 7, 22, 12, 0, tzinfo=exhibition.JST)
    due = [("20260722-01-01", 1, 1, now + timedelta(minutes=5))]
    calls = []

    monkeypatch.setenv("BOATRACE_RENDER_DAYTIME_LITE", "1")
    monkeypatch.setattr(live, "find_due_races", lambda *_args, **_kwargs: due)
    monkeypatch.setattr(live, "find_recent_incomplete_races", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(live, "_merge_due_races", lambda *groups: groups[0] if groups else [])
    monkeypatch.setattr(live, "scrape_one_race", lambda *_args, **_kwargs: {"boats": [{"boat_number": 1}]})
    monkeypatch.setattr(live, "write_updates", lambda updates, _now_iso, also_local: {"supabase_rows": len(updates), "local_rows": 0, "races": len(updates)})
    monkeypatch.setattr(exhibition, "_find_missing_original_exhibition_races", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(exhibition, "_collect_original_exhibition", lambda *_args, **_kwargs: {"races_targeted": 0, "pages_fetched": 0, "races_found": 0, "rows_inserted": 0})
    monkeypatch.setattr(tide, "refresh_tides_for_races", lambda race_ids: calls.append(list(race_ids)) or {"target_races": len(race_ids), "rows": len(race_ids), "stations": 1, "station_failures": 0})
    monkeypatch.setattr(exhibition, "_run_py", lambda args, timeout=900: (_ for _ in ()).throw(AssertionError(f"heavy rebuild should be skipped: {args}")))

    summary = exhibition.collect_live_exhibition("2026-07-22", now)

    assert summary["beforeinfo_races"] == 1
    assert calls == [["20260722-01-01"]]


def test_exhibition_refresh_daytime_lite_skips_market_signal_refresh(monkeypatch):
    from scripts import refresh_race_detail_after_exhibition as exhibition

    monkeypatch.setenv("BOATRACE_RENDER_DAYTIME_LITE", "1")
    summary = exhibition.refresh_market_signals_if_needed(
        "2026-07-22",
        {"beforeinfo_rows": 6, "original": {"rows_inserted": 0}},
        {"refreshed": 1},
    )

    assert summary["triggered"] is False
    assert summary["reason"] == "daytime-lite"
