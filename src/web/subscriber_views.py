"""Web routes for subscribed adopted-confirmed email alerts."""
from __future__ import annotations

import logging
import time
from collections import defaultdict

from flask import abort, redirect, render_template_string, request, url_for

from src.notifications.crypto import is_valid_email
from src.notifications.subscribers import (
    ALL_ALERT_TYPES,
    DEFAULT_ALERT_TYPES,
    DEFAULT_BODY_TEMPLATE,
    DEFAULT_SUBJECT_TEMPLATE,
    subscribe,
    unsubscribe,
    verify,
)

logger = logging.getLogger(__name__)

_SIGNUP_ATTEMPTS: dict[str, list[float]] = defaultdict(list)
_SIGNUP_LIMIT = 5
_SIGNUP_WINDOW = 3600


def _client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _check_signup_rate(ip: str) -> bool:
    now = time.time()
    recent = [ts for ts in _SIGNUP_ATTEMPTS[ip] if now - ts < _SIGNUP_WINDOW]
    _SIGNUP_ATTEMPTS[ip] = recent
    return len(recent) < _SIGNUP_LIMIT


SUBSCRIBE_TEMPLATE = """
{% extends "base.html" %}
{% block title %}通知設定{% endblock %}
{% block content %}
<div class="subscribe-wrap">
  <h2>採用確定レースのメール通知</h2>
  <p class="subscribe-hint">
    採用確定になったレースをメールで受け取れます。件名と本文のひな形もここで調整できます。
  </p>

  {% if error %}<div class="login-error">{{ error }}</div>{% endif %}
  {% if message %}<div class="login-success">{{ message }}</div>{% endif %}

  <form method="post" class="subscribe-form">
    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">

    <label>
      <span>メールアドレス</span>
      <input type="email" name="email" required maxlength="254" autocomplete="email"
             value="{{ form.email }}" placeholder="you@example.com">
    </label>

    <fieldset class="alert-types">
      <legend>通知種別</legend>
      {% for code, label in alert_types.items() %}
        <label class="checkbox-row">
          <input type="checkbox" name="alert_types" value="{{ code }}"
                 {% if code in form.alert_types %}checked{% endif %}>
          <span>{{ label }}</span>
        </label>
      {% endfor %}
    </fieldset>

    <label>
      <span>最低回収率 (%)</span>
      <select name="min_recovery_rate">
        {% for rate in [130, 150, 180, 200, 250] %}
          <option value="{{ rate }}" {% if form.min_recovery_rate == rate %}selected{% endif %}>{{ rate }}%</option>
        {% endfor %}
      </select>
    </label>

    <label>
      <span>件名テンプレート</span>
      <input type="text" name="subject_template" maxlength="200" value="{{ form.subject_template }}">
      <small>{date} / {count}</small>
    </label>

    <label>
      <span>本文テンプレート</span>
      <textarea name="body_template" rows="10">{{ form.body_template }}</textarea>
      <small>{date} / {count} / {items_text} / {unsubscribe_url} / {site_url}</small>
    </label>

    <div class="privacy-note">
      <strong>保存内容:</strong><br>
      メールアドレスは暗号化して保存します。確認メールのリンクを押すまで配信は始まりません。
    </div>

    <button type="submit">確認メールを送る</button>
  </form>
</div>
{% endblock %}
"""


VERIFY_TEMPLATE = """
{% extends "base.html" %}
{% block title %}通知確認{% endblock %}
{% block content %}
<div class="subscribe-wrap">
  <h2>{{ status_title }}</h2>
  <p>{{ status_msg }}</p>
  {% if back_link %}<p><a href="{{ back_link }}">トップへ戻る</a></p>{% endif %}
</div>
{% endblock %}
"""


def _default_form() -> dict:
    return {
        "email": "",
        "alert_types": list(DEFAULT_ALERT_TYPES),
        "min_recovery_rate": 150,
        "subject_template": DEFAULT_SUBJECT_TEMPLATE,
        "body_template": DEFAULT_BODY_TEMPLATE,
    }


def register_subscriber_routes(app):
    """Register subscriber routes on the Flask app."""
    from src.notifications.mailer import send_verification_email
    from src.web.auth import _verify_csrf_token, is_admin, is_member

    @app.route("/alerts/subscribe", methods=["GET", "POST"])
    def alerts_subscribe():
        if not is_member():
            return redirect(url_for("login", next=request.path))
        if not is_admin():
            abort(403)

        form = _default_form()
        ip = _client_ip()

        if request.method == "POST":
            form["email"] = request.form.get("email", "").strip()
            form["alert_types"] = request.form.getlist("alert_types") or list(DEFAULT_ALERT_TYPES)
            form["subject_template"] = request.form.get("subject_template", "").strip() or DEFAULT_SUBJECT_TEMPLATE
            form["body_template"] = request.form.get("body_template", "").strip() or DEFAULT_BODY_TEMPLATE
            try:
                form["min_recovery_rate"] = int(float(request.form.get("min_recovery_rate", "150")))
            except ValueError:
                form["min_recovery_rate"] = 150

            if not _verify_csrf_token():
                return render_template_string(
                    SUBSCRIBE_TEMPLATE,
                    alert_types=ALL_ALERT_TYPES,
                    form=form,
                    error="セッションが無効です。ページを再読み込みして再度お試しください。",
                    message=None,
                ), 400
            if not _check_signup_rate(ip):
                return render_template_string(
                    SUBSCRIBE_TEMPLATE,
                    alert_types=ALL_ALERT_TYPES,
                    form=form,
                    error="短時間に登録が多すぎます。しばらく待ってから再度お試しください。",
                    message=None,
                ), 429
            if not is_valid_email(form["email"]):
                return render_template_string(
                    SUBSCRIBE_TEMPLATE,
                    alert_types=ALL_ALERT_TYPES,
                    form=form,
                    error="有効なメールアドレスを入力してください。",
                    message=None,
                ), 400

            try:
                _email_hash, verify_token = subscribe(
                    form["email"],
                    form["alert_types"],
                    min_recovery_rate=float(form["min_recovery_rate"]),
                    ip=ip,
                    subject_template=form["subject_template"],
                    body_template=form["body_template"],
                )
                _SIGNUP_ATTEMPTS[ip].append(time.time())
                send_verification_email(form["email"], verify_token)
            except Exception as exc:  # noqa: BLE001
                logger.exception("subscribe failed: %s", exc)
                return render_template_string(
                    SUBSCRIBE_TEMPLATE,
                    alert_types=ALL_ALERT_TYPES,
                    form=form,
                    error="通知登録に失敗しました。時間をおいて再度お試しください。",
                    message=None,
                ), 500

            return render_template_string(
                SUBSCRIBE_TEMPLATE,
                alert_types=ALL_ALERT_TYPES,
                form=_default_form(),
                error=None,
                message="確認メールを送信しました。メール内のリンクを押すと配信が有効になります。",
            )

        return render_template_string(
            SUBSCRIBE_TEMPLATE,
            alert_types=ALL_ALERT_TYPES,
            form=form,
            error=None,
            message=None,
        )

    @app.route("/alerts/verify")
    def alerts_verify():
        email_hash = verify(request.args.get("token", ""))
        if email_hash:
            return render_template_string(
                VERIFY_TEMPLATE,
                status_title="通知設定を有効化しました",
                status_msg="採用確定レースのメール通知が有効になりました。",
                back_link="/",
            )
        return render_template_string(
            VERIFY_TEMPLATE,
            status_title="確認リンクが無効です",
            status_msg="期限切れ、または無効なリンクです。必要なら再度登録してください。",
            back_link="/alerts/subscribe",
        ), 400

    @app.route("/alerts/unsubscribe")
    def alerts_unsubscribe():
        ok = unsubscribe(request.args.get("token", ""))
        return render_template_string(
            VERIFY_TEMPLATE,
            status_title="配信停止しました" if ok else "配信停止に失敗しました",
            status_msg="今後の通知は停止されます。" if ok else "リンクが無効です。",
            back_link="/",
        ), 200 if ok else 400
