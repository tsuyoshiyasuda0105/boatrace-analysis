from __future__ import annotations

import logging

import pytest

import config
from scripts import sync_to_supabase
from src.web import app as web_app
from src.web import auth


def _create_lightweight_app(monkeypatch, *, production: bool = False):
    if production:
        monkeypatch.setenv("RENDER", "1")
        monkeypatch.setenv("BOATRACE_TASK_TRIGGER", "1")
        monkeypatch.setattr(config, "WEB_SESSION_SECRET", "production-session-secret")
        monkeypatch.setattr(config, "WEB_MEMBER_PASSWORD", "production-member-password")
    else:
        monkeypatch.delenv("RENDER", raising=False)
        monkeypatch.delenv("BOATRACE_TASK_TRIGGER", raising=False)
    monkeypatch.setattr(web_app, "_ensure_db_initialized", lambda: None)
    app = web_app.create_app(cached_predictions_only=True)
    app.config.update(TESTING=True, SECRET_KEY="security-hardening-test")
    return app


@pytest.mark.parametrize(
    ("default_attr", "default_value", "other_attr", "other_value", "message_name"),
    [
        (
            "WEB_SESSION_SECRET",
            "dev-only-do-not-use-in-prod",
            "WEB_MEMBER_PASSWORD",
            "production-member-password",
            "WEB_SESSION_SECRET",
        ),
        # WEB_MEMBER_PASSWORD は 2026-08-24 の第3段階で会員権を与えなくなった
        # (認証は Supabase のみ)。既定値のままでも誰も入れないので、起動を
        # 止める対象から外した。代わりに警告だけ出す
        # (test_default_shared_password_no_longer_blocks_production_start)。
    ],
)
def test_create_app_rejects_each_default_secret_in_production(
    monkeypatch,
    default_attr,
    default_value,
    other_attr,
    other_value,
    message_name,
):
    monkeypatch.setenv("RENDER", "1")
    # Web サービスとしての起動を表す (cron の BOATRACE_TASK_TRIGGER が他テストから
    # 漏れて cron 除外に入らないよう明示的に外す)
    monkeypatch.delenv("BOATRACE_TASK_TRIGGER", raising=False)
    monkeypatch.setattr(config, default_attr, default_value)
    monkeypatch.setattr(config, other_attr, other_value)

    with pytest.raises(RuntimeError, match=message_name):
        web_app.create_app(cached_predictions_only=True)


def test_cache_clear_rejects_beta_and_paid_members(monkeypatch):
    app = _create_lightweight_app(monkeypatch)

    for role in ("beta_member", "paid_member"):
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["is_member"] = True
            sess["role"] = role
            sess["auth_provider"] = "test"
        assert client.get("/admin/cache-clear").status_code == 403


def test_cache_clear_remains_available_to_admin(monkeypatch):
    app = _create_lightweight_app(monkeypatch)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["is_member"] = True
        sess["role"] = "admin"
        sess["auth_provider"] = "test"

    assert client.get("/admin/cache-clear").status_code == 200


def test_render_trusts_only_the_nearest_forwarded_for_hop(monkeypatch):
    app = _create_lightweight_app(monkeypatch, production=True)
    app.add_url_rule("/_client-ip", view_func=auth._client_ip)

    response = app.test_client().get(
        "/_client-ip",
        headers={"X-Forwarded-For": "203.0.113.9, 198.51.100.7"},
        environ_base={"REMOTE_ADDR": "192.0.2.10"},
    )

    assert response.get_data(as_text=True) == "198.51.100.7"


def test_guest_rate_limit_uses_page_and_api_defaults(monkeypatch):
    monkeypatch.delenv("BOATRACE_GUEST_RATE_LIMIT", raising=False)
    monkeypatch.delenv("BOATRACE_GUEST_API_RATE_LIMIT", raising=False)
    app = _create_lightweight_app(monkeypatch)
    app.add_url_rule(
        "/_rate-probe", endpoint="page_rate_probe", view_func=lambda: "ok"
    )
    app.add_url_rule(
        "/api/_rate-probe", endpoint="api_rate_probe", view_func=lambda: "ok"
    )
    client = app.test_client()

    assert all(client.get("/_rate-probe").status_code == 200 for _ in range(40))
    page_limited = client.get("/_rate-probe")
    assert page_limited.status_code == 429
    assert page_limited.get_json()["error"] == "rate_limit_exceeded"
    assert int(page_limited.headers["Retry-After"]) >= 1
    assert page_limited.headers["Cache-Control"] == "no-store"

    assert all(client.get("/api/_rate-probe").status_code == 200 for _ in range(15))
    api_limited = client.get("/api/_rate-probe")
    assert api_limited.status_code == 429
    assert api_limited.get_json()["error"] == "rate_limit_exceeded"
    assert api_limited.headers["Cache-Control"] == "no-store"


def test_guest_rate_limit_can_be_disabled_and_excludes_operational_paths(monkeypatch):
    monkeypatch.setenv("BOATRACE_GUEST_RATE_LIMIT", "1")
    app = _create_lightweight_app(monkeypatch)
    app.view_functions["healthz"] = lambda: "healthy"
    app.add_url_rule("/_rate-probe-disabled", view_func=lambda: "ok")
    client = app.test_client()

    assert all(client.get("/healthz").status_code == 200 for _ in range(3))
    assert all(client.get("/static/missing.css").status_code == 404 for _ in range(3))
    assert all(client.get("/robots.txt").status_code == 200 for _ in range(3))
    assert client.get("/_rate-probe-disabled").status_code == 200
    assert client.get("/_rate-probe-disabled").status_code == 429

    monkeypatch.setenv("BOATRACE_GUEST_RATE_LIMIT", "0")
    assert all(client.get("/_rate-probe-disabled").status_code == 200 for _ in range(3))


def test_guest_rate_limit_does_not_throttle_authenticated_members(monkeypatch):
    monkeypatch.setenv("BOATRACE_GUEST_RATE_LIMIT", "1")
    monkeypatch.setenv("BOATRACE_GUEST_API_RATE_LIMIT", "1")
    app = _create_lightweight_app(monkeypatch)
    app.add_url_rule(
        "/_member-rate-probe", endpoint="member_page_rate_probe", view_func=lambda: "ok"
    )
    app.add_url_rule(
        "/api/_member-rate-probe",
        endpoint="member_api_rate_probe",
        view_func=lambda: "ok",
    )
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["is_member"] = True
        sess["role"] = "paid_member"

    assert all(client.get("/_member-rate-probe").status_code == 200 for _ in range(3))
    assert all(client.get("/api/_member-rate-probe").status_code == 200 for _ in range(3))


def test_guest_rate_limit_returns_html_for_browser_and_json_for_api(monkeypatch):
    monkeypatch.setenv("BOATRACE_GUEST_RATE_LIMIT", "1")
    monkeypatch.setenv("BOATRACE_GUEST_API_RATE_LIMIT", "1")
    app = _create_lightweight_app(monkeypatch)
    app.add_url_rule(
        "/_html-rate-probe", endpoint="html_rate_probe", view_func=lambda: "ok"
    )
    app.add_url_rule(
        "/api/_json-rate-probe", endpoint="json_rate_probe", view_func=lambda: "ok"
    )
    client = app.test_client()
    html_headers = {"Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8"}

    assert client.get("/_html-rate-probe", headers=html_headers).status_code == 200
    page_limited = client.get("/_html-rate-probe", headers=html_headers)
    assert page_limited.status_code == 429
    assert page_limited.mimetype == "text/html"
    assert "アクセスが集中しています。しばらくお待ちください" in page_limited.get_data(
        as_text=True
    )

    assert client.get("/api/_json-rate-probe", headers=html_headers).status_code == 200
    api_limited = client.get("/api/_json-rate-probe", headers=html_headers)
    assert api_limited.status_code == 429
    assert api_limited.is_json
    assert api_limited.get_json()["error"] == "rate_limit_exceeded"


def test_robots_txt_disallows_by_default_and_can_allow_indexing(monkeypatch):
    monkeypatch.delenv("BOATRACE_ALLOW_INDEXING", raising=False)
    app = _create_lightweight_app(monkeypatch)
    client = app.test_client()

    blocked = client.get("/robots.txt")
    assert blocked.status_code == 200
    assert blocked.mimetype == "text/plain"
    assert blocked.get_data(as_text=True) == "User-agent: *\nDisallow: /\n"

    monkeypatch.setenv("BOATRACE_ALLOW_INDEXING", "1")
    allowed = client.get("/robots.txt")
    assert allowed.get_data(as_text=True) == "User-agent: *\nAllow: /\n"


def test_500_response_hides_internal_exception_but_logs_it(monkeypatch, caplog):
    app = _create_lightweight_app(monkeypatch)
    app.config["PROPAGATE_EXCEPTIONS"] = False
    marker = "the pool 'pool-1' has already 12 requests waiting"

    def fail():
        raise RuntimeError(marker)

    app.add_url_rule("/_forced-500", view_func=fail)
    with caplog.at_level(logging.ERROR, logger="src.web.app"):
        response = app.test_client().get("/_forced-500")

    assert response.status_code == 500
    assert marker not in response.get_data(as_text=True)
    assert marker in caplog.text


def test_sync_to_supabase_rejects_untrusted_table_identifier():
    assert sync_to_supabase._quote_identifier("race_results") == '"race_results"'
    with pytest.raises(ValueError, match="invalid SQL identifier"):
        sync_to_supabase._quote_identifier("races; DROP TABLE races")


def test_cron_prewarm_with_task_trigger_may_start_despite_default_secret(monkeypatch):
    """cron (BOATRACE_TASK_TRIGGER) はセッションを配らないため既定秘密でも起動できる。

    2026-08-20 実障害: cron サービスに BOATRACE_WEB_SECRET が無く、H1 ガードで
    signal_refresh が起動即死した回帰の防止。
    """
    import config as config_module
    from src.web import app as web_app

    monkeypatch.setenv("RENDER", "1")
    monkeypatch.setenv("BOATRACE_TASK_TRIGGER", "render-prewarm")
    monkeypatch.setattr(config_module, "WEB_SESSION_SECRET", "dev-only-do-not-use-in-prod")
    monkeypatch.setattr(web_app, "_ensure_db_initialized", lambda: None)
    app = web_app.create_app(cached_predictions_only=True)
    assert app is not None
