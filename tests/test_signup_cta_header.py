from __future__ import annotations

from html.parser import HTMLParser

import pytest

from src.web import app as web_app


TARGET_DATE = "2026-09-01"


class _AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if href is not None:
            self.hrefs.append(href)


def _snapshot() -> dict:
    return {
        "version": web_app.TOP_PAGE_SNAPSHOT_VERSION,
        "date": TARGET_DATE,
        "generated_at": "2026-09-01T09:00:00+09:00",
        "stadium_groups": [],
        "initial_market_signals": {
            "date": TARGET_DATE,
            "signals": {},
            "race_badges": {},
            "accident_watch": {},
        },
        "empty": False,
    }


def _create_app(monkeypatch: pytest.MonkeyPatch, *, supabase_enabled: bool = True):
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("BOATRACE_TASK_TRIGGER", raising=False)
    monkeypatch.setenv("BOATRACE_GUEST_ACCESS", "1")
    monkeypatch.setattr(web_app, "_ensure_db_initialized", lambda: None)
    monkeypatch.setattr(web_app, "_today_jst_iso", lambda: TARGET_DATE)
    monkeypatch.setattr(
        web_app,
        "is_supabase_auth_enabled",
        lambda: supabase_enabled,
    )
    monkeypatch.setattr(web_app, "_read_top_page_snapshot", lambda *_args: _snapshot())
    web_app.invalidate_cache()
    app = web_app.create_app(cached_predictions_only=True)
    app.config.update(TESTING=True, SECRET_KEY="signup-cta-header-test")
    return app


def _top_html(monkeypatch: pytest.MonkeyPatch, *, supabase_enabled: bool = True) -> str:
    app = _create_app(monkeypatch, supabase_enabled=supabase_enabled)
    response = app.test_client().get("/")
    assert response.status_code == 200
    return response.get_data(as_text=True)


def _anchor_hrefs(html: str) -> list[str]:
    parser = _AnchorParser()
    parser.feed(html)
    return parser.hrefs


def test_guest_top_page_has_signup_link(monkeypatch):
    assert "/signup-supabase" in _anchor_hrefs(_top_html(monkeypatch))


def test_signup_link_has_expected_label(monkeypatch):
    html = _top_html(monkeypatch)
    assert '<a href="/signup-supabase" class="account-btn account-btn-signup">新規登録</a>' in html


def test_member_top_page_hides_signup_link(monkeypatch):
    app = _create_app(monkeypatch)
    client = app.test_client()
    with client.session_transaction() as session:
        session["is_member"] = True
        session["role"] = "paid_member"
        session["auth_provider"] = "test"

    response = client.get("/")

    assert response.status_code == 200
    assert "/signup-supabase" not in _anchor_hrefs(response.get_data(as_text=True))


def test_signup_link_is_hidden_when_supabase_auth_is_disabled(monkeypatch):
    html = _top_html(monkeypatch, supabase_enabled=False)
    assert "/signup-supabase" not in _anchor_hrefs(html)


def test_signup_link_precedes_legacy_login(monkeypatch):
    html = _top_html(monkeypatch)
    assert html.index('href="/signup-supabase"') < html.index('href="/login"')
