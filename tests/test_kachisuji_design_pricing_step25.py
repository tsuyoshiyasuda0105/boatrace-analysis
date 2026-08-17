from __future__ import annotations

import time
from pathlib import Path

import config
from src.web import app as web_app


ROOT = Path(__file__).resolve().parents[1]
KACHISUJI_CSS = ROOT / "src" / "web" / "static" / "kachisuji.css"
KACHISUJI_TEMPLATE = ROOT / "src" / "web" / "templates" / "kachisuji_search.html"


def _app(monkeypatch):
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("BOATRACE_TASK_TRIGGER", raising=False)
    monkeypatch.setattr(config, "STRIPE_PRICE_ID", "")
    monkeypatch.setattr(web_app, "_ensure_db_initialized", lambda: None)
    web_app.invalidate_cache()
    app = web_app.create_app(cached_predictions_only=True)
    app.config.update(TESTING=True, SECRET_KEY="kachisuji-step25-test")
    app._system_status_cache = {"ts": time.time(), "warnings": []}
    return app


def _login_as_paid_member(client) -> None:
    with client.session_transaction() as session:
        session["is_member"] = True
        session["user_id"] = "step25-user"
        session["email"] = "step25@example.test"
        session["role"] = "paid_member"


def test_kachisuji_palette_matches_fixed_production_dark_theme():
    source = KACHISUJI_CSS.read_text(encoding="utf-8").lower()

    for production_color in (
        "#070912",
        "#141a2e",
        "#1c2440",
        "#e7eaf0",
        "#00d4ff",
        "#34e890",
        "#ffb547",
    ):
        assert production_color in source

    for old_light_color in (
        "#f5f7f8",
        "#ffffff",
        "#eef2f4",
        "#f3ead3",
        "#e3f0e8",
        "#f8e9dd",
        "#f7e2e4",
    ):
        assert old_light_color not in source

    assert "prefers-color-scheme" not in source


def test_kachisuji_page_keeps_search_results_and_badge_classes(
    monkeypatch, tmp_path: Path
):
    available_db = tmp_path / "kachisuji.db"
    available_db.touch()
    monkeypatch.setenv("KACHISUJI_DB", str(available_db))
    client = _app(monkeypatch).test_client()
    _login_as_paid_member(client)

    response = client.get("/kachisuji")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    for required_markup in (
        'id="btnSearch"',
        'class="panel results"',
        'class="chip day"',
        'class="chip restored"',
        "discovery-status",
    ):
        assert required_markup in html

    css_source = KACHISUJI_CSS.read_text(encoding="utf-8")
    assert ".discovery-gold" in css_source
    assert ".discovery-vein" in css_source


def test_unconfigured_signup_and_tokushoho_show_monthly_tax_included_price(
    monkeypatch,
):
    for env_name in (
        "LEGAL_PRICE",
        "SIGNUP_BILLING_CYCLE",
        "SIGNUP_RENEWAL_POLICY",
    ):
        monkeypatch.delenv(env_name, raising=False)
    app = _app(monkeypatch)
    client = app.test_client()
    _login_as_paid_member(client)

    signup_html = client.get("/signup/plan").get_data(as_text=True)
    legal_html = client.get("/legal/tokushoho").get_data(as_text=True)

    for html in (signup_html, legal_html):
        assert "1,380円" in html
        assert "税込" in html
        assert "月額" in html
    assert "自動更新" in signup_html


def test_search_template_keeps_only_non_color_dynamic_inline_style():
    source = KACHISUJI_TEMPLATE.read_text(encoding="utf-8")

    assert 'style="width:' in source
    assert "#f5f7f8" not in source.lower()
