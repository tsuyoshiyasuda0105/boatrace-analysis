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
from src.web import supabase_auth_client
from src.web.membership import ensure_profile, get_effective_role, role_allows

logger = logging.getLogger(__name__)

# ===== ブルートフォース対策: IP 別の試行カウンタ (in-memory) =====
# {ip: [(timestamp, success_bool), ...]} 直近 15 分のみ保持
_LOGIN_ATTEMPTS: dict[str, list[tuple[float, bool]]] = {}
_LOCKOUT_THRESHOLD = 10      # 15分以内に失敗10回でロック
_LOCKOUT_WINDOW_SEC = 900    # 15 分
_LOCKOUT_DURATION_SEC = 1800  # ロック後30分はログイン不可


def _client_ip() -> str:
    """クライアント IP を取得。

    セキュリティ: X-Forwarded-For はクライアントが自由に付与できるヘッダのため、
    信頼できる逆プロキシの背後でのみ採用する。具体的には RENDER 環境変数が
    立っている本番環境のみで XFF を読み、それ以外は request.remote_addr を使用。
    これにより、ローカル/直アクセス時のなりすましでブルートフォース制限を
    回避される事を防ぐ。
    """
    import os as _os
    if _os.environ.get("RENDER"):
        # Render の前段プロキシは XFF を必ず最後に追記する。先頭が真のクライアント。
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


def current_role() -> str:
    return session.get("role") or ("paid_member" if is_member() else "guest")


def is_admin() -> bool:
    return role_allows(current_role(), "admin")


def is_paid_member() -> bool:
    return role_allows(current_role(), "paid_member")


def _set_supabase_session(user_id: str, email: str | None, role: str) -> None:
    session.clear()
    session["is_member"] = role in {"free_member", "paid_member", "admin"}
    session["user_id"] = user_id
    session["email"] = email
    session["role"] = role
    session["auth_provider"] = "supabase"
    session.permanent = True


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


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not is_member():
            return redirect(url_for("login", next=request.path))
        if not is_admin():
            abort(403)
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


SUPABASE_LOGIN_TEMPLATE = """
{% extends "base.html" %}
{% block title %}Supabaseログイン{% endblock %}
{% block content %}
<div class="login-wrap">
  <h2>Supabaseログイン</h2>
  <p class="login-hint">新しい会員ログインです。既存ログインは <a href="{{ url_for('login') }}">こちら</a> から利用できます。</p>
  {% if error %}<div class="login-error">{{ error }}</div>{% endif %}
  <form method="post" action="{{ url_for('login_supabase') }}" class="login-form">
    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
    <input type="hidden" name="next" value="{{ safe_next(request.args.get('next', '/'), '/') }}">
    <label>
      <span>メールアドレス</span>
      <input type="email" name="email" autocomplete="email" required autofocus>
    </label>
    <label>
      <span>パスワード</span>
      <input type="password" name="password" autocomplete="current-password" required>
    </label>
    <button type="submit">ログイン</button>
  </form>
  <p class="login-hint"><a href="{{ url_for('signup_supabase') }}">新規登録はこちら</a></p>
</div>
{% endblock %}
"""


SUPABASE_SIGNUP_TEMPLATE = """
{% extends "base.html" %}
{% block title %}新規登録{% endblock %}
{% block content %}
<div class="login-wrap">
  <h2>新規登録</h2>
  <p class="login-hint">登録直後は無料会員です。有料権限はStripe決済または管理者付与で反映します。</p>
  {% if error %}<div class="login-error">{{ error }}</div>{% endif %}
  {% if message %}<div class="login-hint">{{ message }}</div>{% endif %}
  <form method="post" action="{{ url_for('signup_supabase') }}" class="login-form">
    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
    <label>
      <span>メールアドレス</span>
      <input type="email" name="email" autocomplete="email" required autofocus>
    </label>
    <label>
      <span>パスワード</span>
      <input type="password" name="password" autocomplete="new-password" minlength="8" required>
    </label>
    <button type="submit">登録</button>
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
                session["role"] = "paid_member"
                session["auth_provider"] = "legacy_password"
                session.permanent = True
                # オープンリダイレクト対策: next が外部 URL なら index へ
                next_url = _safe_redirect_url(request.form.get("next", ""), url_for("index"))
                return redirect(next_url)
            _record_attempt(ip, False)
            # 短時間の遅延 (timing attack の更なる緩和)
            time.sleep(0.3)
            return render_template_string(LOGIN_TEMPLATE, error="パスワードが違います"), 401
        return render_template_string(LOGIN_TEMPLATE, error=None)

    @app.route("/login-supabase", methods=["GET", "POST"])
    def login_supabase():
        if not supabase_auth_client.is_configured():
            abort(404)
        ip = _client_ip()
        if request.method == "POST":
            if not _verify_csrf_token():
                logger.warning("CSRF token mismatch on /login-supabase from %s", ip)
                return _render_supabase_login("セッションが無効です。ページを再読み込みしてください。"), 400
            allowed, retry_after = _check_rate_limit(ip)
            if not allowed:
                return _render_supabase_login(
                    f"試行回数が多すぎます。{retry_after//60+1}分後に再度お試しください。"
                ), 429
            email = request.form.get("email", "").strip().lower()
            pw = request.form.get("password", "")
            try:
                auth_session = supabase_auth_client.sign_in_with_password(email, pw)
                ensure_profile(auth_session.user_id, auth_session.email)
                role = get_effective_role(auth_session.user_id)
                _record_attempt(ip, True)
                _set_supabase_session(auth_session.user_id, auth_session.email, role)
                next_url = _safe_redirect_url(request.form.get("next", ""), url_for("index"))
                return redirect(next_url)
            except Exception as e:
                logger.warning("supabase login failed for %s from %s: %s", email, ip, e)
                _record_attempt(ip, False)
                time.sleep(0.3)
                return _render_supabase_login(str(e)), 401
        return _render_supabase_login(None)

    @app.route("/signup-supabase", methods=["GET", "POST"])
    def signup_supabase():
        if not supabase_auth_client.is_configured():
            abort(404)
        ip = _client_ip()
        if request.method == "POST":
            if not _verify_csrf_token():
                return _render_supabase_signup(error="セッションが無効です。ページを再読み込みしてください。"), 400
            allowed, retry_after = _check_rate_limit(ip)
            if not allowed:
                return _render_supabase_signup(
                    error=f"試行回数が多すぎます。{retry_after//60+1}分後に再度お試しください。"
                ), 429
            email = request.form.get("email", "").strip().lower()
            pw = request.form.get("password", "")
            if len(pw) < 8:
                return _render_supabase_signup(error="パスワードは8文字以上にしてください。"), 400
            try:
                auth_session = supabase_auth_client.sign_up_with_password(email, pw)
                _record_attempt(ip, True)
                if auth_session and auth_session.user_id:
                    ensure_profile(auth_session.user_id, auth_session.email)
                    if auth_session.access_token:
                        _set_supabase_session(auth_session.user_id, auth_session.email, "free_member")
                        return redirect(url_for("member_today_races"))
                return _render_supabase_signup(
                    message="確認メールを送信しました。メール内のリンクから登録を完了してください。"
                )
            except Exception as e:
                _record_attempt(ip, False)
                return _render_supabase_signup(error=str(e)), 400
        return _render_supabase_signup()

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("index"))


def _render_supabase_login(error: str | None):
    return render_template_string(SUPABASE_LOGIN_TEMPLATE, error=error)


def _render_supabase_signup(error: str | None = None, message: str | None = None):
    return render_template_string(SUPABASE_SIGNUP_TEMPLATE, error=error, message=message)
