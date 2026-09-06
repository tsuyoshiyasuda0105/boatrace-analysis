from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import config
from src.web import app as web_app
from src.web import billing
from src.web.legal_bp import TERMS_VERSION


ROOT = Path(__file__).resolve().parents[1]
SIGNUP_ENV = {
    "SIGNUP_PLAN_NAME": "ENV_PLAN_VALUE",
    "LEGAL_PRICE": "ENV_PRICE_VALUE",
    "SIGNUP_BILLING_CYCLE": "ENV_CYCLE_VALUE",
    "SIGNUP_RENEWAL_POLICY": "ENV_RENEWAL_VALUE",
    "SIGNUP_SERVICE_CONTENT": "ENV_SERVICE_VALUE",
    "LEGAL_SERVICE_START": "ENV_START_VALUE",
    "SIGNUP_CANCELLATION_METHOD": "ENV_CANCEL_VALUE",
    "LEGAL_REFUND_POLICY": "ENV_REFUND_VALUE",
}


@pytest.fixture
def app(monkeypatch):
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("BOATRACE_TASK_TRIGGER", raising=False)
    for env_name in SIGNUP_ENV:
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.setattr(config, "STRIPE_PRICE_ID", "")
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "")
    monkeypatch.setattr(web_app, "_ensure_db_initialized", lambda: None)
    web_app.invalidate_cache()
    flask_app = web_app.create_app(cached_predictions_only=True)
    flask_app.config.update(TESTING=True, SECRET_KEY="kachisuji-step24-test")
    flask_app._system_status_cache = {"ts": time.time(), "warnings": []}
    return flask_app


def _login(client, *, role="free_member"):
    with client.session_transaction() as sess:
        sess["is_member"] = True
        sess["user_id"] = "step24-user"
        sess["email"] = "step24@example.test"
        sess["role"] = role


def test_signup_plan_requires_login(app):
    response = app.test_client().get("/signup/plan")

    assert response.status_code == 302
    assert "/login?next=/signup/plan" in response.headers["Location"]


def test_signup_plan_renders_for_logged_in_user(app):
    client = app.test_client()
    _login(client)

    response = client.get("/signup/plan")

    assert response.status_code == 200
    assert "利用規約およびプライバシーポリシーに同意します" in response.get_data(as_text=True)


def test_unconfigured_price_disables_checkout_and_shows_warning(app):
    """管理者には未設定の環境変数名を出す（設定作業のため）。"""
    client = app.test_client()
    _login(client, role="admin")

    html = client.get("/signup/plan").get_data(as_text=True)

    assert "価格またはStripe Priceが未設定のため、申込はできません。" in html
    assert "LEGAL_PRICE" in html
    assert "STRIPE_PRICE_ID" in html


def test_unconfigured_price_hides_internals_from_normal_members(app):
    """一般会員には環境変数名などの内部情報を見せず、準備中の案内だけを出す。"""
    client = app.test_client()
    _login(client)  # free_member

    html = client.get("/signup/plan").get_data(as_text=True)

    assert "有料プランは現在準備中です。" in html
    assert "LEGAL_PRICE" not in html
    assert "STRIPE_PRICE_ID" not in html
    assert "環境変数" not in html
    assert 'id="checkout-button" type="submit" disabled' in html


def test_signup_conditions_come_from_environment(app, monkeypatch):
    for env_name, value in SIGNUP_ENV.items():
        monkeypatch.setenv(env_name, value)
    monkeypatch.setattr(config, "STRIPE_PRICE_ID", "price_step24")
    client = app.test_client()
    _login(client)

    html = client.get("/signup/plan").get_data(as_text=True)

    for value in SIGNUP_ENV.values():
        assert value in html
    assert "申込条件の設定が未完了です。" not in html
    assert 'id="agree-terms" name="agree_terms" value="true" disabled' not in html


def test_signup_plan_has_all_legal_links(app):
    client = app.test_client()
    _login(client)

    html = client.get("/signup/plan").get_data(as_text=True)

    assert 'href="/legal/terms"' in html
    assert 'href="/legal/tokushoho"' in html
    assert 'href="/legal/privacy"' in html


def test_paid_member_sees_portal_instead_of_checkout(app):
    client = app.test_client()
    _login(client, role="paid_member")

    html = client.get("/signup/plan").get_data(as_text=True)

    assert 'action="/billing/portal"' in html
    assert 'action="/billing/checkout"' not in html


def test_checkout_without_consent_returns_400_without_calling_stripe(app, monkeypatch):
    client = app.test_client()
    _login(client)
    monkeypatch.setattr(config, "STRIPE_PRICE_ID", "price_step24")
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_test_step24")
    stripe_client = Mock(name="stripe_client")
    stripe_factory = Mock(return_value=stripe_client)
    monkeypatch.setattr(billing, "_stripe", stripe_factory)

    response = client.post("/billing/checkout", data={"terms_version": TERMS_VERSION})

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "consent_required",
        "message": "利用規約とプライバシーポリシーへの同意が必要です",
    }
    stripe_factory.assert_not_called()
    stripe_client.checkout.Session.create.assert_not_called()


def test_checkout_rejects_stale_terms_version_without_calling_stripe(app, monkeypatch):
    client = app.test_client()
    _login(client)
    monkeypatch.setattr(config, "STRIPE_PRICE_ID", "price_step24")
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_test_step24")
    stripe_factory = Mock()
    monkeypatch.setattr(billing, "_stripe", stripe_factory)

    response = client.post(
        "/billing/checkout",
        json={"agree_terms": True, "terms_version": "outdated"},
    )

    assert response.status_code == 400
    stripe_factory.assert_not_called()


def test_consented_checkout_keeps_existing_stripe_flow(app, monkeypatch):
    client = app.test_client()
    _login(client)
    monkeypatch.setattr(config, "STRIPE_PRICE_ID", "price_step24")
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_test_step24")
    monkeypatch.setattr(billing, "ensure_profile", Mock())
    monkeypatch.setattr(billing, "get_billing_profile", Mock(return_value={"stripe_customer_id": None}))
    stripe_client = Mock()
    stripe_client.checkout.Session.create.return_value = SimpleNamespace(url="https://checkout.test/session")
    monkeypatch.setattr(billing, "_stripe", Mock(return_value=stripe_client))

    response = client.post(
        "/billing/checkout",
        data={"agree_terms": "true", "terms_version": TERMS_VERSION},
    )

    assert response.status_code == 303
    assert response.headers["Location"] == "https://checkout.test/session"
    stripe_client.checkout.Session.create.assert_called_once()
    kwargs = stripe_client.checkout.Session.create.call_args.kwargs
    assert kwargs["line_items"] == [{"price": "price_step24", "quantity": 1}]
    assert kwargs["client_reference_id"] == "step24-user"


def test_consented_checkout_without_stripe_config_keeps_503(app):
    client = app.test_client()
    _login(client)

    response = client.post(
        "/billing/checkout",
        data={"agree_terms": "on", "terms_version": TERMS_VERSION},
    )

    assert response.status_code == 503
    assert response.get_json() == {"error": "stripe_not_configured"}


def test_step24_changes_stay_on_allowed_registration_seams():
    app_source = (ROOT / "src" / "web" / "app.py").read_text(encoding="utf-8")
    base_source = (ROOT / "src" / "web" / "templates" / "base.html").read_text(encoding="utf-8")

    assert app_source.count('app.register_blueprint(__import__("src.web.signup_bp"') == 1
    assert base_source.count("url_for('signup.plan')") == 1
