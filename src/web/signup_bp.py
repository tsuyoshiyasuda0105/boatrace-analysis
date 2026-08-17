"""Paid-plan signup page and checkout-consent validation."""
from __future__ import annotations

import os
from typing import Any

from flask import Blueprint, Request, render_template

import config
from src.web.auth import is_paid_member, login_required
from src.web.legal_bp import TERMS_VERSION

bp = Blueprint("signup", __name__, url_prefix="/signup")

_PLAN_FIELDS = {
    "plan_name": ("SIGNUP_PLAN_NAME", "{{ プラン名を入力 }}"),
    "price": ("LEGAL_PRICE", "月額1,380円（税込）"),
    "billing_cycle": ("SIGNUP_BILLING_CYCLE", "月額"),
    "renewal_policy": ("SIGNUP_RENEWAL_POLICY", "毎月自動更新"),
    "service_content": ("SIGNUP_SERVICE_CONTENT", "{{ 提供内容を入力 }}"),
    "service_start": ("LEGAL_SERVICE_START", "{{ サービス提供時期を入力 }}"),
    "cancellation_method": ("SIGNUP_CANCELLATION_METHOD", "{{ 解約方法を入力 }}"),
    "refund_policy": ("LEGAL_REFUND_POLICY", "{{ 解約時の利用期限・日割り返金条件を入力 }}"),
}

_TRUE_VALUES = {"1", "true", "on", "yes"}


def _request_values(req: Request) -> Any:
    if req.is_json:
        return req.get_json(silent=True) or {}
    return req.form


def checkout_consent_is_valid(req: Request) -> bool:
    """Accept only explicit consent to the currently published terms version."""
    values = _request_values(req)
    agreed = str(values.get("agree_terms", "")).strip().lower() in _TRUE_VALUES
    version_matches = str(values.get("terms_version", "")).strip() == TERMS_VERSION
    return agreed and version_matches


def _plan_context() -> dict[str, object]:
    plan: dict[str, str] = {}
    missing_fields: list[str] = []
    for key, (env_name, placeholder) in _PLAN_FIELDS.items():
        value = os.environ.get(env_name, "").strip()
        plan[key] = value or placeholder
        if not value:
            missing_fields.append(env_name)

    stripe_price_configured = bool(config.STRIPE_PRICE_ID)
    if not stripe_price_configured:
        missing_fields.append("STRIPE_PRICE_ID")

    return {
        "plan": plan,
        "plan_incomplete": bool(missing_fields),
        "missing_plan_fields": missing_fields,
        "checkout_available": bool(os.environ.get("LEGAL_PRICE", "").strip())
        and stripe_price_configured,
        "terms_version": TERMS_VERSION,
        "already_paid": is_paid_member(),
    }


@bp.get("/plan")
@login_required
def plan():
    return render_template("signup_plan.html", **_plan_context())
