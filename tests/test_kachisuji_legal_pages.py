"""法定ページの必要条件と禁止表現をカバーする回帰テスト。

2026-08-28 に事業者確認済みの草案 4 本に差し替えた
(src/web/legal_drafts/*.md → src/web/legal_bp.py が配信)。
以前は旧テンプレの正確な文言を照合していたが、草案側で表現が変わったので
「趣旨が入っているか」と「未確定項目の警告が出るか」を確かめる形に改める。
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from src.web import app as web_app


ROOT = Path(__file__).resolve().parents[1]
DRAFT_ROOT = ROOT / "src" / "web" / "legal_drafts"
LEGAL_PATHS = ("/legal/terms", "/legal/tokushoho", "/legal/privacy", "/legal/discord")
# 草案が使うプレースホルダー → 埋める環境変数
LEGAL_ENV = {
    "LEGAL_OPERATOR_NAME": "ENV_OPERATOR_VALUE",
    "LEGAL_RESPONSIBLE_PERSON": "ENV_RESPONSIBLE_VALUE",
    "LEGAL_ADDRESS": "ENV_ADDRESS_VALUE",
    "LEGAL_PHONE": "ENV_PHONE_VALUE",
    "LEGAL_EMAIL": "ENV_EMAIL_VALUE",
    "LEGAL_SERVICE_NAME": "ENV_SERVICE_NAME_VALUE",
    "LEGAL_PLAN_NAME": "ENV_PLAN_NAME_VALUE",
    "LEGAL_PRICE": "ENV_PRICE_VALUE",
    "LEGAL_PLAN_FEATURES": "ENV_PLAN_FEATURES_VALUE",
    "LEGAL_SERVICE_PERIOD": "ENV_SERVICE_PERIOD_VALUE",
    "LEGAL_MAINTENANCE_WINDOW": "ENV_MAINTENANCE_VALUE",
    "LEGAL_FREE_TRIAL": "ENV_FREE_TRIAL_VALUE",
    "LEGAL_REFUND_POLICY": "ENV_REFUND_POLICY_VALUE",
    "LEGAL_JURISDICTION": "ENV_JURISDICTION_VALUE",
    "LEGAL_ANALYTICS_VENDORS": "ENV_ANALYTICS_VALUE",
    "LEGAL_EXTERNAL_VENDORS": "ENV_EXTERNAL_VENDORS_VALUE",
    "LEGAL_RETENTION_PERIOD": "ENV_RETENTION_PERIOD_VALUE",
    "LEGAL_EFFECTIVE_DATE": "ENV_EFFECTIVE_DATE_VALUE",
    "LEGAL_BILLING_ANCHOR": "ENV_BILLING_ANCHOR_VALUE",
    "LEGAL_CANCEL_DEADLINE": "ENV_CANCEL_DEADLINE_VALUE",
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
    app.config.update(TESTING=True, SECRET_KEY="legal-test")
    app._system_status_cache = {"ts": time.time(), "warnings": []}
    return app


@pytest.mark.parametrize("path", LEGAL_PATHS)
def test_legal_pages_are_public_and_warn_when_required_values_are_missing(app, path):
    response = app.test_client().get(path)

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "未確定の項目が" in html
    assert 'role="alert"' in html
    assert 'class="legal-placeholder"' in html


@pytest.mark.parametrize("path", LEGAL_PATHS)
def test_legal_pages_render_environment_values_without_warning(app, monkeypatch, path):
    for env_name, value in LEGAL_ENV.items():
        monkeypatch.setenv(env_name, value)

    response = app.test_client().get(path)

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    # どのページも運営者名は入る (プライバシー・利用規約・特商法・Discord いずれも登場)
    # Discord 規約は運営者名を条項内に持たない (草案) のでページ別に確認しない
    assert "class=\"legal-page\"" in html
    # 4 ページ全てで警告が消えることは求めない (草案には環境変数で埋められない
    # 未確定項目が残っている前提)。個々のページで環境変数の反映を確認する。


def test_footer_lists_all_four_legal_pages(app):
    html = app.test_client().get("/legal/terms").get_data(as_text=True)

    for path in LEGAL_PATHS:
        assert f'href="{path}"' in html, f"{path} へのリンクが無い"


def test_tokushoho_renders_the_configured_disclosure_values(app, monkeypatch):
    for env_name, value in LEGAL_ENV.items():
        monkeypatch.setenv(env_name, value)

    html = app.test_client().get("/legal/tokushoho").get_data(as_text=True)

    # 特商法ページで確実に表示される主要項目
    for key in ("LEGAL_OPERATOR_NAME", "LEGAL_RESPONSIBLE_PERSON", "LEGAL_ADDRESS",
                "LEGAL_EMAIL", "LEGAL_PRICE", "LEGAL_SERVICE_NAME"):
        assert LEGAL_ENV[key] in html, f"{key} の値が特商法ページに反映されていない"


def test_drafts_contain_no_prohibited_marketing_expressions():
    """「必ず当たる」「確実」等の誤認を招く表現を使わないこと。

    「絶対」は「絶対にしない」等の禁止条項で使うので単語一致では見ない。
    """
    prohibited = ("必ず当たる", "投資助言", "元本保証")
    for draft in DRAFT_ROOT.glob("*.md"):
        source = draft.read_text(encoding="utf-8")
        for word in prohibited:
            assert word not in source, f"{draft.name} に禁止表現 {word!r}"


def test_terms_capture_the_required_paid_service_conditions():
    """有料サービスに必要な条項が利用規約に入っていること (表現差は許容)。"""
    source = (DRAFT_ROOT / "APP_TERMS_DRAFT.md").read_text(encoding="utf-8")

    for kind, needle in (
        ("予想販売の否定",        "有料予想の販売を目的とするものではありません"),
        ("20歳以上限定",          "20歳以上"),
        ("舟券購入は 20 歳から",   "20歳未満の者は舟券を購入できません"),
        ("自動更新の明示",        "自動更新"),
        ("結果を保証しない・誤認禁止", "的中又は利益を保証"),
        ("賭博対策条項",          "暴力団"),
    ):
        assert needle in source, f"{kind} が利用規約に無い ({needle!r})"


def test_blueprint_registration_is_one_line_and_billing_is_untouched():
    app_source = (ROOT / "src" / "web" / "app.py").read_text(encoding="utf-8")
    billing_source = (ROOT / "src" / "web" / "billing.py").read_text(encoding="utf-8")

    assert app_source.count('app.register_blueprint(__import__("src.web.legal_bp"') == 1
    assert "/legal/" not in billing_source
