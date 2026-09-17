"""サーバー側でページ表示数を数える。

**なぜ要るか**: 本番は Render のドメインで動いていて Cloudflare を経由していない。
そのため Cloudflare Web Analytics のビーコンが `cloudflareinsights.com/cdn-cgi/rum`
へ送る段階で CORS に弾かれ、実際のアクセスを取りこぼしている (2026-09-09 に
ブラウザのコンソールで確認)。広告ブロッカーでも同じことが起きる。
アプリ自身が数えれば、この経路の影響を受けない。

**設計上の約束 (ここを破ると本番が落ちる)**:
  * リクエスト処理中に **DB へ触らない**。この web は 1 リクエストあたりの
    DB チェックアウト予算を管理していて (begin_web_request_db_budget)、
    過去に接続プール枯渇を何度も起こしている。数えるのはメモリ上のカウンタだけ。
  * 書き込みは背後のスレッドがまとめて行う。gunicorn は 2 ワーカーなので
    プロセスごとに独立したカウンタを持ち、DB へは **加算** で持ち寄る。
  * 計測の失敗でページを壊さない。例外は全て握りつぶしてログに落とす。
"""
from __future__ import annotations

import logging
import os
import re
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))

# 数えないパス。静的ファイル・監視・API は「人が見たページ」ではない。
SKIP_PREFIXES = ("/static/", "/api/", "/healthz", "/favicon", "/robots.txt", "/.well-known/")
# User-Agent にこれらを含むものは機械とみなす。
BOT_MARKERS = (
    "bot", "crawler", "spider", "slurp", "crawling", "facebookexternalhit",
    "ia_archiver", "headlesschrome", "python-requests", "curl/", "wget/",
    "monitoring", "uptime", "pingdom", "lighthouse", "gtmetrix",
)
FLUSH_INTERVAL_SECONDS = 120

_CREDENTIAL_IN_URL = re.compile(r"://[^@\s/]+@")


def _safe_error(exc: BaseException) -> str:
    """/healthz に出す用。接続文字列の認証部分は伏せ、長さも抑える。"""
    text = _CREDENTIAL_IN_URL.sub("://***@", f"{type(exc).__name__}: {exc}")
    return " ".join(text.split())[:200]


def _now_text() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")


class AccessCounter:
    """メモリ上で日別に数え、定期的に台帳へ持ち寄る。"""

    def __init__(self, flush_interval: int = FLUSH_INTERVAL_SECONDS) -> None:
        self._counts: dict[str, int] = defaultdict(int)
        self._lock = threading.Lock()
        self._flush_interval = flush_interval
        self._timer: threading.Timer | None = None
        self._started = False
        # 本番で「数えているのに書けていない」を外から見分けるための記録
        # (2026-09-17 デプロイ後に台帳が空のままで、原因が見えなかった)。
        self._recorded = 0
        self._flushed_days = 0
        self._last_flush_at: str | None = None
        self._last_error: str | None = None
        self._last_error_at: str | None = None
        self._ticks = 0

    # ---- 計測 (リクエストの中で呼ばれる。DB へは触らない) ----

    @staticmethod
    def should_count(path: str, method: str, status: int, user_agent: str) -> bool:
        if method not in ("GET", "POST"):
            return False
        if status != 200:
            return False
        if any(path.startswith(p) for p in SKIP_PREFIXES):
            return False
        ua = (user_agent or "").lower()
        if not ua:
            return False  # UA なしはほぼ機械
        if any(marker in ua for marker in BOT_MARKERS):
            return False
        return True

    def record(self, path: str, method: str, status: int, user_agent: str) -> None:
        if not self.should_count(path, method, status, user_agent):
            return
        today = datetime.now(JST).strftime("%Y-%m-%d")
        with self._lock:
            self._counts[today] += 1
            self._recorded += 1

    def status(self) -> dict:
        """いまの状態 (DB へは触らない)。"""
        with self._lock:
            pending = sum(self._counts.values())
        return {
            "pid": os.getpid(),
            "started": self._started,
            "timer_alive": bool(self._timer and self._timer.is_alive()),
            "ticks": self._ticks,
            "recorded": self._recorded,
            "pending": pending,
            "flushed_days": self._flushed_days,
            "last_flush_at": self._last_flush_at,
            "last_error": self._last_error,
            "last_error_at": self._last_error_at,
        }

    # ---- 書き出し (背後のスレッド) ----

    def take(self) -> dict[str, int]:
        """溜まった分を取り出して空にする。"""
        with self._lock:
            taken = dict(self._counts)
            self._counts.clear()
        return taken

    def give_back(self, counts: dict[str, int]) -> None:
        """書き込みに失敗した分を戻す（次回に持ち越す）。"""
        with self._lock:
            for date, n in counts.items():
                self._counts[date] += n

    def flush(self) -> int:
        taken = self.take()
        if not taken:
            return 0
        try:
            from src.access_stats import add_page_views
            from src.db.connection import connect

            with connect() as conn:
                written = add_page_views(conn, taken)
            self._flushed_days += written
            self._last_flush_at = _now_text()
            logger.info("access counter flushed: %s", taken)
            return written
        except Exception as exc:  # 計測のためにサイトを止めない
            self.give_back(taken)
            self._last_error = _safe_error(exc)
            self._last_error_at = _now_text()
            logger.warning("access counter flush failed (will retry): %s", self._last_error)
            return 0

    # ---- 定期実行 ----

    def start(self) -> None:
        if self._started or self._flush_interval <= 0:
            return
        self._started = True
        self._schedule()

    def _schedule(self) -> None:
        timer = threading.Timer(self._flush_interval, self._tick)
        timer.daemon = True  # 終了を妨げない
        timer.start()
        self._timer = timer

    def _tick(self) -> None:
        self._ticks += 1
        try:
            self.flush()
        except BaseException as exc:  # flush 自体は握るが、念のため記録してタイマーは続ける
            self._last_error = _safe_error(exc)
            self._last_error_at = _now_text()
        finally:
            self._schedule()


counter = AccessCounter()
_install_state = {"installed": False, "disabled_by_env": False}


def status() -> dict:
    """/healthz 用の要約。"""
    return {**_install_state, **counter.status()}


def install(app) -> None:
    """Flask アプリに計測を取り付ける。"""
    if os.environ.get("BOATRACE_DISABLE_ACCESS_COUNTER", "").strip() == "1":
        _install_state["disabled_by_env"] = True
        return
    _install_state["installed"] = True

    @app.after_request
    def _count_page_view(response):
        try:
            from flask import request

            counter.record(
                request.path,
                request.method,
                response.status_code,
                request.headers.get("User-Agent", ""),
            )
        except Exception:  # 計測はページの成否に一切関与しない
            pass
        return response

    counter.start()
