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

import hashlib
import hmac
import logging
import secrets
import time
from functools import wraps

from flask import (
    abort,
    current_app,
    jsonify,
    redirect,
    render_template,
    render_template_string,
    request,
    session,
    url_for,
)

import config
from src.web import supabase_auth_client
from src.web.membership import ensure_profile, get_effective_role, list_membership_overview, role_allows

logger = logging.getLogger(__name__)

# ===== ブルートフォース対策: IP 別の試行カウンタ (in-memory) =====
# {ip: [(timestamp, success_bool), ...]} 直近 15 分のみ保持
_LOGIN_ATTEMPTS: dict[str, list[tuple[float, bool]]] = {}
_SUPABASE_ROLE_REFRESH_TTL_SEC = 60
_SUPABASE_ROLE_REFRESH_RETRY_SEC = 15
_SUPABASE_ROLE_MAX_STALE_SEC = 900
_SUPABASE_ROLE_CHECKED_AT_SESSION_KEY = "supabase_role_checked_at"
_SUPABASE_ROLE_RETRY_AT_SESSION_KEY = "supabase_role_retry_at"
_SUPABASE_ROLE_PENDING_AT_SESSION_KEY = "supabase_role_pending_at"
_SUPABASE_MEMBER_ROLES = frozenset(
    {"free_member", "beta_member", "paid_member", "admin"}
)
_LOCKOUT_THRESHOLD = 10      # 15分以内に失敗10回でロック
_LOCKOUT_WINDOW_SEC = 900    # 15 分
_LOCKOUT_DURATION_SEC = 1800  # ロック後30分はログイン不可


def _client_ip() -> str:
    """ProxyFix で補正済みのクライアント IP を取得。"""
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
    r"""オープンリダイレクト対策。
    next が以下のいずれかなら安全、それ以外は default に。
      - / で始まる相対パス (例: /races?date=2026-05-12)
    NG:
      - // で始まる (プロトコル相対 URL)
      - http:// https:// で始まる絶対 URL
      - \\ や @ などの細工
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


def current_auth_provider() -> str:
    return session.get("auth_provider") or "none"


def is_supabase_auth_enabled() -> bool:
    return supabase_auth_client.is_configured()


def is_admin() -> bool:
    return role_allows(current_role(), "admin")


def is_paid_member() -> bool:
    return current_role() == "test_viewer" or role_allows(current_role(), "paid_member")


def can_use_backtest() -> bool:
    """Return whether the current session may use the Backtest feature."""
    return current_role() in {
        "beta_member",
        "paid_member",
        "admin",
        # Preserve the existing read-only Playwright inspection role.
        "test_viewer",
    }


def guest_access_enabled() -> bool:
    """Evaluate the emergency guest-access switch for every request."""
    import os

    return os.getenv("BOATRACE_GUEST_ACCESS", "1") != "0"


def is_playwright_test_viewer() -> bool:
    return (
        current_role() == "test_viewer"
        and current_auth_provider() == "playwright_password"
    )


def _playwright_password_is_safe() -> bool:
    password = getattr(config, "WEB_PLAYWRIGHT_PASSWORD", "")
    if len(password) < 16:
        return False
    return not _safe_password_check(password, config.WEB_MEMBER_PASSWORD)


def _playwright_password_version() -> str:
    if not _playwright_password_is_safe():
        return ""
    return hmac.new(
        config.WEB_SESSION_SECRET.encode("utf-8"),
        config.WEB_PLAYWRIGHT_PASSWORD.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _set_supabase_session(
    user_id: str,
    email: str | None,
    role: str,
    *,
    role_validated: bool = True,
) -> None:
    session.clear()
    session["is_member"] = role in {
        "free_member",
        "beta_member",
        "paid_member",
        "admin",
    }
    session["user_id"] = user_id
    session["email"] = email
    session["role"] = role
    session["auth_provider"] = "supabase"
    now = time.time()
    if role_validated:
        session[_SUPABASE_ROLE_CHECKED_AT_SESSION_KEY] = now
    else:
        session[_SUPABASE_ROLE_PENDING_AT_SESSION_KEY] = now
    session.permanent = True


def _set_test_session_role(role: str) -> None:
    session.clear()
    session["is_member"] = role in {
        "free_member",
        "beta_member",
        "paid_member",
        "admin",
    }
    session["role"] = role
    session["auth_provider"] = "playwright_test"
    session.permanent = True


def _is_transient_db_error(exc: Exception) -> bool:
    return isinstance(exc, TimeoutError) or exc.__class__.__module__.startswith(
        ("psycopg", "psycopg_pool")
    )


def _playwright_test_login_enabled() -> bool:
    try:
        return bool(current_app and current_app.config.get("TESTING"))
    except Exception:
        return False


def _refresh_supabase_membership_session() -> None:
    if session.get("auth_provider") != "supabase":
        return
    user_id = session.get("user_id")
    if not user_id:
        session.clear()
        return
    now = time.time()
    try:
        checked_at = float(session.get(_SUPABASE_ROLE_CHECKED_AT_SESSION_KEY) or 0)
    except (TypeError, ValueError):
        checked_at = 0
    try:
        retry_at = float(session.get(_SUPABASE_ROLE_RETRY_AT_SESSION_KEY) or 0)
    except (TypeError, ValueError):
        retry_at = 0
    try:
        pending_at = float(session.get(_SUPABASE_ROLE_PENDING_AT_SESSION_KEY) or 0)
    except (TypeError, ValueError):
        pending_at = 0
    if now < retry_at:
        return
    if 0 <= now - checked_at < _SUPABASE_ROLE_REFRESH_TTL_SEC:
        return
    try:
        role = get_effective_role(str(user_id))
    except Exception as exc:
        if not _is_transient_db_error(exc):
            raise
        cached_role = str(session.get("role") or "")
        cached_role_is_valid = (
            cached_role in _SUPABASE_MEMBER_ROLES
            and checked_at > 0
            and 0 <= now - checked_at <= _SUPABASE_ROLE_MAX_STALE_SEC
        )
        pending_free_member_is_valid = (
            cached_role == "free_member"
            and pending_at > 0
            and 0 <= now - pending_at <= _SUPABASE_ROLE_MAX_STALE_SEC
        )
        if not (cached_role_is_valid or pending_free_member_is_valid):
            logger.warning("Supabase role refresh unavailable; clearing unvalidated session")
            session.clear()
            return
        session[_SUPABASE_ROLE_RETRY_AT_SESSION_KEY] = (
            now + _SUPABASE_ROLE_REFRESH_RETRY_SEC
        )
        logger.warning(
            "Supabase role refresh unavailable; using recently validated cached role",
            exc_info=True,
        )
        return
    session["role"] = role
    session["is_member"] = role in _SUPABASE_MEMBER_ROLES
    session[_SUPABASE_ROLE_CHECKED_AT_SESSION_KEY] = now
    session.pop(_SUPABASE_ROLE_RETRY_AT_SESSION_KEY, None)
    session.pop(_SUPABASE_ROLE_PENDING_AT_SESSION_KEY, None)


def login_required(view):
    """会員限定の画面ビュー (HTML) → 未ログインなら /login へリダイレクト"""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not is_member():
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapper


def guest_access_or_login_required(view):
    """Allow public race pages unless the request-time kill switch is off."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not is_member() and not guest_access_enabled():
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapper


def guest_access_or_member_api(view):
    """公開レース詳細の描画に使う API。

    レース詳細ページ自体は公開なのに、ページ内から呼ぶモーター履歴・選手詳細・
    タグの API が会員限定のままだと、未ログインの閲覧者にはページの一部が
    開かないまま残る (2026-08-24 に発見)。ページを公開にするなら、その描画に
    必要な API も同じ扱いに揃える。ゲスト閲覧の停止スイッチ
    (BOATRACE_GUEST_ACCESS=0) を切った時だけ会員限定に戻る。

    EV / Value Bet は会員価値の中核なので、この扱いには含めない。
    """
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not is_member() and not guest_access_enabled():
            return jsonify({"error": "unauthorized", "message": "会員ログインが必要です"}), 401
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


def admin_only_api(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not is_member():
            return jsonify({"error": "unauthorized", "message": "会員ログインが必要です"}), 401
        if not is_admin():
            return jsonify({"error": "forbidden", "message": "管理者のみ利用できます"}), 403
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


SUPABASE_RESET_PASSWORD_TEMPLATE = """
{% extends "base.html" %}
{% block title %}パスワード再設定{% endblock %}
{% block content %}
<div class="login-wrap">
  <h2>パスワード再設定</h2>
  <p class="login-hint">
    メールのリセットリンクから開いた場合だけ、新しいパスワードを設定できます。
  </p>
  <div id="reset-message" class="login-hint"></div>
  <form id="reset-form" class="login-form" style="display:none">
    <label>
      <span>新しいパスワード</span>
      <input id="new-password" type="password" autocomplete="new-password" minlength="8" required autofocus>
    </label>
    <label>
      <span>確認</span>
      <input id="confirm-password" type="password" autocomplete="new-password" minlength="8" required>
    </label>
    <button type="submit">パスワードを更新</button>
  </form>
  <p class="login-hint"><a href="{{ url_for('login_supabase') }}">ログイン画面へ戻る</a></p>
</div>
<script>
(function () {
  const supabaseUrl = {{ supabase_url|tojson }};
  const publishableKey = {{ publishable_key|tojson }};
  const msg = document.getElementById("reset-message");
  const form = document.getElementById("reset-form");
  const password = document.getElementById("new-password");
  const confirmPassword = document.getElementById("confirm-password");

  function show(text, isError) {
    msg.textContent = text;
    msg.className = isError ? "login-error" : "login-hint";
  }

  function paramsFromHash() {
    const raw = window.location.hash && window.location.hash.startsWith("#")
      ? window.location.hash.slice(1)
      : "";
    return new URLSearchParams(raw);
  }

  const params = paramsFromHash();
  const accessToken = params.get("access_token");
  const tokenType = params.get("token_type") || "bearer";
  const flowType = params.get("type");

  if (!supabaseUrl || !publishableKey) {
    show("Supabase Authの設定がまだ完了していません。", true);
    return;
  }
  if (!accessToken || (flowType && flowType !== "recovery")) {
    show("有効なリセットリンクではありません。Supabaseから最新のReset Passwordメールを再送してください。", true);
    return;
  }

  form.style.display = "";
  show("新しいパスワードを入力してください。", false);

  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    const newPassword = password.value;
    if (newPassword.length < 8) {
      show("パスワードは8文字以上にしてください。", true);
      return;
    }
    if (newPassword !== confirmPassword.value) {
      show("確認用パスワードが一致していません。", true);
      return;
    }

    form.querySelector("button").disabled = true;
    show("更新しています...", false);
    try {
      const response = await fetch(supabaseUrl.replace(/\\/$/, "") + "/auth/v1/user", {
        method: "PUT",
        headers: {
          "apikey": publishableKey,
          "Authorization": tokenType.charAt(0).toUpperCase() + tokenType.slice(1) + " " + accessToken,
          "Content-Type": "application/json"
        },
        body: JSON.stringify({ password: newPassword })
      });
      const payload = await response.json().catch(function () { return {}; });
      if (!response.ok) {
        throw new Error(payload.msg || payload.message || payload.error_description || "パスワード更新に失敗しました。");
      }
      history.replaceState(null, document.title, window.location.pathname);
      form.style.display = "none";
      show("パスワードを更新しました。ログイン画面から新しいパスワードでログインしてください。", false);
    } catch (error) {
      show(error.message || "パスワード更新に失敗しました。", true);
      form.querySelector("button").disabled = false;
    }
  });
})();
</script>
{% endblock %}
"""


def register_auth_routes(app):
    # テンプレートから _safe_redirect_url を使えるように (next の事前検証用)
    app.jinja_env.globals["safe_next"] = _safe_redirect_url
    # テンプレートから {{ csrf_token() }} を使えるように
    app.jinja_env.globals["csrf_token"] = _get_csrf_token

    @app.before_request
    def _sync_supabase_auth_role():
        if (
            request.endpoint == "static"
            or request.path.startswith("/static/")
            or request.path.startswith("/race/")
            or request.path == "/api/session-navigation"
            or request.path in {"/", "/races", "/favicon.ico", "/healthz"}
        ):
            return None
        _refresh_supabase_membership_session()

    @app.before_request
    def _expire_rotated_playwright_session():
        if session.get("auth_provider") != "playwright_password":
            return None
        expected_version = _playwright_password_version()
        session_version = str(session.get("playwright_password_version") or "")
        if expected_version and hmac.compare_digest(session_version, expected_version):
            return None
        session.clear()
        if request.path.startswith("/api/"):
            return jsonify({"error": "unauthorized", "message": "Playwright test session expired"}), 401
        return redirect(url_for("login", next=request.path))

    @app.before_request
    def _enforce_playwright_read_only():
        if is_playwright_test_viewer() and request.method not in {"GET", "HEAD", "OPTIONS"}:
            return jsonify({"error": "forbidden", "message": "Playwright test viewer is read-only"}), 403

    if (
        getattr(config, "WEB_PLAYWRIGHT_PASSWORD", "")
        and not _playwright_password_is_safe()
    ):
        logger.critical(
            "SECURITY: BOATRACE_PLAYWRIGHT_PASSWORD must be 16+ characters and "
            "differ from BOATRACE_MEMBER_PASSWORD; dedicated test login is disabled."
        )

    @app.route("/api/session-navigation", methods=["GET"])
    def session_navigation():
        """Return cookie-only header state for shared cache-neutral HTML."""
        payload: dict[str, object] = {"is_member": is_member()}
        if is_member():
            items = [
                {
                    "href": url_for("member_today_races"),
                    "icon": "今",
                    "label": "本日のレース",
                    "class_name": "nav-btn-today",
                },
                {
                    "href": url_for("kachisuji.index"),
                    "icon": "検",
                    "label": "バックテスト",
                    "class_name": "",
                },
                {
                    "href": url_for("signup.plan"),
                    "icon": "申",
                    "label": "プラン申込",
                    "class_name": "",
                },
            ]
            if is_admin():
                items.extend(
                    [
                        {
                            "href": url_for("member_strategy"),
                            "icon": "ROI",
                            "label": "ROI",
                            "class_name": "nav-btn-roi",
                        },
                        {
                            "href": url_for("member_strategy_monthly"),
                            "icon": "月",
                            "label": "月別推移",
                            "class_name": "nav-btn-monthly",
                        },
                        {
                            "href": url_for("member_health"),
                            "icon": "健",
                            "label": "健全度",
                            "class_name": "nav-btn-health",
                        },
                        {
                            "href": url_for("member_accidents"),
                            "icon": "注",
                            "label": "事故率",
                            "class_name": "nav-btn-accidents",
                        },
                    ]
                )
                if "start_prediction.prediction_metrics_page" in app.view_functions:
                    items.append(
                        {
                            "href": url_for("start_prediction.prediction_metrics_page"),
                            "icon": "ST",
                            "label": "展示精度",
                            "class_name": "",
                        }
                    )
                items.append(
                    {
                        "href": url_for("admin_memberships"),
                        "icon": "管",
                        "label": "管理",
                        "class_name": "nav-btn-health",
                    }
                )
            payload.update(
                {
                    "home_url": items[0]["href"],
                    "home_title": "本日のROI候補一覧",
                    "items": items,
                    "role_label": f"{current_role()} / {current_auth_provider()}",
                    "is_admin": is_admin(),
                    "logout_url": url_for("logout"),
                }
            )
        response = jsonify(payload)
        response.headers["Cache-Control"] = "private, no-store, max-age=0"
        response.headers["Vary"] = "Cookie"
        return response

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
            # 2026-08-24 第3段階: 共有パスワードによる会員ログインを廃止した。
            # 会員認証は Supabase (メール + パスワード) のみ。合鍵を 1 本配って
            # 回す方式は、渡した相手を絞れず、失効もできない。読み取り専用の
            # Playwright 検査ログインだけは別枠として残す (画面収録の自動化に要る)。
            playwright_match = (
                _playwright_password_is_safe()
                and _safe_password_check(pw, config.WEB_PLAYWRIGHT_PASSWORD)
            )
            if playwright_match:
                _record_attempt(ip, True)
                session.clear()  # セッション固定攻撃対策
                session["is_member"] = True
                session["role"] = "test_viewer"
                session["auth_provider"] = "playwright_password"
                session["playwright_password_version"] = _playwright_password_version()
                session.permanent = True
                # オープンリダイレクト対策: next が外部 URL なら index へ
                next_url = _safe_redirect_url(request.form.get("next", ""), url_for("index"))
                return redirect(next_url)
            _record_attempt(ip, False)
            # 短時間の遅延 (timing attack の更なる緩和)
            time.sleep(0.3)
            return render_template_string(LOGIN_TEMPLATE, error="パスワードが違います"), 401
        # 会員は Supabase 側で受け付ける。共有パスワードの入力欄を出し続けると
        # 「まだ使える」と誤解させるので、設定されているなら素直に送る。
        if supabase_auth_client.is_configured():
            return redirect(url_for("login_supabase", next=request.args.get("next", "")))
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
            except supabase_auth_client.SupabaseAuthError as e:
                logger.warning("supabase authentication rejected for %s from %s", email, ip)
                _record_attempt(ip, False)
                time.sleep(0.3)
                return _render_supabase_login(str(e)), 401
            except Exception:
                logger.exception("supabase authentication unavailable for %s from %s", email, ip)
                return _render_supabase_login(
                    "Supabase認証に一時的に接続できません。少し待ってから再度お試しください。"
                ), 503
            try:
                ensure_profile(auth_session.user_id, auth_session.email)
                role = get_effective_role(auth_session.user_id)
            except Exception as e:
                if _is_transient_db_error(e):
                    logger.error(
                        "supabase login membership lookup unavailable for %s from %s",
                        email,
                        ip,
                        exc_info=True,
                    )
                    _record_attempt(ip, True)
                    _set_supabase_session(
                        auth_session.user_id,
                        auth_session.email,
                        "free_member",
                        role_validated=False,
                    )
                    next_url = _safe_redirect_url(
                        request.form.get("next", ""), url_for("index")
                    )
                    return redirect(next_url)
                logger.exception("supabase login processing failed for %s from %s", email, ip)
                return _render_supabase_login(
                    "ログイン処理で一時的な問題が発生しました。少し待ってから再度お試しください。"
                ), 503
            _record_attempt(ip, True)
            _set_supabase_session(auth_session.user_id, auth_session.email, role)
            next_url = _safe_redirect_url(request.form.get("next", ""), url_for("index"))
            return redirect(next_url)
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

    @app.route("/reset-password", methods=["GET"])
    def reset_password():
        if not supabase_auth_client.is_configured():
            abort(404)
        return render_template_string(
            SUPABASE_RESET_PASSWORD_TEMPLATE,
            supabase_url=config.SUPABASE_URL,
            publishable_key=config.SUPABASE_PUBLISHABLE_KEY,
        )

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("index"))

    @app.route("/test/login-as/<role>", methods=["GET"])
    def test_login_as(role: str):
        if not _playwright_test_login_enabled():
            abort(404)
        if role not in {"guest", "free_member", "paid_member", "admin"}:
            abort(404)
        _set_test_session_role(role)
        next_url = _safe_redirect_url(request.args.get("next", ""), url_for("index"))
        return redirect(next_url)

    @app.route("/test/logout", methods=["GET"])
    def test_logout():
        if not _playwright_test_login_enabled():
            abort(404)
        session.clear()
        return redirect(_safe_redirect_url(request.args.get("next", ""), url_for("index")))

    @app.route("/admin/memberships", methods=["GET"])
    @admin_required
    def admin_memberships():
        return render_template(
            "admin_memberships.html",
            rows=list_membership_overview(),
        )


def _render_supabase_login(error: str | None):
    return render_template_string(SUPABASE_LOGIN_TEMPLATE, error=error)


def _render_supabase_signup(error: str | None = None, message: str | None = None):
    return render_template_string(SUPABASE_SIGNUP_TEMPLATE, error=error, message=message)
