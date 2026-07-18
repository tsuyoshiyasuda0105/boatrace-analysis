import os

os.environ["DATABASE_URL"] = ""

from src.web import app as web_app


def _member_client():
    app = web_app.create_app()
    client = app.test_client()
    with client.session_transaction() as session:
        session["is_member"] = True
    return client


def test_strategy_page_uses_stale_html_without_running_aggregation(monkeypatch):
    web_app.invalidate_cache()
    monkeypatch.setattr(web_app, "_read_page_html_cache", lambda *_args: None)
    monkeypatch.setattr(
        web_app,
        "_read_page_html_cache_stale",
        lambda _key: "stale-strategy-page",
    )
    response = _member_client().get("/member/strategy")

    assert response.status_code == 200
    assert response.get_data(as_text=True) == "stale-strategy-page"


def test_monthly_page_uses_stale_html_without_running_aggregation(monkeypatch):
    web_app.invalidate_cache()
    monkeypatch.setattr(web_app, "_read_page_html_cache", lambda *_args: None)
    monkeypatch.setattr(
        web_app,
        "_read_page_html_cache_stale",
        lambda _key: "stale-monthly-page",
    )
    response = _member_client().get("/member/strategy/monthly")

    assert response.status_code == 200
    assert response.get_data(as_text=True) == "stale-monthly-page"
