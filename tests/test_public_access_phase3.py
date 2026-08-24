# -*- coding: utf-8 -*-
"""公開範囲 第3段階: 共有パスワードによる会員ログインの廃止。

発注者の判断 (2026-08-24): 「Supabaseログインのみに設定」「会員ログインは
なくしてもいい」。合鍵を 1 本配って回す方式は、渡した相手を絞れず失効もできない。
"""
import pytest

from src.web import app as app_module
from src.web import auth as auth_module


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(app_module.config, "WEB_SESSION_SECRET", "phase3-test-secret")
    monkeypatch.setattr(app_module.config, "WEB_MEMBER_PASSWORD", "shared-password-1234567")
    monkeypatch.setattr(app_module, "_ensure_db_initialized", lambda: None)
    app = app_module.create_app()
    app.config.update(TESTING=True)
    return app.test_client()


def test_shared_password_no_longer_grants_membership(client, monkeypatch):
    monkeypatch.setattr(auth_module, "_verify_csrf_token", lambda: True)

    response = client.post("/login", data={"password": "shared-password-1234567"})

    assert response.status_code == 401, "共有パスワードで会員になれてはいけない"
    with client.session_transaction() as session:
        assert not session.get("is_member")


def test_default_shared_password_no_longer_blocks_production_start(monkeypatch):
    """会員になれない値なので、既定値のままでも起動を止める理由はない。"""
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setattr(app_module.config, "WEB_SESSION_SECRET", "phase3-production-secret")
    monkeypatch.setattr(app_module.config, "WEB_MEMBER_PASSWORD", "dev-member")
    monkeypatch.setattr(app_module, "_ensure_db_initialized", lambda: None)

    app = app_module.create_app()  # 例外を投げないこと

    assert app is not None


def test_login_page_sends_people_to_supabase_when_configured(client, monkeypatch):
    monkeypatch.setattr(
        auth_module.supabase_auth_client, "is_configured", lambda: True
    )

    response = client.get("/login")

    assert response.status_code in (301, 302)
    assert "/login-supabase" in response.headers.get("Location", "")


def test_login_page_still_renders_when_supabase_is_not_configured(client, monkeypatch):
    monkeypatch.setattr(
        auth_module.supabase_auth_client, "is_configured", lambda: False
    )

    assert client.get("/login").status_code == 200
