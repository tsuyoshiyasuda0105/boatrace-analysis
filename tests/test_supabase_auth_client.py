from src.web import supabase_auth_client


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def _configure(monkeypatch):
    monkeypatch.setattr(supabase_auth_client.config, "SUPABASE_AUTH_ENABLED", True)
    monkeypatch.setattr(
        supabase_auth_client.config,
        "SUPABASE_URL",
        "https://project.supabase.co",
    )
    monkeypatch.setattr(
        supabase_auth_client.config,
        "SUPABASE_PUBLISHABLE_KEY",
        "public-test-key",
    )


def test_signup_accepts_top_level_user_response_when_email_confirmation_is_enabled(
    monkeypatch,
):
    _configure(monkeypatch)
    monkeypatch.setattr(
        supabase_auth_client.requests,
        "post",
        lambda *_args, **_kwargs: _Response(
            {"id": "user-1", "email": "member@example.com"}
        ),
    )

    session = supabase_auth_client.sign_up_with_password(
        "member@example.com", "valid-password"
    )

    assert session is not None
    assert session.user_id == "user-1"
    assert session.email == "member@example.com"
    assert session.access_token == ""


def test_signup_accepts_session_response_when_email_confirmation_is_disabled(
    monkeypatch,
):
    _configure(monkeypatch)
    monkeypatch.setattr(
        supabase_auth_client.requests,
        "post",
        lambda *_args, **_kwargs: _Response(
            {
                "user": {"id": "user-2", "email": "member@example.com"},
                "access_token": "access-token",
                "refresh_token": "refresh-token",
            }
        ),
    )

    session = supabase_auth_client.sign_up_with_password(
        "member@example.com", "valid-password"
    )

    assert session is not None
    assert session.user_id == "user-2"
    assert session.access_token == "access-token"
    assert session.refresh_token == "refresh-token"


def test_password_recovery_uses_public_key_and_explicit_reset_redirect(monkeypatch):
    _configure(monkeypatch)
    captured = {}

    def fake_post(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return _Response({})

    monkeypatch.setattr(supabase_auth_client.requests, "post", fake_post)

    supabase_auth_client.request_password_recovery(
        "member@example.com",
        "https://boatrace-web.onrender.com/reset-password",
    )

    assert captured["url"] == "https://project.supabase.co/auth/v1/recover"
    assert captured["json"] == {"email": "member@example.com"}
    assert captured["params"] == {
        "redirect_to": "https://boatrace-web.onrender.com/reset-password"
    }
    assert captured["headers"]["apikey"] == "public-test-key"
    assert "service_role" not in str(captured).lower()
