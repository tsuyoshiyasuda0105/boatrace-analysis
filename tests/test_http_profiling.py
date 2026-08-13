import os

os.environ["DATABASE_URL"] = ""

from src.web import app as web_app


def test_profile_headers_are_added_when_enabled(monkeypatch):
    monkeypatch.setenv("BOATRACE_PROFILE_HTTP", "1")
    monkeypatch.setattr(web_app, "_today_jst_iso", lambda: "2026-08-08")
    monkeypatch.setattr(web_app, "_races_for_date", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(web_app, "_venue_environment_summaries_for_date", lambda *_args, **_kwargs: {})
    web_app.invalidate_cache()

    app = web_app.create_app()
    app.config.update(TESTING=True, SECRET_KEY="test")
    client = app.test_client()
    response = client.get("/races?date=2026-08-08", follow_redirects=True)

    assert response.status_code == 200
    assert response.headers["X-Boatrace-Profile"] == "1"
    assert int(response.headers["X-Boatrace-Db-Query-Count"]) >= 0
    assert float(response.headers["X-Boatrace-Elapsed-Ms"]) >= 0.0
    assert int(response.headers["X-Boatrace-Response-Bytes"]) > 0
    assert "app;dur=" in response.headers["Server-Timing"]


def test_profile_headers_are_absent_when_disabled(monkeypatch):
    monkeypatch.delenv("BOATRACE_PROFILE_HTTP", raising=False)
    monkeypatch.setattr(web_app, "_today_jst_iso", lambda: "2026-08-08")
    monkeypatch.setattr(web_app, "_races_for_date", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(web_app, "_venue_environment_summaries_for_date", lambda *_args, **_kwargs: {})
    web_app.invalidate_cache()

    app = web_app.create_app()
    app.config.update(TESTING=True, SECRET_KEY="test")
    client = app.test_client()
    response = client.get("/races?date=2026-08-08", follow_redirects=True)

    assert response.status_code == 200
    assert "X-Boatrace-Profile" not in response.headers
