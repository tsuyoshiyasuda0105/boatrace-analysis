from pathlib import Path
from types import SimpleNamespace

from flask import Flask, session

from src.web import auth

ROOT = Path(__file__).resolve().parents[1]


def test_supabase_session_role_is_refreshed_from_membership_table():
    source = (ROOT / "src" / "web" / "auth.py").read_text(encoding="utf-8")
    assert 'if session.get("auth_provider") != "supabase":' in source
    assert 'role = get_effective_role(str(user_id))' in source
    assert 'session["role"] = role' in source
    assert "@app.before_request" in source


def test_supabase_role_refresh_uses_session_ttl(monkeypatch):
    app = Flask(__name__)
    app.secret_key = "test-secret"
    calls = []
    clock = [1_000.0]
    monkeypatch.setattr(auth.time, "time", lambda: clock[0])
    monkeypatch.setattr(
        auth,
        "ensure_profile",
        lambda user_id, email: calls.append(("profile", user_id, email)),
    )
    monkeypatch.setattr(
        auth,
        "get_effective_role",
        lambda user_id: calls.append(("role", user_id)) or "admin",
    )

    with app.test_request_context("/"):
        session.update({
            "auth_provider": "supabase",
            "user_id": "user-1",
            "email": "member@example.com",
            "role": "free_member",
            "is_member": True,
            "supabase_role_checked_at": 950.0,
        })
        auth._refresh_supabase_membership_session()
        assert calls == []

        clock[0] = 1_011.0
        auth._refresh_supabase_membership_session()
        auth._refresh_supabase_membership_session()

        assert calls == [("role", "user-1")]
        assert session["role"] == "admin"
        assert session["supabase_role_checked_at"] == 1_011.0


def test_supabase_role_refresh_uses_validated_role_during_pool_timeout(monkeypatch):
    app = Flask(__name__)
    app.secret_key = "test-secret"
    clock = [1_000.0]
    calls = []
    monkeypatch.setattr(auth.time, "time", lambda: clock[0])

    def fail_role_refresh(_user_id):
        calls.append("refresh")
        raise TimeoutError("pool unavailable")

    monkeypatch.setattr(auth, "get_effective_role", fail_role_refresh)

    with app.test_request_context("/"):
        session.update({
            "auth_provider": "supabase",
            "user_id": "user-1",
            "role": "paid_member",
            "is_member": True,
            "supabase_role_checked_at": 900.0,
        })

        auth._refresh_supabase_membership_session()
        auth._refresh_supabase_membership_session()

        assert calls == ["refresh"]
        assert session["role"] == "paid_member"
        assert session["is_member"] is True
        assert session["supabase_role_retry_at"] == 1_015.0


def test_supabase_role_refresh_clears_unvalidated_session_on_pool_timeout(monkeypatch):
    app = Flask(__name__)
    app.secret_key = "test-secret"
    monkeypatch.setattr(auth.time, "time", lambda: 1_000.0)
    monkeypatch.setattr(
        auth,
        "get_effective_role",
        lambda _user_id: (_ for _ in ()).throw(TimeoutError("pool unavailable")),
    )

    with app.test_request_context("/"):
        session.update({
            "auth_provider": "supabase",
            "user_id": "user-1",
            "role": "admin",
            "is_member": True,
        })

        auth._refresh_supabase_membership_session()

        assert dict(session) == {}


def test_supabase_role_refresh_does_not_hide_programming_errors(monkeypatch):
    app = Flask(__name__)
    app.secret_key = "test-secret"
    monkeypatch.setattr(auth.time, "time", lambda: 1_000.0)
    monkeypatch.setattr(
        auth,
        "get_effective_role",
        lambda _user_id: (_ for _ in ()).throw(ValueError("bad role query")),
    )

    with app.test_request_context("/"):
        session.update({
            "auth_provider": "supabase",
            "user_id": "user-1",
            "role": "paid_member",
            "is_member": True,
            "supabase_role_checked_at": 900.0,
        })

        try:
            auth._refresh_supabase_membership_session()
        except ValueError as exc:
            assert str(exc) == "bad role query"
        else:
            raise AssertionError("programming error must not be hidden")


def test_supabase_login_session_starts_with_fresh_role_timestamp(monkeypatch):
    app = Flask(__name__)
    app.secret_key = "test-secret"
    monkeypatch.setattr(auth.time, "time", lambda: 2_000.0)

    with app.test_request_context("/"):
        auth._set_supabase_session("user-2", "member@example.com", "paid_member")

        assert session["supabase_role_checked_at"] == 2_000.0
        assert session["role"] == "paid_member"


def test_pending_supabase_login_keeps_minimum_role_during_pool_timeout(monkeypatch):
    app = Flask(__name__)
    app.secret_key = "test-secret"
    monkeypatch.setattr(auth.time, "time", lambda: 2_000.0)
    monkeypatch.setattr(
        auth,
        "get_effective_role",
        lambda _user_id: (_ for _ in ()).throw(TimeoutError("pool unavailable")),
    )

    with app.test_request_context("/"):
        auth._set_supabase_session(
            "user-3", "member@example.com", "free_member", role_validated=False
        )
        auth._refresh_supabase_membership_session()

        assert session["is_member"] is True
        assert session["role"] == "free_member"
        assert session["supabase_role_retry_at"] == 2_015.0


def test_pending_supabase_login_upgrades_after_db_recovery(monkeypatch):
    app = Flask(__name__)
    app.secret_key = "test-secret"
    monkeypatch.setattr(auth.time, "time", lambda: 2_000.0)
    monkeypatch.setattr(auth, "get_effective_role", lambda _user_id: "admin")

    with app.test_request_context("/"):
        auth._set_supabase_session(
            "user-4", "admin@example.com", "free_member", role_validated=False
        )
        auth._refresh_supabase_membership_session()

        assert session["role"] == "admin"
        assert "supabase_role_pending_at" not in session


def test_supabase_login_redirects_with_minimum_role_when_membership_db_is_busy(monkeypatch):
    app = Flask(__name__)
    app.secret_key = "test-secret"

    @app.route("/")
    def index():
        return "ok"

    monkeypatch.setattr(auth.supabase_auth_client, "is_configured", lambda: True)
    monkeypatch.setattr(
        auth.supabase_auth_client,
        "sign_in_with_password",
        lambda _email, _password: SimpleNamespace(
            user_id="user-5", email="member@example.com"
        ),
    )
    monkeypatch.setattr(
        auth,
        "ensure_profile",
        lambda _user_id, _email: (_ for _ in ()).throw(TimeoutError("pool busy")),
    )
    auth.register_auth_routes(app)
    client = app.test_client()
    with client.session_transaction() as login_session:
        login_session["csrf_token"] = "csrf-test"

    response = client.post(
        "/login-supabase",
        data={
            "csrf_token": "csrf-test",
            "email": "member@example.com",
            "password": "valid-password",
            "next": "/",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")
    with client.session_transaction() as logged_in_session:
        assert logged_in_session["is_member"] is True
        assert logged_in_session["role"] == "free_member"
        assert logged_in_session["auth_provider"] == "supabase"
        assert "supabase_role_pending_at" in logged_in_session


def test_public_race_and_health_requests_skip_supabase_role_refresh(monkeypatch):
    app = Flask(__name__, static_folder="static")
    app.secret_key = "test-secret"
    calls = []
    monkeypatch.setattr(
        auth,
        "_refresh_supabase_membership_session",
        lambda: calls.append("refresh"),
    )
    auth.register_auth_routes(app)

    for path in (
        "/",
        "/races?date=2026-08-12",
        "/race/20260812-01-01",
        "/static/app.css",
        "/favicon.ico",
        "/healthz",
    ):
        with app.test_request_context(path):
            app.preprocess_request()

    assert calls == []


def test_protected_html_request_still_refreshes_supabase_role(monkeypatch):
    app = Flask(__name__)
    app.secret_key = "test-secret"
    calls = []
    monkeypatch.setattr(
        auth,
        "_refresh_supabase_membership_session",
        lambda: calls.append("refresh"),
    )
    auth.register_auth_routes(app)

    with app.test_request_context("/member/today-races"):
        app.preprocess_request()

    assert calls == ["refresh"]


def test_admin_membership_route_is_protected_and_rendered():
    source = (ROOT / "src" / "web" / "auth.py").read_text(encoding="utf-8")
    assert '@app.route("/admin/memberships", methods=["GET"])' in source
    assert "@admin_required" in source
    assert '"admin_memberships.html"' in source
    assert "list_membership_overview()" in source


def test_base_template_shows_admin_menu_and_auth_badge():
    base = (ROOT / "src" / "web" / "templates" / "base.html").read_text(encoding="utf-8")
    assert "{% if is_admin() and not cache_neutral_auth %}" in base
    assert "url_for('admin_memberships')" in base
    assert "{{ current_role() }} / {{ current_auth_provider() }}" in base
    assert "url_for('login_supabase')" in base
    assert "url_for('member_today_races'" in base
    assert "url_for('public_roi')" in base
    assert "/alerts/subscribe" in base


def test_app_exposes_auth_context_to_templates():
    app_source = (ROOT / "src" / "web" / "app.py").read_text(encoding="utf-8")
    assert 'app.jinja_env.globals["current_role"] = current_role' in app_source
    assert 'app.jinja_env.globals["current_auth_provider"] = current_auth_provider' in app_source
    assert 'app.jinja_env.globals["is_supabase_auth_enabled"] = is_supabase_auth_enabled' in app_source


def test_cached_page_key_is_partitioned_by_viewer_role():
    app_source = (ROOT / "src" / "web" / "app.py").read_text(encoding="utf-8")
    assert 'viewer_cache_scope = "guest:none"' in app_source
    assert 'viewer_role = str(session.get("role")' in app_source
    assert 'viewer_provider = str(session.get("auth_provider") or "none")' in app_source
    assert 'key = f"{fn.__name__}:{args}:{kwargs}:{filtered_qs}:{viewer_cache_scope}"' in app_source


def test_admin_only_routes_are_protected():
    app_source = (ROOT / "src" / "web" / "app.py").read_text(encoding="utf-8")
    assert '@app.route("/public/roi")' in app_source
    assert '@app.route("/member/today-races")' in app_source
    assert '@app.route("/member/today-races/history")' in app_source
    assert '@app.route("/member/strategy")' in app_source
    assert '@app.route("/member/strategy/monthly")' in app_source
    assert '@app.route("/member/health")' in app_source
    assert app_source.count("@admin_required") >= 6


def test_start_prediction_and_alerts_are_admin_only():
    start_prediction = (ROOT / "src" / "web" / "start_prediction_api.py").read_text(encoding="utf-8")
    subscriber_views = (ROOT / "src" / "web" / "subscriber_views.py").read_text(encoding="utf-8")
    auth_source = (ROOT / "src" / "web" / "auth.py").read_text(encoding="utf-8")
    assert "def admin_only_api(view):" in auth_source
    assert "@admin_only_api" in start_prediction
    assert "@admin_required" in start_prediction
    assert "if not is_admin():" in subscriber_views
    assert 'abort(403)' in subscriber_views
