from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_supabase_session_role_is_refreshed_from_membership_table():
    source = (ROOT / "src" / "web" / "auth.py").read_text(encoding="utf-8")
    assert 'if session.get("auth_provider") != "supabase":' in source
    assert 'role = get_effective_role(str(user_id))' in source
    assert 'session["role"] = role' in source
    assert "@app.before_request" in source


def test_admin_membership_route_is_protected_and_rendered():
    source = (ROOT / "src" / "web" / "auth.py").read_text(encoding="utf-8")
    assert '@app.route("/admin/memberships", methods=["GET"])' in source
    assert "@admin_required" in source
    assert '"admin_memberships.html"' in source
    assert "list_membership_overview()" in source


def test_base_template_shows_admin_menu_and_auth_badge():
    base = (ROOT / "src" / "web" / "templates" / "base.html").read_text(encoding="utf-8")
    assert "{% if is_admin() %}" in base
    assert "url_for('admin_memberships')" in base
    assert "{{ current_role() }} / {{ current_auth_provider() }}" in base
    assert "url_for('login_supabase')" in base


def test_app_exposes_auth_context_to_templates():
    app_source = (ROOT / "src" / "web" / "app.py").read_text(encoding="utf-8")
    assert 'app.jinja_env.globals["current_role"] = current_role' in app_source
    assert 'app.jinja_env.globals["current_auth_provider"] = current_auth_provider' in app_source
    assert 'app.jinja_env.globals["is_supabase_auth_enabled"] = is_supabase_auth_enabled' in app_source
