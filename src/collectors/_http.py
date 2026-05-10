"""
Layer 3 共通 HTTP クライアント

- リクエスト間隔 (REQUEST_INTERVAL_SECONDS) をプロセス内で厳守
- 5xx・接続エラーはリトライ、4xx (404 等) は即終了
- レスポンス文字コードは apparent_encoding で誤判定を回避
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import requests

import config

logger = logging.getLogger(__name__)

_session: Optional[requests.Session] = None
_lock = threading.Lock()
_last_request_at: float = 0.0


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        s = requests.Session()
        s.headers.update({"User-Agent": config.USER_AGENT})
        _session = s
    return _session


def _wait_interval() -> None:
    """前回リクエストからの経過時間が REQUEST_INTERVAL_SECONDS 未満なら sleep"""
    global _last_request_at
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

    for attempt in range(1, config.LAYER3_MAX_RETRIES + 1):
        _wait_interval()
        try:
            resp = session.get(url, timeout=config.REQUEST_TIMEOUT_SECONDS)
        except requests.RequestException as e:
            logger.warning("HTTP error attempt=%d url=%s err=%s", attempt, url, e)
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
