import os

os.environ["DATABASE_URL"] = ""

from flask import Flask

from src.web import app as web_app


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
