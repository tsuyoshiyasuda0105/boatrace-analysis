from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_supabase_auth_is_added_as_parallel_login():
    source = (ROOT / "src" / "web" / "auth.py").read_text(encoding="utf-8")
    assert '@app.route("/login", methods=["GET", "POST"])' in source
    assert '@app.route("/login-supabase", methods=["GET", "POST"])' in source
    assert '@app.route("/signup-supabase", methods=["GET", "POST"])' in source
    assert '@app.route("/reset-password", methods=["GET"])' in source


def test_supabase_refresh_token_is_not_saved_in_flask_session():
    source = (ROOT / "src" / "web" / "auth.py").read_text(encoding="utf-8")
    assert 'session["supabase_refresh_token"]' not in source
    assert "refresh_token" not in source


def test_reset_password_page_uses_only_public_supabase_key():
    source = (ROOT / "src" / "web" / "auth.py").read_text(encoding="utf-8")
    assert "SUPABASE_RESET_PASSWORD_TEMPLATE" in source
    assert "publishable_key=config.SUPABASE_PUBLISHABLE_KEY" in source
    assert 'fetch(supabaseUrl.replace(/\\\\/$/, "") + "/auth/v1/user"' in source
    assert "SUPABASE_SECRET_KEY" not in source
    assert "SERVICE_ROLE" not in source


def test_legacy_password_login_is_not_admin():
    source = (ROOT / "src" / "web" / "auth.py").read_text(encoding="utf-8")
    assert 'session["auth_provider"] = "legacy_password"' in source
    assert 'session["role"] = "paid_member"' in source
    assert 'session["role"] = "admin"' not in source


def test_stripe_webhook_and_billing_are_registered():
    billing = (ROOT / "src" / "web" / "billing.py").read_text(encoding="utf-8")
    app = (ROOT / "src" / "web" / "app.py").read_text(encoding="utf-8")
    assert '@app.route("/stripe/webhook", methods=["POST"])' in billing
    assert '@app.route("/billing/checkout", methods=["POST"])' in billing
    assert '@app.route("/billing/portal", methods=["POST"])' in billing
    assert "register_billing_routes(app)" in app


def test_membership_migration_enables_rls():
    migration = (
        ROOT / "supabase" / "migrations" / "202608030001_supabase_auth_stripe_membership.sql"
    ).read_text(encoding="utf-8")
    assert "ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;" in migration
    assert "ALTER TABLE public.user_roles ENABLE ROW LEVEL SECURITY;" in migration
    assert "ALTER TABLE public.subscriptions ENABLE ROW LEVEL SECURITY;" in migration
