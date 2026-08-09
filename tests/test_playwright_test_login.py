from src.web.app import create_app


def test_playwright_test_login_is_hidden_by_default():
    app = create_app()
    app.config.update(TESTING=False, SECRET_KEY="test")

    client = app.test_client()
    response = client.get("/test/login-as/admin?next=/member/today-races")

    assert response.status_code == 404


def test_playwright_test_login_is_available_in_testing_mode():
    app = create_app()
    app.config.update(TESTING=True, SECRET_KEY="test")

    client = app.test_client()
    response = client.get("/test/login-as/admin?next=/member/today-races")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/member/today-races")
    with client.session_transaction() as session:
        assert session["is_member"] is True
        assert session["role"] == "admin"
        assert session["auth_provider"] == "playwright_test"


def test_playwright_test_login_accepts_guest_role_for_public_ui():
    app = create_app()
    app.config.update(TESTING=True, SECRET_KEY="test")

    client = app.test_client()
    response = client.get("/test/login-as/guest?next=/races?date=2026-08-09")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/races?date=2026-08-09")
    with client.session_transaction() as session:
        assert session["is_member"] is False
        assert session["role"] == "guest"
        assert session["auth_provider"] == "playwright_test"


def test_playwright_test_login_rejects_unknown_role():
    app = create_app()
    app.config.update(TESTING=True, SECRET_KEY="test")

    client = app.test_client()
    response = client.get("/test/login-as/owner")

    assert response.status_code == 404
