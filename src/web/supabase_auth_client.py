"""Small Supabase Auth REST client for the Flask server."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

import config


class SupabaseAuthError(RuntimeError):
    pass


@dataclass(frozen=True)
class SupabaseSession:
    user_id: str
    email: str | None
    access_token: str
    refresh_token: str | None


def is_configured() -> bool:
    return bool(config.SUPABASE_AUTH_ENABLED and config.SUPABASE_URL and config.SUPABASE_PUBLISHABLE_KEY)


def _headers() -> dict[str, str]:
    return {
        "apikey": config.SUPABASE_PUBLISHABLE_KEY,
        "Authorization": f"Bearer {config.SUPABASE_PUBLISHABLE_KEY}",
        "Content-Type": "application/json",
    }


def sign_in_with_password(email: str, password: str) -> SupabaseSession:
    if not is_configured():
        raise SupabaseAuthError("Supabase Auth is not configured")
    resp = requests.post(
        f"{config.SUPABASE_URL}/auth/v1/token?grant_type=password",
        headers=_headers(),
        json={"email": email, "password": password},
        timeout=20,
    )
    if resp.status_code >= 400:
        raise SupabaseAuthError("メールアドレスまたはパスワードが違います")
    payload: dict[str, Any] = resp.json()
    user = payload.get("user") or {}
    user_id = user.get("id")
    if not user_id:
        raise SupabaseAuthError("Supabase Auth response did not include a user id")
    return SupabaseSession(
        user_id=str(user_id),
        email=user.get("email"),
        access_token=str(payload.get("access_token") or ""),
        refresh_token=payload.get("refresh_token"),
    )


def sign_up_with_password(email: str, password: str) -> SupabaseSession | None:
    if not is_configured():
        raise SupabaseAuthError("Supabase Auth is not configured")
    resp = requests.post(
        f"{config.SUPABASE_URL}/auth/v1/signup",
        headers=_headers(),
        json={"email": email, "password": password},
        timeout=20,
    )
    if resp.status_code >= 400:
        raise SupabaseAuthError("登録できませんでした。既に登録済みの可能性があります")
    payload: dict[str, Any] = resp.json()
    # GoTrue returns the user object itself when email confirmation is enabled,
    # but returns a token/session-shaped object when automatic confirmation is
    # enabled. Accept both wire formats so every successful signup gets an
    # application profile before the confirmation link is opened.
    user = payload.get("user") or payload
    auth_session = payload.get("session") or payload
    user_id = user.get("id")
    if not user_id:
        return None
    return SupabaseSession(
        user_id=str(user_id),
        email=user.get("email"),
        access_token=str(auth_session.get("access_token") or ""),
        refresh_token=auth_session.get("refresh_token"),
    )


def request_password_recovery(email: str, redirect_to: str) -> None:
    if not is_configured():
        raise SupabaseAuthError("Supabase Auth is not configured")
    resp = requests.post(
        f"{config.SUPABASE_URL}/auth/v1/recover",
        headers=_headers(),
        params={"redirect_to": redirect_to},
        json={"email": email},
        timeout=20,
    )
    if resp.status_code >= 400:
        raise SupabaseAuthError("パスワード再設定メールを送信できませんでした")
