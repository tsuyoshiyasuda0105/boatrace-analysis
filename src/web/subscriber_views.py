"""
アラート購読の Web UI ルート (Flask)

エンドポイント:
  GET  /alerts/subscribe    - 購読フォーム表示
  POST /alerts/subscribe    - 購読登録 (確認メール送信)
  GET  /alerts/verify       - 確認メールリンク受け取り
  GET  /alerts/unsubscribe  - 解除リンク受け取り

セキュリティ:
  - CSRF トークン検証
  - reCAPTCHA は無し (ブルートフォース制限で代用)
  - IP 制限: 同 IP からの登録は 5/時間
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Optional

from flask import request, render_template_string, abort, jsonify

from src.notifications.subscribers import (
    ALL_ALERT_TYPES,
    DEFAULT_ALERT_TYPES,
    subscribe,
    verify,
    unsubscribe,
)
from src.notifications.crypto import is_valid_email

logger = logging.getLogger(__name__)

# 簡易レート制限 (IP 別、in-memory)
_SIGNUP_ATTEMPTS: dict[str, list[float]] = defaultdict(list)
_SIGNUP_LIMIT = 5  # 同 IP から 5 回/時間
_SIGNUP_WINDOW = 3600


def _client_ip() -> str:
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _check_signup_rate(ip: str) -> bool:
    now = time.time()
    attempts = [t for t in _SIGNUP_ATTEMPTS[ip] if now - t < _SIGNUP_WINDOW]
    _SIGNUP_ATTEMPTS[ip] = attempts
    return len(attempts) < _SIGNUP_LIMIT


SUBSCRIBE_TEMPLATE = """
{% extends "base.html" %}
{% block title %}アラート通知の購読{% endblock %}
{% block content %}
<div class="subscribe-wrap">
  <h2>📬 L4 シグナル メール通知</h2>
  <p class="subscribe-hint">
    検証回収率 150% 超の高確度レース (L4 マーク) が発見されたら、
    レース締切前にメールでお知らせします。
  </p>

  {% if error %}<div class="login-error">{{ error }}</div>{% endif %}
  {% if message %}<div class="login-success">{{ message }}</div>{% endif %}

  <form method="post" class="subscribe-form">
    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">

    <label>
      <span>メールアドレス</span>
      <input type="email" name="email" required maxlength="254" autocomplete="email"
             placeholder="you@example.com">
    </label>

    <fieldset class="alert-types">
      <legend>受信したいアラート種類 (推奨 = 全選択)</legend>
      {% for code, label in alert_types.items() %}
        <label class="checkbox-row">
          <input type="checkbox" name="alert_types" value="{{ code }}"
                 {% if code in default_selected %}checked{% endif %}>
          <span>{{ label }}</span>
        </label>
      {% endfor %}
    </fieldset>

    <label>
      <span>通知の最小回収率 (%) - これ未満は通知しない</span>
      <select name="min_recovery_rate">
        <option value="130">130% 以上 (多め)</option>
        <option value="150" selected>150% 以上 (推奨)</option>
        <option value="200">200% 以上 (厳選)</option>
      </select>
    </label>

    <div class="privacy-note">
      <strong>🔒 プライバシー保護:</strong><br>
      メールアドレスは AES-256-GCM 暗号化して保存します。<br>
      DB が漏洩しても、別管理の鍵がなければ復号できません。<br>
      いつでも解除可能 (メール内のリンク 1 クリック)。
    </div>

    <button type="submit">確認メールを受け取る</button>
  </form>

  <p class="subscribe-footer">
    既に登録済みの場合は再登録すると新しい確認メールが届きます。<br>
    <strong>本サービスは投資助言ではありません。最終判断はご自身で。</strong>
  </p>
</div>
{% endblock %}
"""

VERIFY_TEMPLATE = """
{% extends "base.html" %}
{% block title %}メール認証{% endblock %}
{% block content %}
<div class="subscribe-wrap">
  <h2>{{ status_emoji }} {{ status_title }}</h2>
  <p>{{ status_msg }}</p>
  {% if back_link %}<p><a href="{{ back_link }}">← トップへ戻る</a></p>{% endif %}
</div>
{% endblock %}
"""


def register_subscriber_routes(app):
    """Flask アプリにルートを登録"""

    # auth.py の _verify_csrf_token を import (循環参照を避ける)
    from src.web.auth import _verify_csrf_token, is_member

    @app.route("/alerts/subscribe", methods=["GET", "POST"])
    def alerts_subscribe():
        # ▼ 会員ログイン必須 (backlog item 19, 20: Pro 廃止 + 全機能ログイン)
        if not is_member():
            from flask import redirect, url_for
            return redirect(url_for("login", next=request.path))

        ip = _client_ip()

        if request.method == "POST":
            # CSRF 検証
            if not _verify_csrf_token():
                return render_template_string(
                    SUBSCRIBE_TEMPLATE,
                    alert_types=ALL_ALERT_TYPES,
                    default_selected=DEFAULT_ALERT_TYPES,
                    error="セッションが無効です。ページを再読み込みしてください。",
                    message=None,
                ), 400

            # レート制限
            if not _check_signup_rate(ip):
                logger.warning("subscribe rate-limited: %s", ip)
                return render_template_string(
                    SUBSCRIBE_TEMPLATE,
                    alert_types=ALL_ALERT_TYPES,
                    default_selected=DEFAULT_ALERT_TYPES,
                    error="登録試行が多すぎます。1時間後に再度お試しください。",
                    message=None,
                ), 429

            email = request.form.get("email", "").strip()
            alert_types = request.form.getlist("alert_types") or list(DEFAULT_ALERT_TYPES)
            try:
                min_rate = float(request.form.get("min_recovery_rate", "150"))
            except ValueError:
                min_rate = 150.0

            try:
                eh, verify_token = subscribe(
                    email, alert_types,
                    min_recovery_rate=min_rate, ip=ip,
                )
                _SIGNUP_ATTEMPTS[ip].append(time.time())
            except ValueError as e:
                return render_template_string(
                    SUBSCRIBE_TEMPLATE,
                    alert_types=ALL_ALERT_TYPES,
                    default_selected=DEFAULT_ALERT_TYPES,
                    error=str(e),
                    message=None,
                ), 400
            except Exception as e:
                logger.exception("subscribe failed: %s", e)
                return render_template_string(
                    SUBSCRIBE_TEMPLATE,
                    alert_types=ALL_ALERT_TYPES,
                    default_selected=DEFAULT_ALERT_TYPES,
                    error="登録に失敗しました。管理者にご連絡ください。",
                    message=None,
                ), 500

            # 確認メール送信 (Phase 2 で実装、今は console log のみ)
            from src.notifications.mailer import send_verification_email
            try:
                send_verification_email(email, verify_token)
            except Exception as e:
                logger.exception("send_verification_email failed: %s", e)

            return render_template_string(
                SUBSCRIBE_TEMPLATE,
                alert_types=ALL_ALERT_TYPES,
                default_selected=DEFAULT_ALERT_TYPES,
                error=None,
                message="確認メールを送信しました。受信箱を確認してリンクをクリックしてください。",
            )

        # GET
        return render_template_string(
            SUBSCRIBE_TEMPLATE,
            alert_types=ALL_ALERT_TYPES,
            default_selected=DEFAULT_ALERT_TYPES,
            error=None,
            message=None,
        )

    @app.route("/alerts/verify")
    def alerts_verify():
        token = request.args.get("token", "")
        eh = verify(token)
        if eh:
            return render_template_string(
                VERIFY_TEMPLATE,
                status_emoji="✅",
                status_title="認証完了",
                status_msg="メール認証が完了しました。次に L4 マークが発火したらメールが届きます。",
                back_link="/",
            )
        return render_template_string(
            VERIFY_TEMPLATE,
            status_emoji="❌",
            status_title="認証失敗",
            status_msg="リンクが無効、期限切れ、または既に使用済みです。再度登録してください。",
            back_link="/alerts/subscribe",
        ), 400

    @app.route("/alerts/unsubscribe")
    def alerts_unsubscribe():
        token = request.args.get("token", "")
        ok = unsubscribe(token)
        if ok:
            return render_template_string(
                VERIFY_TEMPLATE,
                status_emoji="👋",
                status_title="解除完了",
                status_msg="メール通知を解除しました。ご利用ありがとうございました。",
                back_link="/",
            )
        return render_template_string(
            VERIFY_TEMPLATE,
            status_emoji="❌",
            status_title="解除失敗",
            status_msg="リンクが無効です。",
            back_link="/",
        ), 400
