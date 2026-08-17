"""Public legal disclosure pages for the paid service."""
from __future__ import annotations

import os
from collections.abc import Iterable

from flask import Blueprint, render_template

bp = Blueprint("legal", __name__, url_prefix="/legal")

TERMS_VERSION = "2026-08-17"


_LEGAL_FIELDS = {
    "operator_name": ("LEGAL_OPERATOR_NAME", "{{ 販売事業者名を入力 }}"),
    "responsible_person": ("LEGAL_RESPONSIBLE_PERSON", "{{ 運営統括責任者名を入力 }}"),
    "address": ("LEGAL_ADDRESS", "{{ 所在地を入力 }}"),
    "phone": ("LEGAL_PHONE", "{{ 電話番号を入力 }}"),
    "email": ("LEGAL_EMAIL", "{{ メールアドレスを入力 }}"),
    "price": ("LEGAL_PRICE", "月額1,380円（税込）"),
    "additional_fees": ("LEGAL_ADDITIONAL_FEES", "{{ 商品代金以外の必要料金を入力 }}"),
    "payment_method": ("LEGAL_PAYMENT_METHOD", "{{ 支払方法を入力 }}"),
    "payment_timing": ("LEGAL_PAYMENT_TIMING", "{{ 支払時期を入力 }}"),
    "service_start": ("LEGAL_SERVICE_START", "{{ サービス提供時期を入力 }}"),
    "refund_policy": ("LEGAL_REFUND_POLICY", "{{ 返品・キャンセル（返金）条件を入力 }}"),
    "system_requirements": ("LEGAL_SYSTEM_REQUIREMENTS", "{{ 動作環境を入力 }}"),
    "jurisdiction": ("LEGAL_JURISDICTION", "{{ 管轄裁判所を入力 }}"),
    "effective_date": ("LEGAL_EFFECTIVE_DATE", "{{ 制定日・最終改定日を入力 }}"),
}

_TOKUSHOHO_FIELDS = (
    "operator_name",
    "responsible_person",
    "address",
    "phone",
    "email",
    "price",
    "additional_fees",
    "payment_method",
    "payment_timing",
    "service_start",
    "refund_policy",
    "system_requirements",
    "effective_date",
)


def _legal_context(required_keys: Iterable[str]) -> dict[str, object]:
    legal: dict[str, str] = {}
    missing_fields: list[str] = []
    for key in required_keys:
        env_name, placeholder = _LEGAL_FIELDS[key]
        value = os.environ.get(env_name, "").strip()
        legal[key] = value or placeholder
        if not value:
            missing_fields.append(env_name)
    return {
        "legal": legal,
        "legal_incomplete": bool(missing_fields),
        "missing_legal_fields": missing_fields,
    }


@bp.get("/terms")
def terms():
    return render_template(
        "legal_terms.html",
        **_legal_context(
            (
                "operator_name",
                "email",
                "price",
                "payment_method",
                "payment_timing",
                "refund_policy",
                "jurisdiction",
                "effective_date",
            )
        ),
    )


@bp.get("/tokushoho")
def tokushoho():
    return render_template("legal_tokushoho.html", **_legal_context(_TOKUSHOHO_FIELDS))


@bp.get("/privacy")
def privacy():
    return render_template(
        "legal_privacy.html",
        **_legal_context(("operator_name", "email", "effective_date")),
    )
