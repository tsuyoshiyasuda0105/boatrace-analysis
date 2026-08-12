import os
from pathlib import Path

os.environ["DATABASE_URL"] = ""

from flask import Flask

from src.web import app as web_app
from scripts import prewarm_strategy_pages as prewarm
from scripts import render_regular_scheduler as scheduler


def test_browser_recompute_flag_does_not_allow_expensive_sql(monkeypatch):
    monkeypatch.delenv("BOATRACE_TASK_TRIGGER", raising=False)
    monkeypatch.delenv("BOATRACE_ALLOW_EXPENSIVE_WEB_RECOMPUTE", raising=False)

    app = Flask(__name__)
    with app.test_request_context("/member/strategy?recompute=1"):
        assert web_app._wants_recompute() is True
        assert web_app._effective_force_recompute() is False


def test_render_cron_recompute_flag_allows_expensive_sql(monkeypatch):
    monkeypatch.setenv("BOATRACE_TASK_TRIGGER", "render-prewarm")
    monkeypatch.delenv("BOATRACE_ALLOW_EXPENSIVE_WEB_RECOMPUTE", raising=False)

    app = Flask(__name__)
    with app.test_request_context("/member/strategy?recompute=1"):
        assert web_app._wants_recompute() is True
        assert web_app._effective_force_recompute() is True


def test_explicit_override_allows_expensive_sql_for_maintenance(monkeypatch):
    monkeypatch.delenv("BOATRACE_TASK_TRIGGER", raising=False)
    monkeypatch.setenv("BOATRACE_ALLOW_EXPENSIVE_WEB_RECOMPUTE", "1")

    app = Flask(__name__)
    with app.test_request_context("/member/strategy?recompute=1"):
        assert web_app._effective_force_recompute() is True


def test_market_signal_cache_miss_never_self_heals_in_web_worker():
    source = Path("src/web/app.py").read_text(encoding="utf-8")
    route_source = source.split("def market_signals_for_date():", 1)[1]
    route_source = route_source.split("@app.route", 1)[0]

    assert "market-signals cache missing; self-healing" not in route_source
    assert 'cache_only' in route_source
    assert 'cache_miss' in route_source


def test_cached_market_signals_are_returned_without_live_db_overlays():
    source = Path("src/web/app.py").read_text(encoding="utf-8")
    route_source = source.split("def market_signals_for_date():", 1)[1]
    route_source = route_source.split("@app.route", 1)[0]

    cache_return_source = route_source.split(
        "if not force_recompute:", 1
    )[1].split("# Web worker", 1)[0]
    assert "_read_best_market_signals_snapshot(" in cache_return_source
    assert "target_date," in cache_return_source
    assert "_market_json_response(cached_payload, cache_state)" in cache_return_source
    assert "_with_current_data_status(" not in cache_return_source
    assert "_apply_start_prediction_filters_to_cached_payload(" not in cache_return_source


def test_market_signals_do_not_use_a_second_flask_response_cache():
    source = Path("src/web/app.py").read_text(encoding="utf-8")
    route_prefix = source.split("def market_signals_for_date():", 1)[0]
    route_prefix = route_prefix.rsplit("@app.route", 1)[1]

    assert "@cached(" not in route_prefix


def test_daily_source_complete_requires_all_races_entries_and_predictions():
    assert scheduler.daily_source_complete(
        {"races": 144, "entries": 864, "predictions": 144}
    )
    assert not scheduler.daily_source_complete(
        {"races": 144, "entries": 858, "predictions": 144}
    )
    assert not scheduler.daily_source_complete(
        {"races": 144, "entries": 864, "predictions": 143}
    )
    assert not scheduler.daily_source_complete(
        {"races": 0, "entries": 0, "predictions": 0}
    )
    assert not scheduler.daily_source_complete(
        {
            "races": 144,
            "entries": 864,
            "detail_entries": 858,
            "predictions": 144,
        }
    )


def test_signal_refresh_uses_one_task_slot_per_five_minutes(monkeypatch):
    attempted = []
    recorded = []
    run_calls = []
    monkeypatch.setattr(
        scheduler,
        "task_attempt_exists",
        lambda task, run_date: attempted.append((task, run_date)) or False,
    )
    monkeypatch.setattr(
        scheduler,
        "signal_refresh_recently_running",
        lambda _now: False,
    )
    monkeypatch.setattr(
        scheduler,
        "record_task",
        lambda *args, **kwargs: recorded.append((args, kwargs)),
    )
    monkeypatch.setattr(
        scheduler,
        "run_py",
        lambda args, timeout: run_calls.append((args, timeout)) or True,
    )
    monkeypatch.setattr(scheduler, "run_program_source_gate", lambda _date, **_kwargs: True)

    now = scheduler.datetime(2026, 7, 21, 10, 37, tzinfo=scheduler.JST)
    assert scheduler.run_signal_refresh_slot(now)
    assert attempted == [("render_signal_refresh_10_7", "2026-07-21")]
    assert recorded[0][0][:3] == ("render_signal_refresh_10_7", "2026-07-21", "running")
    assert recorded[-1][0][:3] == ("render_signal_refresh_10_7", "2026-07-21", "success")
    assert run_calls == [
        (
            ["scripts/build_derived_start_stats.py", "--from", "2026-07-21", "--to", "2026-07-21"],
            1800,
        ),
        (
            ["scripts/prewarm_strategy_pages.py", "--mode", "signals", "--date", "2026-07-21"],
            1800,
        )
    ]


def test_signal_refresh_skips_when_previous_slot_is_still_running(monkeypatch):
    monkeypatch.setattr(scheduler, "task_attempt_exists", lambda *_args: False)
    monkeypatch.setattr(scheduler, "signal_refresh_recently_running", lambda _now: True)
    monkeypatch.setattr(
        scheduler,
        "run_py",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not overlap")),
    )
    monkeypatch.setattr(
        scheduler,
        "record_task",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not mark a skipped slot")),
    )

    now = scheduler.datetime(2026, 7, 21, 10, 40, tzinfo=scheduler.JST)
    assert scheduler.run_signal_refresh_slot(now)


def test_signal_refresh_lock_reads_recent_running_task(monkeypatch):
    class _Cursor:
        def fetchone(self):
            return ("render_signal_refresh_10_7", "2026-07-21T10:37:00")

    class _Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, *_args):
            return _Cursor()

    monkeypatch.setattr(scheduler, "db_connect", lambda: _Connection())
    now = scheduler.datetime(2026, 7, 21, 10, 40, tzinfo=scheduler.JST)
    assert scheduler.signal_refresh_recently_running(now)


def test_lite_daytime_bootstrap_runs_full_snapshot_once(monkeypatch):
    calls = []

    monkeypatch.setattr(
        scheduler,
        "task_success_exists",
        lambda task, _run_date: False if task in {"render_lite_daytime_bootstrap", "render_detail_tags_today"} else True,
    )
    monkeypatch.setattr(
        scheduler,
        "daily_source_counts",
        lambda _run_date: {"races": 10, "entries": 60, "predictions": 10},
    )
    monkeypatch.setattr(
        scheduler,
        "run_signal_refresh_slot",
        lambda _now, **kwargs: calls.append(("signal_refresh", kwargs)) or True,
    )
    monkeypatch.setattr(
        scheduler,
        "run_py",
        lambda args, timeout: calls.append((tuple(args), timeout)) or True,
    )
    monkeypatch.setattr(
        scheduler,
        "run_top_page_snapshot",
        lambda _now, *, lightweight, environment_only=False: calls.append(
            ("snapshot", lightweight, environment_only)
        ) or True,
    )
    monkeypatch.setattr(
        scheduler,
        "record_task",
        lambda task, _run_date, status, detail=None: calls.append(("record", task, status, detail)),
    )

    now = scheduler.datetime(2026, 7, 21, 8, 5, tzinfo=scheduler.JST)
    assert scheduler.run_lite_daytime_bootstrap(now)
    assert ("signal_refresh", {"source_gate_verified": True}) in calls
    assert "source_recovery" not in calls
    assert (("scripts/prewarm_race_detail_tags.py", "--date", "2026-07-21"), 900) in calls
    assert (("scripts/prewarm_race_detail_pages.py", "--date", "2026-07-21"), 1800) in calls
    assert ("snapshot", False, False) in calls
    assert any(item[:3] == ("record", "render_lite_daytime_bootstrap", "success") for item in calls if isinstance(item, tuple))


def test_lite_daytime_bootstrap_revalidates_stale_gate_after_rows_complete(monkeypatch):
    calls = []
    monkeypatch.setattr(
        scheduler,
        "task_success_exists",
        lambda task, _run_date: task not in {
            "render_lite_daytime_bootstrap",
            "render_program_source_gate_v1",
        },
    )
    monkeypatch.setattr(
        scheduler,
        "daily_source_counts",
        lambda _run_date: {"races": 10, "entries": 60, "predictions": 10},
    )
    monkeypatch.setattr(
        scheduler,
        "run_program_source_gate",
        lambda run_date, **kwargs: calls.append(("source_gate", run_date, kwargs)) or True,
    )
    monkeypatch.setattr(
        scheduler,
        "run_signal_refresh_slot",
        lambda _now, **kwargs: calls.append(("signal_refresh", kwargs)) or True,
    )
    monkeypatch.setattr(scheduler, "run_top_page_snapshot", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(scheduler, "record_task", lambda *_args, **_kwargs: None)

    now = scheduler.datetime(2026, 7, 21, 8, 5, tzinfo=scheduler.JST)
    assert scheduler.run_lite_daytime_bootstrap(now)
    assert calls[:2] == [
        (
            "source_gate",
            "2026-07-21",
            {"allow_official_fallback": True},
        ),
        ("signal_refresh", {"source_gate_verified": True}),
    ]


def test_lite_daytime_bootstrap_stops_when_revalidated_gate_is_not_ready(monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler, "task_success_exists", lambda *_args: False)
    monkeypatch.setattr(
        scheduler,
        "daily_source_counts",
        lambda _run_date: {"races": 10, "entries": 60, "predictions": 10},
    )
    monkeypatch.setattr(
        scheduler,
        "run_program_source_gate",
        lambda _run_date, **_kwargs: False,
    )
    monkeypatch.setattr(
        scheduler,
        "run_signal_refresh_slot",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not build before gate success")),
    )
    monkeypatch.setattr(
        scheduler,
        "record_task",
        lambda task, _run_date, status, detail=None: calls.append((task, status, detail)),
    )

    now = scheduler.datetime(2026, 7, 21, 8, 5, tzinfo=scheduler.JST)
    assert scheduler.run_lite_daytime_bootstrap(now) is False
    assert calls[-1] == (
        "render_lite_daytime_bootstrap",
        "failure",
        "source_gate_not_ready",
    )


def test_lite_daytime_bootstrap_stops_when_sources_remain_incomplete(monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler, "task_success_exists", lambda *_args: False)
    monkeypatch.setattr(scheduler, "run_morning_catchup_if_needed", lambda _now: False)
    monkeypatch.setattr(
        scheduler,
        "daily_source_counts",
        lambda _run_date: {"races": 10, "entries": 54, "predictions": 9},
    )
    monkeypatch.setattr(
        scheduler,
        "run_signal_refresh_slot",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not build from partial data")),
    )
    monkeypatch.setattr(
        scheduler,
        "record_task",
        lambda task, _run_date, status, detail=None: calls.append((task, status, detail)),
    )

    now = scheduler.datetime(2026, 7, 21, 8, 10, tzinfo=scheduler.JST)
    assert scheduler.run_lite_daytime_bootstrap(now) is False
    assert calls == [
        (
            "render_lite_daytime_bootstrap",
            "failure",
            "source_incomplete={'races': 10, 'entries': 54, 'predictions': 9}",
        )
    ]


def test_lite_daytime_bootstrap_stops_when_signal_gate_fails(monkeypatch):
    calls = []
    monkeypatch.setattr(
        scheduler,
        "task_success_exists",
        lambda task, _run_date: task == "render_program_source_gate_v1",
    )
    monkeypatch.setattr(
        scheduler,
        "daily_source_counts",
        lambda _run_date: {"races": 10, "entries": 60, "predictions": 10},
    )
    monkeypatch.setattr(
        scheduler,
        "run_signal_refresh_slot",
        lambda _now, **_kwargs: False,
    )
    monkeypatch.setattr(
        scheduler,
        "run_py",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("must not generate from an unverified source")
        ),
    )
    monkeypatch.setattr(
        scheduler,
        "run_top_page_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("must not publish a full snapshot")
        ),
    )
    monkeypatch.setattr(
        scheduler,
        "record_task",
        lambda task, _run_date, status, detail=None: calls.append(
            (task, status, detail)
        ),
    )

    now = scheduler.datetime(2026, 7, 21, 8, 10, tzinfo=scheduler.JST)
    assert scheduler.run_lite_daytime_bootstrap(now) is False
    assert calls[-1] == (
        "render_lite_daytime_bootstrap",
        "failure",
        "signal_refresh_failed",
    )


def test_main_returns_failure_when_lite_bootstrap_fails(monkeypatch):
    from contextlib import contextmanager

    @contextmanager
    def unlocked():
        yield True

    now = scheduler.datetime(2026, 7, 21, 10, 10, tzinfo=scheduler.JST)
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    monkeypatch.setattr(scheduler, "jst_now", lambda: now)
    monkeypatch.setattr(scheduler, "ensure_task_runs_table", lambda: None)
    monkeypatch.setattr(scheduler, "_regular_run_lock", unlocked)
    monkeypatch.setattr(scheduler, "render_daytime_lite_mode", lambda: True)
    monkeypatch.setattr(scheduler, "run_py", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(scheduler, "run_lite_daytime_bootstrap", lambda _now: False)
    monkeypatch.setattr(
        scheduler,
        "run_top_page_snapshot",
        lambda *_args, **_kwargs: True,
    )

    assert scheduler.main() == 1


def test_main_skips_safely_when_previous_regular_run_is_active(monkeypatch):
    from contextlib import contextmanager

    @contextmanager
    def locked():
        yield False

    monkeypatch.setattr(scheduler, "_regular_run_lock", locked)
    monkeypatch.setattr(
        scheduler,
        "jst_now",
        lambda: (_ for _ in ()).throw(AssertionError("locked run must do no work")),
    )

    assert scheduler.main() == 0


def test_regular_run_lock_uses_postgres_advisory_lock(monkeypatch):
    calls = []

    class _Cursor:
        def fetchone(self):
            return (True,)

    class _Connection:
        _kind = "postgres"

        def execute(self, sql, params):
            calls.append((sql, params))
            return _Cursor()

        def close(self):
            calls.append("close")

    monkeypatch.setattr(scheduler, "db_connect", lambda: _Connection())

    with scheduler._regular_run_lock() as locked:
        assert locked is True

    assert "pg_try_advisory_lock" in calls[0][0]
    assert calls[0][1] == (scheduler.REGULAR_RUN_LOCK_NAME,)
    assert "pg_advisory_unlock" in calls[1][0]
    assert calls[-1] == "close"


def test_roi_history_uses_one_task_slot_per_twelve_hours(monkeypatch):
    attempted = []
    monkeypatch.setattr(
        scheduler,
        "task_attempt_exists",
        lambda _task, _run_date: False,
    )
    monkeypatch.setattr(
        scheduler,
        "task_success_exists",
        lambda task, run_date: attempted.append((task, run_date)) or True,
    )

    morning = scheduler.datetime(2026, 7, 21, 10, 37, tzinfo=scheduler.JST)
    evening = scheduler.datetime(2026, 7, 21, 18, 2, tzinfo=scheduler.JST)
    assert scheduler.run_roi_history_slot(morning)
    assert scheduler.run_roi_history_slot(evening)
    assert attempted == [
        ("render_roi_history_00", "2026-07-21"),
        ("render_roi_history_12", "2026-07-21"),
    ]


def test_roi_history_does_not_retry_failed_slot_every_five_minutes(monkeypatch):
    calls = []
    monkeypatch.setattr(
        scheduler,
        "task_attempt_exists",
        lambda task, run_date: calls.append(("attempt", task, run_date)) or True,
    )
    monkeypatch.setattr(
        scheduler,
        "task_success_exists",
        lambda *_args: (_ for _ in ()).throw(AssertionError("attempt check should win")),
    )
    monkeypatch.setattr(
        scheduler,
        "run_py",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not rerun history")),
    )

    now = scheduler.datetime(2026, 7, 21, 14, 43, tzinfo=scheduler.JST)
    assert scheduler.run_roi_history_slot(now)
    assert calls == [("attempt", "render_roi_history_12", "2026-07-21")]


def test_roi_history_only_runs_in_two_narrow_windows():
    assert scheduler.should_run_roi_history_slot(
        scheduler.datetime(2026, 7, 21, 0, 0, tzinfo=scheduler.JST)
    )
    assert scheduler.should_run_roi_history_slot(
        scheduler.datetime(2026, 7, 21, 12, 4, tzinfo=scheduler.JST)
    )
    assert not scheduler.should_run_roi_history_slot(
        scheduler.datetime(2026, 7, 21, 14, 43, tzinfo=scheduler.JST)
    )
    assert not scheduler.should_run_roi_history_slot(
        scheduler.datetime(2026, 7, 21, 12, 5, tzinfo=scheduler.JST)
    )


def test_market_signals_keep_a_stable_last_good_snapshot():
    source = Path("src/web/app.py").read_text(encoding="utf-8")
    route_source = source.split("def market_signals_for_date():", 1)[1]
    route_source = route_source.split("@app.route", 1)[0]

    assert "def _market_signals_last_good_cache_key" in source
    assert '"last-good"' in route_source
    assert "_market_signals_last_good_cache_key(target_date)" in route_source
    assert (
        "_write_json_cache(_market_signals_last_good_cache_key(target_date), payload)"
        in route_source
    )


def test_market_signals_snapshot_merges_adopted_rows_from_compatible_cache(monkeypatch):
    current_key = web_app._market_signals_cache_key("2026-08-01")
    compat_key = "market_signals:v27:old-signature:2026-08-01"
    current_payload = {
        "date": "2026-08-01",
        "cache_version": "v27",
        "signals": {
            "20260801-08-09": {
                "race_id": "20260801-08-09",
                "is_positive_ev": True,
                "l4": {
                    "level": "tokoname_coursefit_boat3_general_win",
                    "label": "常滑 コース適合 3号艇単勝",
                },
            }
        },
    }
    compat_payload = {
        "date": "2026-08-01",
        "cache_version": "v27",
        "signals": {
            "20260801-05-10": {
                "race_id": "20260801-05-10",
                "is_positive_ev": True,
                "l4": {
                    "level": "tamagawa_13_weak_sashi2_exa",
                    "label": "多摩川13 差し弱2型",
                    "bet": "2連単 1-3",
                },
            }
        },
        "race_badges": {"20260801-05-10": {"market": {"label": "多摩川13"}}},
    }

    monkeypatch.setattr(
        web_app,
        "_read_json_cache",
        lambda key, _ttl: current_payload if key == current_key else None,
    )
    monkeypatch.setattr(
        web_app,
        "_read_json_cache_stale",
        lambda key: compat_payload if key == compat_key else None,
    )
    monkeypatch.setattr(
        web_app,
        "_market_signals_compat_cache_keys",
        lambda target_date: [compat_key] if target_date == "2026-08-01" else [],
    )

    payload, cache_state = web_app._read_best_market_signals_snapshot(
        "2026-08-01",
        adopted_levels={
            "tokoname_coursefit_boat3_general_win",
            "tamagawa_13_weak_sashi2_exa",
        },
        cache_ttl=15,
    )

    assert cache_state == "merged-compat"
    assert set(payload["signals"]) == {"20260801-08-09", "20260801-05-10"}
    assert payload["signals"]["20260801-05-10"]["l4"]["bet"] == "2連単 1-3"
    assert payload["cache_recovered_adopted_races"] == ["20260801-05-10"]


def test_tamagawa_13_signal_is_an_adopted_roi_strategy():
    source = Path("src/web/app.py").read_text(encoding="utf-8")
    assert '"tamagawa_13_weak_sashi2_exa"' in source
    payload = {
        "date": "2026-08-01",
        "signals": {
            "20260801-05-10": {
                "l4": {"level": "tamagawa_13_weak_sashi2_exa"}
            }
        },
    }

    assert web_app._market_signals_adopted_race_ids(
        payload,
        {"tamagawa_13_weak_sashi2_exa"},
    ) == {"20260801-05-10"}


def test_today_races_payload_falls_back_to_last_good_snapshot():
    source = Path("src/web/app.py").read_text(encoding="utf-8")
    helper_source = source.split("def _market_pick_rows_for_display(", 1)[1]
    helper_source = helper_source.split("@app.route", 1)[0]

    assert "_read_best_market_signals_snapshot(" in helper_source
    assert "adopted_levels=set(MARKET_SIGNAL_ADOPTED_LEVELS)" in helper_source
    shared_source = source.split("def _read_best_market_signals_snapshot(", 1)[1]
    shared_source = shared_source.split("def _parse_market_signal_bets_for_roi", 1)[0]
    assert "_market_signals_last_good_cache_key(target_date)" in shared_source
    assert "_market_signals_compat_cache_keys(target_date)" in shared_source


def test_pending_market_signals_skip_badge_hydration():
    payload = {
        "date": "2026-07-21",
        "data_status": {"cache_miss": True, "cache_only": True},
        "race_badges": {},
        "signals": {},
    }

    assert web_app._hydrate_market_race_badges(payload, "2026-07-21") is payload


def test_accident_self_heal_skips_when_snapshot_is_current(monkeypatch):
    monkeypatch.setattr(scheduler, "latest_completed_results_date", lambda: "2026-07-20")
    monkeypatch.setattr(scheduler, "latest_accident_snapshot_state", lambda: ("2026-07-20", "2026-07-20"))
    monkeypatch.setattr(
        scheduler,
        "run_accident_full_refresh",
        lambda _target: (_ for _ in ()).throw(AssertionError("must not rebuild")),
    )

    now = scheduler.datetime(2026, 7, 21, 9, 0, tzinfo=scheduler.JST)
    assert scheduler.run_accident_self_heal(now)


def test_accident_self_heal_rebuilds_full_period_and_verifies_snapshot(monkeypatch):
    snapshots = iter([("2026-07-19", "2026-07-19"), ("2026-07-20", "2026-07-20")])
    monkeypatch.setattr(scheduler, "latest_completed_results_date", lambda: "2026-07-20")
    rebuilt = []
    recorded = []
    monkeypatch.setattr(scheduler, "latest_accident_snapshot_state", lambda: next(snapshots))
    monkeypatch.setattr(scheduler, "task_attempt_exists", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        scheduler,
        "run_accident_full_refresh",
        lambda target: rebuilt.append(target) or True,
    )
    monkeypatch.setattr(
        scheduler,
        "record_task",
        lambda *args, **kwargs: recorded.append((args, kwargs)),
    )

    now = scheduler.datetime(2026, 7, 21, 9, 0, tzinfo=scheduler.JST)
    assert scheduler.run_accident_self_heal(now)
    assert rebuilt == ["2026-07-20"]
    assert recorded[0][0][:3] == (
        "render_accident_refresh_slot_09",
        "2026-07-21",
        "running",
    )
    assert recorded[1][0][:3] == (
        "render_accident_refresh_slot_09",
        "2026-07-21",
        "success",
    )
    assert recorded[2][0][:3] == (
        "render_accident_refresh",
        "2026-07-20",
        "success",
    )


def test_accident_full_refresh_rebuilds_from_period_start(monkeypatch):
    calls = []
    monkeypatch.setattr(
        scheduler,
        "run_accident_rebuild",
        lambda date_from, date_to: calls.append((date_from, date_to)) or True,
    )
    monkeypatch.setattr(
        scheduler,
        "run_accident_rank_snapshot",
        lambda target: calls.append(("snapshot", target)) or True,
    )

    assert scheduler.run_accident_full_refresh("2026-07-20")
    assert calls == [
        (scheduler.accident_period_start(scheduler.datetime(2026, 7, 20, tzinfo=scheduler.JST)), "2026-07-20"),
        ("snapshot", "2026-07-20"),
    ]


def test_scheduler_never_rebuilds_accident_stats_from_one_day_only():
    source = Path("scripts/render_regular_scheduler.py").read_text(encoding="utf-8")
    assert "run_accident_rebuild(today, today)" not in source


def test_scheduler_runs_accident_refresh_only_in_0730_window():
    source = Path("scripts/render_regular_scheduler.py").read_text(encoding="utf-8")
    main_source = source.split("def main() -> int:", 1)[1]
    live_loop = main_source.split("# End-of-day refresh", 1)[0]
    nightly_source = source.split("def run_nightly", 1)[1].split("def main", 1)[0]

    assert "now.hour == 7 and 30 <= now.minute < 35" in live_loop
    assert live_loop.count("run_accident_self_heal(now)") == 1
    assert "run_accident_full_refresh(today)" in nightly_source


def test_accident_watch_map_uses_latest_period_end_before_target(monkeypatch):
    web_app._accident_watch_map.cache_clear()
    web_app._preferred_accident_source.cache_clear()
    calls = []

    class _Cursor:
        def __init__(self, *, one=None, all_rows=None):
            self.one = one
            self.all_rows = all_rows or []

        def fetchone(self):
            return self.one

        def fetchall(self):
            return self.all_rows

    class _Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql, params=()):
            calls.append((sql, params))
            if "racer_accident_external_snapshots" in sql:
                return _Cursor(one=None)
            if "SELECT MAX(period_end)" in sql:
                return _Cursor(one=("2026-08-01",))
            return _Cursor(all_rows=[(1234, 0.62, 14, 22, None)])

    monkeypatch.setattr(web_app, "db_connect", lambda: _Connection())
    got = web_app._accident_watch_map("2026-05-01", "2026-08-02", (1234,))

    assert got[1234] == {
        "rate": 0.62,
        "points": 14,
        "starts": 22,
        "flying_count": None,
        "late_count": None,
    }
    sql, params = calls[-1]
    assert "period_end = ?" in sql
    assert "MAX(accident_rate)" not in sql
    assert params == ("2026-05-01", "2026-08-01", 1234)


def test_accident_rate_queries_do_not_mix_period_rows_with_max_rate():
    source = Path("src/web/app.py").read_text(encoding="utf-8")
    assert "MAX(accident_rate)" not in source
    assert "MAX(accident_points)" not in source
    assert "period_end < ?" in source
    assert "period_end < r.race_date" in source


def test_accident_rebuild_updates_period_even_without_new_accident_events():
    source = Path("scripts/rebuild_racer_accident_stats.py").read_text(encoding="utf-8")
    assert "periods = affected_class_periods(date_from, date_to)" in source
    assert "effective_period_end = min(period_end, date_to)" in source


def test_accident_rank_snapshot_uses_only_latest_period_end_rows():
    source = Path("scripts/cache_racer_accident_rank_snapshot.py").read_text(encoding="utf-8")
    build_source = source.split("def build_snapshot", 1)[1].split("def main", 1)[0]
    assert "period_end = str(period_row[2])" in build_source
    assert "AND s.period_end = ?" in build_source
    assert "(class_as_of, period_start, period_end, source_kind, RULE_VERSION)" in build_source


def test_roi_cache_self_heal_skips_current_cache(monkeypatch):
    monkeypatch.setattr(scheduler, "roi_daily_cache_needs_repair", lambda _date: False)
    monkeypatch.setattr(
        scheduler,
        "run_py",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not rebuild")),
    )
    now = scheduler.datetime(2026, 7, 21, 9, 0, tzinfo=scheduler.JST)
    assert scheduler.run_roi_daily_self_heal(now)


def test_roi_cache_repair_detects_old_select_version(monkeypatch):
    class _Cursor:
        def fetchone(self):
            return ('{"_adopted_daily_select_version":"adopted_daily_select_v30"}',)

    class _Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, *_args):
            return _Cursor()

    monkeypatch.setattr(scheduler, "db_connect", lambda: _Connection())
    assert scheduler.roi_daily_cache_needs_repair("2026-07-20")


def test_roi_cache_self_heal_repairs_and_verifies(monkeypatch):
    checks = iter([True, False])
    calls = []
    records = []
    monkeypatch.setattr(scheduler, "roi_daily_cache_needs_repair", lambda _date: next(checks))
    monkeypatch.setattr(
        scheduler,
        "run_py",
        lambda args, timeout: calls.append((args, timeout)) or True,
    )
    monkeypatch.setattr(
        scheduler,
        "record_task",
        lambda *args, **kwargs: records.append((args, kwargs)),
    )
    now = scheduler.datetime(2026, 7, 21, 9, 0, tzinfo=scheduler.JST)
    assert scheduler.run_roi_daily_self_heal(now)
    assert calls == [
        (["scripts/prewarm_strategy_pages.py", "--mode", "daily-reconcile", "--date", "2026-07-21"], 1800),
        (["scripts/build_derived_start_stats.py", "--from", "2026-07-20", "--to", "2026-07-20"], 1800),
        (["scripts/backfill_accident_dent_daily_cache.py", "--from", "2026-07-20", "--to", "2026-07-20"], 900),
    ]
    assert records[0][0][:3] == (
        "render_roi_daily_reconcile",
        "2026-07-20",
        "success",
    )


def test_daily_reconcile_targets_yesterday_before_roi_page():
    targets = prewarm.build_targets("daily-reconcile", scheduler.datetime(2026, 7, 21).date())
    assert targets[0] == "/api/market-signals?date=2026-07-20&recompute=1"
    assert targets[1] == "/member/strategy?from=2026-07-20&to=2026-07-21&recompute=1"


def test_nightly_prewarms_tomorrow_market_signals(monkeypatch):
    calls = []
    monkeypatch.setattr(
        scheduler,
        "run_py",
        lambda args, timeout: calls.append((args, timeout)) or True,
    )
    monkeypatch.setattr(scheduler, "run_tides", lambda _now: True)
    monkeypatch.setattr(scheduler, "run_program_source_gate", lambda _date, **_kwargs: True)
    monkeypatch.setattr(
        scheduler,
        "daily_source_counts",
        lambda _date: {"races": 12, "entries": 72, "predictions": 12},
    )
    monkeypatch.setattr(scheduler, "run_accident_full_refresh", lambda _today: True)
    monkeypatch.setattr(scheduler, "run_db_maintenance", lambda: True)

    now = scheduler.datetime(2026, 7, 21, 23, 30, tzinfo=scheduler.JST)
    assert scheduler.run_nightly(now)
    assert (
        [
            "scripts/prewarm_strategy_pages.py",
            "--mode",
            "signals",
            "--date",
            "2026-07-22",
        ],
        1800,
    ) in calls


def test_nightly_retries_when_tomorrow_source_is_not_ready(monkeypatch):
    calls = []
    monkeypatch.setattr(
        scheduler,
        "run_py",
        lambda args, timeout: calls.append((args, timeout)) or True,
    )
    monkeypatch.setattr(scheduler, "run_tides", lambda _now: True)
    monkeypatch.setattr(scheduler, "run_program_source_gate", lambda _date, **_kwargs: True)
    monkeypatch.setattr(
        scheduler,
        "daily_source_counts",
        lambda _date: {"races": 0, "entries": 0, "predictions": 0},
    )

    now = scheduler.datetime(2026, 7, 21, 23, 30, tzinfo=scheduler.JST)
    assert scheduler.run_nightly(now) is False
    assert not any(
        args[:3] == ["scripts/prewarm_strategy_pages.py", "--mode", "signals"]
        for args, _timeout in calls
    )
