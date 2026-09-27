"""登録者向けの「期間限定特典」ボタンと、そのリンクを設定する管理画面。

リンク先 (Google Drive の共有リンク) は管理者が /admin/promo で入れる。コードを直さずに
差し替えられるよう、DB の site_settings 表に持つ (SQLite / PostgreSQL の両方)。

  * ボタンは会員 (無料登録者を含む) のメニューに出る。リンクが空、または期限を過ぎたら出さない。
  * ボタンは /promo を経由して Drive へ飛ぶ。/promo のアクセス数がそのまま「押された回数」になる。
  * リンクは https の drive.google.com / docs.google.com だけ受け付ける (別サイトへの誘導を防ぐ)。
  * ページを描くたびに DB を読まないよう、設定は各プロセスで 60 秒だけ覚えておく。
    DB が読めないときはボタンを出さないだけで、ページは止めない。
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime
from typing import Any
from urllib.parse import urlsplit

from flask import Blueprint, abort, redirect, render_template, request, session, url_for

from src.web.auth import _verify_csrf_token, admin_required, is_member

logger = logging.getLogger(__name__)
bp = Blueprint("promo", __name__)

KEY_URL, KEY_LABEL, KEY_UNTIL = "promo_url", "promo_label", "promo_until"
DEFAULT_LABEL = "期間限定特典"
ALLOWED_HOSTS = frozenset({"drive.google.com", "docs.google.com"})
MAX_LABEL = 20
CACHE_SECONDS = 60.0

_SCHEMA_READY = False
_cache: dict[str, Any] = {"at": 0.0, "value": None}
_cache_lock = threading.Lock()


# ---------------------------------------------------------------- 保存場所
def ensure_site_settings_table(conn: Any) -> None:
    """設定の表 (キーと値) を SQLite / PostgreSQL の両方で作る。"""
    global _SCHEMA_READY
    is_postgres = getattr(conn, "_kind", "") == "postgres"
    if is_postgres and _SCHEMA_READY:
        return
    if is_postgres:
        try:
            conn.execute("SELECT 1 FROM site_settings LIMIT 0")
            _SCHEMA_READY = True
            return
        except Exception:
            pass
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS site_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            updated_by TEXT
        )
        """
    )
    if is_postgres:
        _SCHEMA_READY = True


def load_settings(conn: Any, keys: tuple[str, ...], *, create: bool = True) -> dict[str, str]:
    """設定を読む。create=False なら表を作らない（表がまだ無ければ空）。"""
    if create:
        ensure_site_settings_table(conn)
    try:
        # 表は数行だけなので全部読んで絞る（SQL を組み立てない）
        rows = [r for r in conn.execute("SELECT key, value FROM site_settings").fetchall() if r[0] in keys]
    except Exception as exc:
        if create or "site_settings" not in str(exc):
            raise
        return {}  # 管理画面で一度も保存していない＝特典なし
    return {str(r[0]): str(r[1]) for r in rows}


def save_settings(conn: Any, values: dict[str, str], updated_by: str | None) -> None:
    ensure_site_settings_table(conn)
    now = datetime.now().isoformat(timespec="seconds")
    for key, value in values.items():
        conn.execute(
            """
            INSERT INTO site_settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?)
            ON CONFLICT (key) DO UPDATE SET value = excluded.value,
                updated_at = excluded.updated_at, updated_by = excluded.updated_by
            """,
            (key, value, now, updated_by),
        )


# ---------------------------------------------------------------- 入力の確認
def validate(url: str, label: str, until: str) -> tuple[dict[str, str], str | None]:
    """管理画面の入力を確かめる。(保存する値, エラー文) を返す。URL が空なら特典を止める。"""
    url, label, until = url.strip(), label.strip(), until.strip()
    if url:
        parts = urlsplit(url)
        if parts.scheme != "https" or (parts.hostname or "").lower() not in ALLOWED_HOSTS:
            return {}, "リンクは Google Drive（https://drive.google.com/…）の共有リンクを貼ってください。"
    if len(label) > MAX_LABEL:
        return {}, f"ボタンの文字は{MAX_LABEL}文字以内にしてください。"
    if until:
        try:
            date.fromisoformat(until)
        except ValueError:
            return {}, "期限は日付（例: 2026-10-31）で入れてください。"
    return {KEY_URL: url, KEY_LABEL: label or DEFAULT_LABEL, KEY_UNTIL: until}, None


def _active(values: dict[str, str], today: date) -> dict[str, str] | None:
    url = values.get(KEY_URL, "")
    if not url:
        return None
    until = values.get(KEY_UNTIL, "")
    if until:
        try:
            if today > date.fromisoformat(until):
                return None
        except ValueError:
            return None
    return {"url": url, "label": values.get(KEY_LABEL) or DEFAULT_LABEL, "until": until}


def _connect():
    from src.db.connection import connect

    return connect()


def _read_from_db(*, create: bool = False) -> dict[str, str]:
    # ページを描くたびの読み出しでは表を作らない（書き込みは管理画面だけ）
    with _connect() as conn:
        return load_settings(conn, (KEY_URL, KEY_LABEL, KEY_UNTIL), create=create)


def current_promo(today: date | None = None) -> dict[str, str] | None:
    """いま出すべき特典 (url / label / until)。無ければ None。60 秒だけ覚えておく。"""
    now = time.monotonic()
    with _cache_lock:
        fresh = _cache["value"] is not None and now - _cache["at"] < CACHE_SECONDS
        values = _cache["value"] if fresh else None
    if values is None:
        try:
            values = _read_from_db()
        except Exception as exc:  # 特典のためにページを止めない
            logger.warning("promo settings unavailable: %s", type(exc).__name__)
            values = {}
        with _cache_lock:
            _cache.update(at=now, value=values)
    return _active(values, today or date.today())


def clear_cache() -> None:
    with _cache_lock:
        _cache.update(at=0.0, value=None)


def member_promo() -> dict[str, str] | None:
    """テンプレート用: 会員にだけ特典を返す。"""
    if not is_member():
        return None
    return current_promo()


# ---------------------------------------------------------------- 画面
@bp.get("/promo")
def open_promo():
    """ボタンの行き先。会員だけ Drive へ送る (このページの表示数＝押された回数)。"""
    if not is_member():
        return redirect(url_for("login", next=request.path))
    promo = current_promo()
    if promo is None:
        abort(404)
    return redirect(promo["url"], code=302)


@bp.route("/admin/promo", methods=["GET", "POST"])
@admin_required
def admin_promo():
    error = message = None
    if request.method == "POST":
        if not _verify_csrf_token():
            abort(400)
        values, error = validate(
            request.form.get("url", ""), request.form.get("label", ""), request.form.get("until", "")
        )
        if error is None:
            with _connect() as conn:
                save_settings(conn, values, session.get("email"))
            clear_cache()
            message = "保存しました。" if values[KEY_URL] else "特典を止めました（ボタンは出ません）。"
    try:
        saved = _read_from_db(create=True)
    except Exception as exc:
        logger.warning("promo settings unavailable: %s", type(exc).__name__)
        saved, error = {}, error or "設定を読み込めませんでした。時間をおいて開き直してください。"
    form = {
        "url": request.form.get("url", saved.get(KEY_URL, "")) if error else saved.get(KEY_URL, ""),
        "label": (request.form.get("label", "") if error else saved.get(KEY_LABEL, "")) or DEFAULT_LABEL,
        "until": request.form.get("until", saved.get(KEY_UNTIL, "")) if error else saved.get(KEY_UNTIL, ""),
    }
    active = _active(saved, date.today())
    return render_template("admin_promo.html", form=form, active=active, error=error, message=message)
