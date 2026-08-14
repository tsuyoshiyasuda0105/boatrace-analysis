"""
Layer 3 共通 HTTP クライアント

- リクエスト間隔 (REQUEST_INTERVAL_SECONDS) をプロセス内で厳守
- 5xx・接続エラーはリトライ、4xx (404 等) は即終了
- レスポンス文字コードは apparent_encoding で誤判定を回避
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional
from urllib.parse import urlparse

import requests
from requests.exceptions import ConnectionError

import config

logger = logging.getLogger(__name__)

_session: Optional[requests.Session] = None
_lock = threading.Lock()
_last_request_at: float = 0.0

# ============================================================
# プロセス横断の共有レートリミッタ (P0-3)
#
# Render では同一 cadence の複数 cron (odds / regular / exhibition-detail)
# が並走し、プロセス内リミッタだけでは boatrace.jp への合算リクエスト間隔が
# REQUEST_INTERVAL_SECONDS を下回る。DATABASE_URL が Postgres の場合は
# DB 上の 1 行/host を「リクエスト枠」として奪い合うことで、全プロセス
# 合算でも間隔が守られるようにする。
#
# フェイルセーフ: DB に届かない場合は従来のプロセス内リミッタに自動
# フォールバックする (止血が本体を殺さない)。
# ============================================================

# 共有スロット取得の再試行パラメータ
SHARED_SLOT_POLL_SECONDS = 0.4
SHARED_SLOT_MAX_WAIT_SECONDS = 30.0
# DB 障害時に共有リミッタを一時停止する秒数 (毎リクエストで接続失敗を繰り返さない)
_SHARED_FAILURE_COOLDOWN_SECONDS = 300.0

_shared_conn = None
_shared_conn_lock = threading.Lock()
_shared_disabled_until: float = 0.0


def _shared_rate_limit_enabled() -> bool:
    """env BOATRACE_SHARED_RATE_LIMIT (デフォルト有効) + Postgres 接続が条件。"""
    flag = os.getenv("BOATRACE_SHARED_RATE_LIMIT", "1").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return False
    try:
        from src.db.connection import targets_postgres

        return targets_postgres()
    except Exception:  # noqa: BLE001
        return False


def _rate_limit_host(url: str) -> str:
    """URL からレート制限の単位となる host キーを求める。"""
    netloc = (urlparse(url).netloc or "").lower()
    for root in ("boatrace.jp", "mbrace.or.jp"):
        if netloc == root or netloc.endswith("." + root):
            return root
    return netloc or "unknown"


def _get_shared_conn():
    """共有スロット用の直結 Postgres 接続 (使い回し)。取れなければ None。"""
    global _shared_conn
    with _shared_conn_lock:
        if _shared_conn is not None:
            return _shared_conn
        from src.db.connection import connect

        conn = connect(direct=True)
        if getattr(conn, "_kind", "sqlite") != "postgres":
            # SQLite ローカル環境では共有リミッタは使わない
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
            return None
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scrape_rate_slots (
                host TEXT PRIMARY KEY,
                last_request_at TIMESTAMPTZ NOT NULL
            )
            """
        )
        _shared_conn = conn
        return _shared_conn


def _drop_shared_conn() -> None:
    global _shared_conn
    with _shared_conn_lock:
        if _shared_conn is not None:
            try:
                _shared_conn.close()
            except Exception:  # noqa: BLE001
                pass
            _shared_conn = None


def _acquire_shared_slot(
    conn,
    host: str,
    interval_seconds: float,
    *,
    max_wait_seconds: float = SHARED_SLOT_MAX_WAIT_SECONDS,
    poll_seconds: float = SHARED_SLOT_POLL_SECONDS,
) -> bool:
    """DB 上の host 行を CAS 的に更新してリクエスト枠を 1 つ取得する。

    UPDATE が行を返す = 前回リクエストから interval_seconds 以上経過しており、
    now() へ更新して枠を得たことを意味する。行が返るまで poll_seconds で
    リトライ (上限 max_wait_seconds)。取得できなければ False。
    """
    conn.execute(
        """
        INSERT INTO scrape_rate_slots (host, last_request_at)
        VALUES (?, to_timestamp(0))
        ON CONFLICT (host) DO NOTHING
        """,
        (host,),
    )
    deadline = time.monotonic() + max_wait_seconds
    while True:
        cur = conn.execute(
            """
            UPDATE scrape_rate_slots
               SET last_request_at = now()
             WHERE host = ?
               AND last_request_at <= now() - make_interval(secs => ?)
            RETURNING 1
            """,
            (host, float(interval_seconds)),
        )
        if cur.fetchone():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll_seconds)


def _try_shared_slot(host: str) -> bool:
    """共有スロット取得を試みる。取得できたら True。

    無効/非Postgres/DB障害/待機タイムアウトでは False を返し、呼び出し側は
    プロセス内リミッタへフォールバックする。
    """
    global _shared_disabled_until
    if not _shared_rate_limit_enabled():
        return False
    if time.monotonic() < _shared_disabled_until:
        return False
    try:
        conn = _get_shared_conn()
        if conn is None:
            return False
        got = _acquire_shared_slot(conn, host, config.REQUEST_INTERVAL_SECONDS)
        if not got:
            logger.warning(
                "shared rate slot wait timed out host=%s → local limiter", host
            )
        return got
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "shared rate limiter unavailable (%s) → local limiter for %.0fs",
            exc,
            _SHARED_FAILURE_COOLDOWN_SECONDS,
        )
        _shared_disabled_until = time.monotonic() + _SHARED_FAILURE_COOLDOWN_SECONDS
        _drop_shared_conn()
        return False


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        s = requests.Session()
        # ブラウザらしい一般的なヘッダ一式 (BAN 検知のヒューリスティクスを回避)
        s.headers.update({
            "User-Agent": config.USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                     "image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "ja,en-US;q=0.7,en;q=0.3",
            # Do not advertise Brotli unconditionally.  requests can only
            # decode `br` when an optional Brotli package is installed; the
            # Render cron image does not include one.
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        })
        _session = s
    return _session


def _referer_for(url: str) -> Optional[str]:
    """boatrace.jp 内のページに対する自然な Referer を作る。
    例: /owpc/pc/race/odds3t?... に対しては /owpc/pc/race/index?hd=... を返す。
    """
    if "boatrace.jp" not in url:
        return None
    # 同じドメインのトップが妥当
    return "https://www.boatrace.jp/owpc/pc/race/index"


def _wait_interval(host: Optional[str] = None) -> None:
    """リクエスト間隔 (REQUEST_INTERVAL_SECONDS) を厳守する。

    1. host 指定 + 共有リミッタ有効 (Postgres): DB 上の host 行から枠を取得。
       複数プロセス合算でも間隔が守られる。
    2. それ以外 / DB 不通: 従来どおりプロセス内リミッタで待つ。
    共有枠が取れた場合もプロセス内タイムスタンプを更新し、直後に
    フォールバックが起きても間隔が縮まないようにする。
    """
    global _last_request_at
    if host and _try_shared_slot(host):
        with _lock:
            _last_request_at = time.monotonic()
        return
    with _lock:
        elapsed = time.monotonic() - _last_request_at
        if elapsed < config.REQUEST_INTERVAL_SECONDS:
            time.sleep(config.REQUEST_INTERVAL_SECONDS - elapsed)
        _last_request_at = time.monotonic()


def fetch_html(url: str) -> Optional[str]:
    """
    HTML を取得。

    戻り値:
      - 成功: HTML 文字列 (UTF-8 デコード済)
      - 404: None (ページ未公開・中止レース等)
      - リトライ上限超過: None
    """
    session = _get_session()

    referer = _referer_for(url)
    extra_headers = {"Referer": referer} if referer else {}

    for attempt in range(1, config.LAYER3_MAX_RETRIES + 1):
        _wait_interval(_rate_limit_host(url))
        try:
            resp = session.get(url, timeout=config.REQUEST_TIMEOUT_SECONDS,
                               headers=extra_headers)
        except requests.RequestException as e:
            logger.warning("HTTP error attempt=%d url=%s err=%s", attempt, url, e)
            # DNS / name resolution failures never recover within the same run.
            cause = getattr(e, "__cause__", None)
            message = f"{e} {cause or ''}".lower()
            if isinstance(e, ConnectionError) and (
                "nameresolutionerror" in message
                or "getaddrinfo failed" in message
                or "failed to resolve" in message
            ):
                return None
            if attempt < config.LAYER3_MAX_RETRIES:
                time.sleep(config.LAYER3_RETRY_BACKOFF_SECONDS)
                continue
            return None

        if resp.status_code == 404:
            logger.info("not found 404: %s", url)
            return None

        if 500 <= resp.status_code < 600:
            logger.warning("server error %d attempt=%d url=%s", resp.status_code, attempt, url)
            if attempt < config.LAYER3_MAX_RETRIES:
                time.sleep(config.LAYER3_RETRY_BACKOFF_SECONDS)
                continue
            return None

        if resp.status_code >= 400:
            logger.warning("client error %d url=%s", resp.status_code, url)
            return None

        # boatrace.jp は UTF-8 だが念のため
        if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
            resp.encoding = resp.apparent_encoding or "utf-8"
        return resp.text

    return None
