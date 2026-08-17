from __future__ import annotations

import time
from pathlib import Path

import pytest

from src.web import app as web_app


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_ROOT = ROOT / "src" / "web" / "templates"
LEGAL_PATHS = ("/legal/terms", "/legal/tokushoho", "/legal/privacy")
LEGAL_ENV = {
    "LEGAL_OPERATOR_NAME": "ENV_OPERATOR_VALUE",
    "LEGAL_RESPONSIBLE_PERSON": "ENV_RESPONSIBLE_VALUE",
    "LEGAL_ADDRESS": "ENV_ADDRESS_VALUE",
    "LEGAL_PHONE": "ENV_PHONE_VALUE",
    "LEGAL_EMAIL": "ENV_EMAIL_VALUE",
    "LEGAL_PRICE": "ENV_PRICE_VALUE",
    "LEGAL_ADDITIONAL_FEES": "ENV_ADDITIONAL_FEES_VALUE",
    "LEGAL_PAYMENT_METHOD": "ENV_PAYMENT_METHOD_VALUE",
    "LEGAL_PAYMENT_TIMING": "ENV_PAYMENT_TIMING_VALUE",
    "LEGAL_SERVICE_START": "ENV_SERVICE_START_VALUE",
    "LEGAL_REFUND_POLICY": "ENV_REFUND_POLICY_VALUE",
    "LEGAL_SYSTEM_REQUIREMENTS": "ENV_SYSTEM_REQUIREMENTS_VALUE",
    "LEGAL_JURISDICTION": "ENV_JURISDICTION_VALUE",
    "LEGAL_EFFECTIVE_DATE": "ENV_EFFECTIVE_DATE_VALUE",
}


@pytest.fixture
def app(monkeypatch):
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("BOATRACE_TASK_TRIGGER", raising=False)
    for env_name in LEGAL_ENV:
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.setattr(web_app, "_ensure_db_initialized", lambda: None)
    web_app.invalidate_cache()
    app = web_app.create_app(cached_predictions_only=True)
    app.config.update(TESTING=True, SECRET_KEY="kachisuji-step23-test")
    app._system_status_cache = {"ts": time.time(), "warnings": []}
    return app


@pytest.mark.parametrize("path", LEGAL_PATHS)
def test_legal_pages_are_public_and_warn_when_required_values_are_missing(app, path):
    response = app.test_client().get(path)

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "この表記は未完成です。事業者情報を設定してください。" in html
    assert "role=\"alert\"" in html
    assert "LEGAL_" in html


@pytest.mark.parametrize("path", LEGAL_PATHS)
def test_legal_pages_render_environment_values_without_warning(app, monkeypatch, path):
    for env_name, value in LEGAL_ENV.items():
        monkeypatch.setenv(env_name, value)

    response = app.test_client().get(path)

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "ENV_OPERATOR_VALUE" in html
    assert "この表記は未完成です。事業者情報を設定してください。" not in html


def test_legal_footer_links_are_rendered(app):
    html = app.test_client().get("/legal/terms").get_data(as_text=True)

    assert 'href="/legal/terms"' in html
    assert 'href="/legal/tokushoho"' in html
    assert 'href="/legal/privacy"' in html


def test_tokushoho_renders_every_configured_disclosure_value(app, monkeypatch):
    for env_name, value in LEGAL_ENV.items():
        monkeypatch.setenv(env_name, value)

    html = app.test_client().get("/legal/tokushoho").get_data(as_text=True)

    for env_name, value in LEGAL_ENV.items():
        if env_name == "LEGAL_JURISDICTION":
            continue
        assert value in html


def test_legal_templates_contain_no_prohibited_expressions():
    prohibited = (
        "\u7d76\u5bfe",
        "\u5fc5\u305a\u5f53\u305f\u308b",
        "\u6295\u8cc7",
    )
    templates = (
        TEMPLATE_ROOT / "legal_terms.html",
        TEMPLATE_ROOT / "legal_tokushoho.html",
        TEMPLATE_ROOT / "legal_privacy.html",
    )

    for template in templates:
        source = template.read_text(encoding="utf-8")
        assert not any(expression in source for expression in prohibited), template


def test_terms_include_required_paid_service_conditions():
    source = (TEMPLATE_ROOT / "legal_terms.html").read_text(encoding="utf-8")

    for required_text in (
        "予想の販売を目的とするサービスではありません",
        "的中または利益を保証するものではありません",
        "18歳未満の方は本サービスを利用できません",
        "20歳未満の方は舟券を購入できません",
        "自動更新",
        "毎日24:00から翌6:00",
        "原則として終了日の30日前",
        "終了日以降の料金は請求しません",
        "支払済みの当月分は返金しません",
        "準拠法・管轄",
    ):
        assert required_text in source


def test_app_registration_is_one_line_and_billing_is_untouched_by_legal_routes():
    app_source = (ROOT / "src" / "web" / "app.py").read_text(encoding="utf-8")
    billing_source = (ROOT / "src" / "web" / "billing.py").read_text(encoding="utf-8")

    assert app_source.count('app.register_blueprint(__import__("src.web.legal_bp"') == 1
    assert "/legal/" not in billing_source
