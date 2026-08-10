from __future__ import annotations

from flask import jsonify

import config
from src.web.app import create_app
from src.web.auth import login_required


def _app(
    monkeypatch,
    *,
    member_password="member-secret",
    playwright_password="playwright-test-secret-123",
):
    monkeypatch.setattr(config, "WEB_MEMBER_PASSWORD", member_password)
    monkeypatch.setattr(config, "WEB_PLAYWRIGHT_PASSWORD", playwright_password)
    app = create_app()
    app.config.update(TESTING=True, SECRET_KEY="test-secret-key")

    @app.get("/_test/read")
    @login_required
    def _test_read():
        return jsonify({"ok": True})

    @app.post("/_test/write")
    @login_required
    def _test_write():
        return jsonify({"ok": True})

    return app


def _login(client, password: str):
    with client.session_transaction() as session:
        session["csrf_token"] = "csrf-test-token"
    return client.post(
        "/login",
        data={
            "password": password,
            "csrf_token": "csrf-test-token",
            "next": "/_test/read",
        },
    )


def test_playwright_password_creates_read_only_test_viewer(monkeypatch):
    client = _app(monkeypatch).test_client()

    response = _login(client, "playwright-test-secret-123")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/_test/read")
    with client.session_transaction() as session:
        assert session["is_member"] is True
        assert session["role"] == "test_viewer"
        assert session["auth_provider"] == "playwright_password"
        assert len(session["playwright_password_version"]) == 64
        assert session["playwright_password_version"] != "playwright-test-secret-123"
    assert client.get("/_test/read").status_code == 200
    assert client.post("/_test/write").status_code == 403
    assert client.get("/admin/memberships").status_code == 403


def test_existing_member_password_keeps_paid_member_access(monkeypatch):
    client = _app(monkeypatch).test_client()

    response = _login(client, "member-secret")

    assert response.status_code == 302
    with client.session_transaction() as session:
        assert session["role"] == "paid_member"
        assert session["auth_provider"] == "legacy_password"
    assert client.post("/_test/write").status_code == 200


def test_playwright_password_is_disabled_when_empty(monkeypatch):
    client = _app(monkeypatch, playwright_password="").test_client()

    assert _login(client, "playwright-test-secret-123").status_code == 401


def test_playwright_password_is_disabled_when_too_short(monkeypatch):
    client = _app(monkeypatch, playwright_password="short-secret").test_client()

    assert _login(client, "short-secret").status_code == 401


def test_playwright_password_must_differ_from_member_password(monkeypatch):
    client = _app(
        monkeypatch,
        member_password="same-secret-value-123",
        playwright_password="same-secret-value-123",
    ).test_client()

    response = _login(client, "same-secret-value-123")

    assert response.status_code == 302
    with client.session_transaction() as session:
        assert session["role"] == "paid_member"
        assert session["auth_provider"] == "legacy_password"


def test_rotating_playwright_password_expires_existing_session(monkeypatch):
    client = _app(monkeypatch).test_client()
    assert _login(client, "playwright-test-secret-123").status_code == 302

    monkeypatch.setattr(config, "WEB_PLAYWRIGHT_PASSWORD", "rotated-test-secret-456")
    response = client.get("/_test/read")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login?next=/_test/read")
    with client.session_transaction() as session:
        assert "is_member" not in session
        assert "playwright_password_version" not in session
