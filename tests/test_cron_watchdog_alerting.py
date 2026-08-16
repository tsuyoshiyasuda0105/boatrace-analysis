from contextlib import contextmanager
from datetime import datetime

import pytest

from scripts import check_external_accident_snapshot as accident
from scripts import odds_scheduler_render as odds
from scripts import refresh_race_detail_after_exhibition as exhibition
from scripts import render_regular_scheduler as regular


def _now(hour: int = 10) -> datetime:
    return datetime(2026, 8, 16, hour, 10, tzinfo=regular.JST)


def _snapshot(**overrides):
    value = {
        "detail_cache": {"races": 10, "covered": 10},
        "yesterday": "2026-08-15",
        "missing_results": None,
        "repeated_failures": [],
        "pool_events": 0,
        "pool_recent": [],
        "stale_running": [],
    }
    value.update(overrides)
    return value


def test_regular_terminal_failure_notifies(monkeypatch):
    @contextmanager
    def unlocked():
        yield True

    notices = []
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    monkeypatch.setattr(regular, "_regular_run_lock", unlocked)
    monkeypatch.setattr(regular, "log_deploy_revision", lambda *_a: None)
    monkeypatch.setattr(regular, "jst_now", lambda: _now())
    monkeypatch.setattr(regular, "ensure_task_runs_table", lambda: None)
    monkeypatch.setattr(regular, "reap_stale_task_runs", lambda _now: 0)
    monkeypatch.setattr(regular, "run_cron_watchdog", lambda *_a, **_k: True)
    monkeypatch.setattr(regular, "render_daytime_lite_mode", lambda: True)
    monkeypatch.setattr(regular, "run_py", lambda *_a, **_k: True)
    monkeypatch.setattr(regular, "run_lite_daytime_bootstrap", lambda _now: False)
    monkeypatch.setattr(regular, "run_top_page_snapshot", lambda *_a, **_k: True)
    monkeypatch.setattr(
        regular,
        "notify_cron_failure",
        lambda job, message, **kwargs: notices.append((job, message, kwargs)),
    )

    assert regular.main() == 1
    assert notices[0][0] == regular.REGULAR_CRON_JOB_NAME


def test_odds_terminal_failure_notifies_and_mail_failure_is_nonfatal(monkeypatch):
    @contextmanager
    def unlocked():
        yield True

    notices = []
    monkeypatch.setattr(odds, "odds_lock", unlocked)
    monkeypatch.setattr(odds, "log_deploy_revision", lambda *_a: None)
    monkeypatch.setattr(odds.base, "main", lambda: 1)
    monkeypatch.setattr(
        odds,
        "notify_cron_failure",
        lambda job, message, **kwargs: notices.append((job, message, kwargs)),
    )
    assert odds.main() == 1
    assert notices[0][0] == odds.CRON_JOB_NAME

    monkeypatch.setattr(
        odds,
        "notify_cron_failure",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("mail down")),
    )
    assert odds.main() == 1


def test_odds_nonzero_system_exit_notifies_and_preserves_exit(monkeypatch):
    @contextmanager
    def unlocked():
        yield True

    notices = []
    monkeypatch.setattr(odds, "odds_lock", unlocked)
    monkeypatch.setattr(odds, "log_deploy_revision", lambda *_a: None)
    monkeypatch.setattr(
        odds.base,
        "main",
        lambda: (_ for _ in ()).throw(SystemExit(2)),
    )
    monkeypatch.setattr(
        odds,
        "notify_cron_failure",
        lambda job, message, **kwargs: notices.append((job, message, kwargs)),
    )

    with pytest.raises(SystemExit) as raised:
        odds.main()
    assert raised.value.code == 2
    assert notices[0][0] == odds.CRON_JOB_NAME


@pytest.mark.parametrize("module", [exhibition, accident])
def test_wrapped_cron_terminal_failure_notifies(monkeypatch, module):
    notices = []
    monkeypatch.setattr(module, "_main_impl", lambda: 1)
    monkeypatch.setattr(
        module,
        "notify_cron_failure",
        lambda job, message, **kwargs: notices.append((job, message, kwargs)),
    )
    assert module.main() == 1
    assert notices[0][0] == module.CRON_JOB_NAME


def test_wrapped_cron_exception_notifies_without_masking_original(monkeypatch):
    monkeypatch.setattr(
        exhibition,
        "_main_impl",
        lambda: (_ for _ in ()).throw(ValueError("collector failed")),
    )
    monkeypatch.setattr(
        exhibition,
        "notify_cron_failure",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("mail down")),
    )
    with pytest.raises(ValueError, match="collector failed"):
        exhibition.main()


def test_watchdog_repairs_low_current_version_cache_and_skips_sufficient(monkeypatch):
    heals = []
    statuses = []
    alerts = []
    monkeypatch.setattr(
        regular,
        "_watchdog_snapshot",
        lambda _now: _snapshot(detail_cache={"races": 10, "covered": 0}),
    )
    monkeypatch.setattr(
        regular,
        "run_detail_pages_selfheal",
        lambda _now: heals.append(_now) or True,
    )
    monkeypatch.setattr(
        regular,
        "race_detail_page_cache_coverage",
        lambda _date: {"races": 10, "covered": 6},
    )
    monkeypatch.setattr(regular, "_write_watchdog_status", lambda *args: statuses.append(args))
    monkeypatch.setattr(regular, "_watchdog_alert", lambda *args: alerts.append(args))

    assert regular.run_cron_watchdog(_now()) is True
    assert len(heals) == 1
    assert statuses[0][0:3] == ("detail_cache", _now(), "ok")
    assert alerts == []

    heals.clear()
    statuses.clear()
    monkeypatch.setattr(regular, "_watchdog_snapshot", lambda _now: _snapshot())
    assert regular.run_cron_watchdog(_now()) is True
    assert heals == []
    assert statuses == []


def test_watchdog_alert_uses_daily_cooldown_when_repair_remains_incomplete(monkeypatch):
    notices = []
    monkeypatch.setattr(
        regular,
        "_watchdog_snapshot",
        lambda _now: _snapshot(detail_cache={"races": 10, "covered": 0}),
    )
    monkeypatch.setattr(regular, "run_detail_pages_selfheal", lambda _now: True)
    monkeypatch.setattr(
        regular,
        "race_detail_page_cache_coverage",
        lambda _date: {"races": 10, "covered": 4},
    )
    monkeypatch.setattr(regular, "_write_watchdog_status", lambda *_a: None)
    monkeypatch.setattr(
        regular,
        "notify_cron_failure",
        lambda job, message, **kwargs: notices.append((job, message, kwargs)),
    )

    assert regular.run_cron_watchdog(_now()) is True
    assert notices[0][0] == "boatrace-watchdog-detail-cache"
    assert notices[0][2]["cooldown_hours"] == 24.0


def test_watchdog_repairs_results_and_zombies_and_alerts_unrepaired_signals(monkeypatch):
    snapshot = _snapshot(
        missing_results=5,
        repeated_failures=[{"task_name": "odds", "run_count": 4}],
        pool_events=4,
        pool_recent=[{"at": _now(8).isoformat(), "error_type": "PoolTimeout"}],
        stale_running=[{"task_name": "zombie"}],
    )
    repairs = []
    alerts = []
    statuses = []
    monkeypatch.setattr(regular, "_watchdog_snapshot", lambda _now: snapshot)
    monkeypatch.setattr(regular, "_watchdog_missing_result_count", lambda _date: 2)
    monkeypatch.setattr(
        regular,
        "_watchdog_stale_running",
        lambda _now: [{"task_name": "zombie"}],
    )
    monkeypatch.setattr(
        regular,
        "run_yesterday_results_backfill",
        lambda _now: repairs.append("results") or True,
    )
    monkeypatch.setattr(
        regular,
        "reap_stale_task_runs",
        lambda _now: repairs.append("zombie") or 0,
    )
    monkeypatch.setattr(regular, "_write_watchdog_status", lambda *args: statuses.append(args))
    monkeypatch.setattr(regular, "_watchdog_alert", lambda *args: alerts.append(args))

    assert regular.run_cron_watchdog(_now(8)) is True
    assert repairs == ["results", "zombie"]
    assert {item[0] for item in alerts} == {"cron-failures", "pool-exhaustion", "stale-running"}
    assert {item[0] for item in statuses} == {
        "yesterday_results",
        "cron_failures",
        "pool_exhaustion",
        "stale_running",
    }


def test_cache_coverage_uses_runtime_cache_key_version(monkeypatch):
    from src.web import app as web_app

    executed = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql, params):
            executed.append((sql, params))
            return self

        def fetchone(self):
            return (8, 0)

    monkeypatch.setattr(web_app, "_race_detail_page_cache_key", lambda race_id: f"race_detail_page:v99:{race_id}")
    monkeypatch.setattr(regular, "db_connect", Connection)

    assert regular.race_detail_page_cache_coverage("2026-08-16") == {"races": 8, "covered": 0}
    assert executed[0][1][0] == "race_detail_page:v99:"


def test_watchdog_failure_never_stops_regular_tick(monkeypatch):
    @contextmanager
    def unlocked():
        yield True

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    monkeypatch.setattr(regular, "_regular_run_lock", unlocked)
    monkeypatch.setattr(regular, "log_deploy_revision", lambda *_a: None)
    monkeypatch.setattr(regular, "jst_now", lambda: _now())
    monkeypatch.setattr(regular, "ensure_task_runs_table", lambda: None)
    monkeypatch.setattr(regular, "reap_stale_task_runs", lambda _now: 0)
    monkeypatch.setattr(
        regular,
        "run_cron_watchdog",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("watchdog down")),
    )
    monkeypatch.setattr(regular, "render_daytime_lite_mode", lambda: False)
    monkeypatch.setattr(regular, "run_py", lambda *_a, **_k: True)
    monkeypatch.setattr(regular, "run_top_page_snapshot", lambda *_a, **_k: True)

    assert regular.main() == 0
