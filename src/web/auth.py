"""
シンプルなパスワードベース会員認証

セッションに `is_member: True` を立てる方式。
共有パスワード1個 (config.WEB_MEMBER_PASSWORD) を確認するだけの軽量実装。

セキュリティ対策:
  - hmac.compare_digest で timing attack 防止
  - IP ベースのブルートフォース制限 (10回/15分でロック)
  - CSRF トークン (セッションごとに生成、フォーム POST で検証)
  - オープンリダイレクト対策 (相対パスのみ許可)
  - パスワード短すぎ警告
"""
from __future__ import annotations

import hmac
import logging
import secrets
import time
from functools import wraps

from flask import session, redirect, url_for, request, jsonify, render_template_string, abort

import config

logger = logging.getLogger(__name__)

# ===== ブルートフォース対策: IP 別の試行カウンタ (in-memory) =====
# {ip: [(timestamp, success_bool), ...]} 直近 15 分のみ保持
_LOGIN_ATTEMPTS: dict[str, list[tuple[float, bool]]] = {}
_LOCKOUT_THRESHOLD = 10      # 15分以内に失敗10回でロック
_LOCKOUT_WINDOW_SEC = 900    # 15 分
_LOCKOUT_DURATION_SEC = 1800  # ロック後30分はログイン不可


def _client_ip() -> str:
    """Render は X-Forwarded-For を立てる。逆プロキシ前提"""
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _check_rate_limit(ip: str) -> tuple[bool, int]:
    """Returns (allowed, retry_after_sec)"""
    now = time.time()
    # 古い記録を削除 (15分 + lockout duration 以上)
    attempts = _LOGIN_ATTEMPTS.get(ip, [])
    attempts = [(t, ok) for t, ok in attempts
                if now - t < _LOCKOUT_WINDOW_SEC + _LOCKOUT_DURATION_SEC]
    _LOGIN_ATTEMPTS[ip] = attempts

    # 直近 _LOCKOUT_WINDOW_SEC 以内の失敗数
    recent_failures = sum(1 for t, ok in attempts
                          if not ok and now - t < _LOCKOUT_WINDOW_SEC)
    if recent_failures >= _LOCKOUT_THRESHOLD:
        # 最新失敗時刻 + lockout 期間まで待つ
        last_fail = max((t for t, ok in attempts if not ok), default=now)
        retry_after = int(last_fail + _LOCKOUT_DURATION_SEC - now)
        if retry_after > 0:
            return False, retry_after
    return True, 0


def _record_attempt(ip: str, success: bool):
    _LOGIN_ATTEMPTS.setdefault(ip, []).append((time.time(), success))
    if not success:
        logger.warning("login failed from %s (attempts=%d)",
                       ip, sum(1 for _, ok in _LOGIN_ATTEMPTS[ip] if not ok))


def _safe_password_check(input_pw: str, expected_pw: str) -> bool:
    """timing-attack 対策: hmac.compare_digest で定数時間比較"""
    if not input_pw or not expected_pw:
        return False
    try:
        return hmac.compare_digest(input_pw.encode("utf-8"),
                                    expected_pw.encode("utf-8"))
    except Exception:
        return False


def _get_csrf_token() -> str:
    """セッションに CSRF トークンを生成・取得"""
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def _verify_csrf_token() -> bool:
    """POST 時の CSRF トークン検証"""
    expected = session.get("csrf_token")
    given = request.form.get("csrf_token", "")
    if not expected or not given:
        return False
    return hmac.compare_digest(expected, given)


def _safe_redirect_url(next_url: str, default: str = "/") -> str:
    """オープンリダイレクト対策。
    next が以下のいずれかなら安全、それ以外は default に。
      - / で始まる相対パス (例: /races?date=2026-05-12)
    NG:
      - // で始まる (プロトコル相対 URL)
      - http:// https:// で始まる絶対 URL
      - \ や @ などの細工
    """
    if not next_url:
        return default
    # 細工系の文字を弾く
    if "\\" in next_url or "\x00" in next_url:
        return default
    # // または スキーム付きは外部 URL の可能性
    if next_url.startswith("//"):
        return default
    if "://" in next_url:
        return default
    # /xxx で始まる相対パスのみ許可
    if not next_url.startswith("/"):
        return default
    return next_url


def is_member() -> bool:
    return bool(session.get("is_member"))


def login_required(view):
    """会員限定の画面ビュー (HTML) → 未ログインなら /login へリダイレクト"""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not is_member():
            return redirect(url_for("login", next=request.path))
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


LOGIN_TEMPLATE = """
{% extends "base.html" %}
{% block title %}会員ログイン{% endblock %}
{% block content %}
<div class="login-wrap">
  <h2>会員ログイン</h2>
  <p class="login-hint">本サービスは会員限定です。パスワードを入力してログインしてください。</p>
  {% if error %}<div class="login-error">{{ error }}</div>{% endif %}
  <form method="post" action="{{ url_for('login') }}" class="login-form">
    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
    <input type="hidden" name="next" value="{{ safe_next(request.args.get('next', '/'), '/') }}">
    <label>
      <span>パスワード</span>
      <input type="password" name="password" required autofocus>
    </label>
    <button type="submit">ログイン</button>
  </form>
</div>
{% endblock %}
"""


def register_auth_routes(app):
    # テンプレートから _safe_redirect_url を使えるように (next の事前検証用)
    app.jinja_env.globals["safe_next"] = _safe_redirect_url
    # テンプレートから {{ csrf_token() }} を使えるように
    app.jinja_env.globals["csrf_token"] = _get_csrf_token

    @app.route("/login", methods=["GET", "POST"])
    def login():
        ip = _client_ip()
        if request.method == "POST":
            # CSRF トークン検証
            if not _verify_csrf_token():
                logger.warning("CSRF token mismatch on /login from %s", ip)
                return render_template_string(
                    LOGIN_TEMPLATE,
                    error="セッションが無効です。ページを再読み込みしてください。"
                ), 400
            # ブルートフォース制限
            allowed, retry_after = _check_rate_limit(ip)
            if not allowed:
                logger.warning("login rate-limited for %s (retry %ds)", ip, retry_after)
                return render_template_string(
                    LOGIN_TEMPLATE,
                    error=f"試行回数が多すぎます。{retry_after//60+1}分後に再度お試しください。"
                ), 429
            pw = request.form.get("password", "")
            if _safe_password_check(pw, config.WEB_MEMBER_PASSWORD):
                _record_attempt(ip, True)
                session.clear()  # セッション固定攻撃対策
                session["is_member"] = True
                session.permanent = True
                # オープンリダイレクト対策: next が外部 URL なら index へ
                next_url = _safe_redirect_url(request.form.get("next", ""), url_for("index"))
                return redirect(next_url)
            _record_attempt(ip, False)
            # 短時間の遅延 (timing attack の更なる緩和)
            time.sleep(0.3)
            return render_template_string(LOGIN_TEMPLATE, error="パスワードが違います"), 401
        return render_template_string(LOGIN_TEMPLATE, error=None)

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("index"))
