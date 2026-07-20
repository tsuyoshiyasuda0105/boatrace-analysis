import os
from pathlib import Path

os.environ["DATABASE_URL"] = ""

from flask import Flask

from src.web import app as web_app
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
    assert '"cache_only": True' in route_source
    assert '"cache_miss": True' in route_source


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


def test_signal_refresh_uses_one_task_slot_per_half_hour(monkeypatch):
    attempted = []
    monkeypatch.setattr(
        scheduler,
        "task_attempt_exists",
        lambda task, run_date: attempted.append((task, run_date)) or True,
    )

    now = scheduler.datetime(2026, 7, 21, 10, 37, tzinfo=scheduler.JST)
    assert scheduler.run_signal_refresh_slot(now)
    assert attempted == [("render_signal_refresh_10_1", "2026-07-21")]
