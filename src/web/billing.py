"""Stripe Checkout, Customer Portal, and webhook routes."""
from __future__ import annotations

import logging

from flask import jsonify, redirect, request, session, url_for

import config
from src.web.auth import login_required
from src.web.membership import (
    ensure_profile,
    get_billing_profile,
    get_user_id_by_stripe_customer,
    set_stripe_customer,
    upsert_subscription,
)
from src.web.signup_bp import checkout_consent_is_valid

logger = logging.getLogger(__name__)


def _stripe():
    try:
        import stripe
    except ImportError as exc:
        raise RuntimeError("stripe package is not installed") from exc
    stripe.api_key = config.STRIPE_SECRET_KEY
    return stripe


def stripe_configured() -> bool:
    return bool(config.STRIPE_SECRET_KEY and config.STRIPE_PRICE_ID)


def register_billing_routes(app):
    @app.route("/billing/checkout", methods=["POST"])
    @login_required
    def billing_checkout():
        if not checkout_consent_is_valid(request):
            return jsonify({"error": "consent_required", "message": "利用規約とプライバシーポリシーへの同意が必要です"}), 400
        if not stripe_configured():
            return jsonify({"error": "stripe_not_configured"}), 503
        user_id = session.get("user_id")
        email = session.get("email")
        if not user_id:
            return jsonify({"error": "supabase_login_required"}), 401
        ensure_profile(user_id, email)
        profile = get_billing_profile(user_id)
        stripe = _stripe()
        kwargs = {
            "mode": "subscription",
            "line_items": [{"price": config.STRIPE_PRICE_ID, "quantity": 1}],
            "success_url": config.STRIPE_SUCCESS_URL or url_for("member_today_races", _external=True),
            "cancel_url": config.STRIPE_CANCEL_URL or url_for("index", _external=True),
            "client_reference_id": user_id,
            "metadata": {"user_id": user_id},
        }
        if profile.get("stripe_customer_id"):
            kwargs["customer"] = profile["stripe_customer_id"]
        elif email:
            kwargs["customer_email"] = email
        checkout = stripe.checkout.Session.create(**kwargs)
        return redirect(checkout.url, code=303)

    @app.route("/billing/portal", methods=["POST"])
    @login_required
    def billing_portal():
        if not config.STRIPE_SECRET_KEY:
            return jsonify({"error": "stripe_not_configured"}), 503
        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"error": "supabase_login_required"}), 401
        profile = get_billing_profile(user_id)
        customer_id = profile.get("stripe_customer_id")
        if not customer_id:
            return jsonify({"error": "stripe_customer_not_found"}), 404
        stripe = _stripe()
        portal = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=config.STRIPE_PORTAL_RETURN_URL or url_for("member_today_races", _external=True),
        )
        return redirect(portal.url, code=303)

    @app.route("/stripe/webhook", methods=["POST"])
    def stripe_webhook():
        if not config.STRIPE_SECRET_KEY or not config.STRIPE_WEBHOOK_SECRET:
            return jsonify({"error": "stripe_webhook_not_configured"}), 503
        stripe = _stripe()
        payload = request.get_data()
        sig = request.headers.get("Stripe-Signature", "")
        try:
            event = stripe.Webhook.construct_event(payload, sig, config.STRIPE_WEBHOOK_SECRET)
        except Exception as exc:
            logger.warning("stripe webhook signature/payload rejected: %s", exc)
            return jsonify({"error": "invalid_webhook"}), 400

        event_type = event.get("type")
        obj = event.get("data", {}).get("object", {})
        try:
            if event_type == "checkout.session.completed":
                _handle_checkout_completed(stripe, obj)
            elif event_type in {
                "customer.subscription.created",
                "customer.subscription.updated",
                "customer.subscription.deleted",
            }:
                _handle_subscription_event(obj)
        except Exception:
            logger.exception("stripe webhook processing failed type=%s", event_type)
            return jsonify({"error": "webhook_processing_failed"}), 500
        return jsonify({"received": True})


def _handle_checkout_completed(stripe, checkout_session: dict) -> None:
    user_id = checkout_session.get("client_reference_id") or checkout_session.get("metadata", {}).get("user_id")
    customer_id = checkout_session.get("customer")
    if not user_id or not customer_id:
        logger.warning("checkout completed without user/customer metadata")
        return
    set_stripe_customer(str(user_id), str(customer_id))
    subscription_id = checkout_session.get("subscription")
    if subscription_id:
        subscription = stripe.Subscription.retrieve(subscription_id)
        upsert_subscription(str(user_id), str(customer_id), dict(subscription))


def _handle_subscription_event(subscription: dict) -> None:
    customer_id = subscription.get("customer")
    if not customer_id:
        return
    user_id = get_user_id_by_stripe_customer(str(customer_id))
    if not user_id:
        logger.warning("subscription event for unknown customer=%s", customer_id)
        return
    upsert_subscription(user_id, str(customer_id), dict(subscription))
