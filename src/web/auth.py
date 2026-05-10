"""
シンプルなパスワードベース会員認証

セッションに `is_member: True` を立てる方式。
共有パスワード1個 (config.WEB_MEMBER_PASSWORD) を確認するだけの軽量実装。

本格運用が必要なら個別ユーザー管理 / ハッシュ化パスワードに置き換え。
"""
from __future__ import annotations

from functools import wraps

from flask import session, redirect, url_for, request, jsonify, render_template_string

import config


def is_member() -> bool:
    return bool(session.get("is_member") or session.get("is_pro"))


def is_pro() -> bool:
    return bool(session.get("is_pro"))


def login_required(view):
    """会員限定の画面ビュー (HTML) → 未ログインなら /login へリダイレクト"""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not is_member():
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapper


def pro_required(view):
    """Pro プラン専用の画面ビュー → 非Proなら /pro/login へリダイレクト"""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not is_pro():
            return redirect(url_for("pro_login", next=request.path))
        return view(*args, **kwargs)
    return wrapper


def member_only_api(view):
    """会員限定の API (JSON) → 未ログインなら 401 を返す"""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not is_member():
            return jsonify({"error": "unauthorized", "message": "会員ログインが必要です"}), 401
        return view(*args, **kwargs)
    return wrapper


def pro_only_api(view):
    """Pro 専用 API → 非Proなら 403"""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not is_pro():
            return jsonify({"error": "forbidden", "message": "Pro プランが必要です"}), 403
        return view(*args, **kwargs)
    return wrapper


LOGIN_TEMPLATE = """
{% extends "base.html" %}
{% block title %}会員ログイン{% endblock %}
{% block content %}
<div class="login-wrap">
  <h2>会員ログイン</h2>
  <p class="login-hint">オンタイム予測 (EV+ マーク・Value Bet 検出) は会員限定です。</p>
  {% if error %}<div class="login-error">{{ error }}</div>{% endif %}
  <form method="post" action="{{ url_for('login') }}" class="login-form">
    <input type="hidden" name="next" value="{{ request.args.get('next', '/') }}">
    <label>
      <span>パスワード</span>
      <input type="password" name="password" required autofocus>
    </label>
    <button type="submit">ログイン</button>
  </form>
</div>
{% endblock %}
"""


PRO_LOGIN_TEMPLATE = """
{% extends "base.html" %}
{% block title %}Pro プラン ログイン{% endblock %}
{% block content %}
<div class="login-wrap pro-login">
  <div class="pro-badge">⬢ PRO</div>
  <h2>Pro プラン ログイン</h2>
  <p class="login-hint">T-15分オッズによる期待値モニター。<br>
    <strong>免責</strong>: 公営競技は控除率25%です。本ツールは支援のみで、利益保証ではありません。</p>
  {% if error %}<div class="login-error">{{ error }}</div>{% endif %}
  <form method="post" action="{{ url_for('pro_login') }}" class="login-form">
    <input type="hidden" name="next" value="{{ request.args.get('next', '/pro/ev') }}">
    <label>
      <span>Pro パスワード</span>
      <input type="password" name="password" required autofocus>
    </label>
    <button type="submit">Pro にログイン</button>
  </form>
</div>
{% endblock %}
"""


def register_auth_routes(app):
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            pw = request.form.get("password", "")
            if pw == config.WEB_MEMBER_PASSWORD:
                session["is_member"] = True
                session.permanent = True
                return redirect(request.form.get("next") or url_for("index"))
            return render_template_string(LOGIN_TEMPLATE, error="パスワードが違います")
        return render_template_string(LOGIN_TEMPLATE, error=None)

    @app.route("/pro/login", methods=["GET", "POST"])
    def pro_login():
        if request.method == "POST":
            pw = request.form.get("password", "")
            if pw == config.WEB_PRO_PASSWORD:
                session["is_pro"] = True
                session["is_member"] = True  # Pro は member の上位互換
                session.permanent = True
                return redirect(request.form.get("next") or url_for("pro_ev"))
            return render_template_string(PRO_LOGIN_TEMPLATE, error="Pro パスワードが違います")
        return render_template_string(PRO_LOGIN_TEMPLATE, error=None)

    @app.route("/logout")
    def logout():
        session.pop("is_member", None)
        session.pop("is_pro", None)
        return redirect(url_for("index"))
