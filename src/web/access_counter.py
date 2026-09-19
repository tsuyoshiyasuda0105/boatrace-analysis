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
SKIP_PREFIXES = ("/static/", "/api/", "/healthz", "/favicon", "/robots.txt", "/sitemap.xml", "/.well-known/")
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
        # LP(/start) の訪問を (日付, 流入元utm_source) 別に。全体PVとは別立て。
        self._landing: dict[tuple[str, str], int] = defaultdict(int)
        self._lock = threading.Lock()
        self._flush_interval = flush_interval
        self._timer: threading.Timer | None = None
        self._timer_pid: int | None = None
        self._start_lock = threading.Lock()
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

    @staticmethod
    def _clean_source(utm_source: str | None) -> str:
        """流入元ラベルを短く安全に整える（過剰・不正な値でテーブルを汚さない）。"""
        s = (utm_source or "").strip().lower()
        if not s:
            return "(direct)"
        # 想定外の長い/変な値は捨てる（英数と - _ . のみ・32文字まで）。
        if len(s) > 32 or not re.fullmatch(r"[a-z0-9._-]+", s):
            return "(other)"
        return s

    def record(self, path: str, method: str, status: int, user_agent: str,
               utm_source: str | None = None) -> None:
        if not self.should_count(path, method, status, user_agent):
            return
        today = datetime.now(JST).strftime("%Y-%m-%d")
        is_landing = path == "/start"
        source = self._clean_source(utm_source) if is_landing else None
        with self._lock:
            self._counts[today] += 1
            self._recorded += 1
            if is_landing:
                self._landing[(today, source)] += 1
        self._ensure_running()

    def status(self) -> dict:
        """いまの状態 (DB へは触らない)。"""
        with self._lock:
            pending = sum(self._counts.values())
        return {
            "pid": os.getpid(),
            "started": self._started,
            "timer_alive": self._timer_is_running(),
            "timer_pid": self._timer_pid,
            "ticks": self._ticks,
            "recorded": self._recorded,
            "pending": pending,
            "flushed_days": self._flushed_days,
            "last_flush_at": self._last_flush_at,
            "last_error": self._last_error,
            "last_error_at": self._last_error_at,
        }

    # ---- 書き出し (背後のスレッド) ----

    def take(self) -> tuple[dict[str, int], dict[tuple[str, str], int]]:
        """溜まった分を取り出して空にする（全体PV, LP流入元別）。"""
        with self._lock:
            taken = dict(self._counts)
            landing = dict(self._landing)
            self._counts.clear()
            self._landing.clear()
        return taken, landing

    def give_back(self, counts: dict[str, int],
                  landing: dict[tuple[str, str], int] | None = None) -> None:
        """書き込みに失敗した分を戻す（次回に持ち越す）。"""
        with self._lock:
            for date, n in counts.items():
                self._counts[date] += n
            for key, n in (landing or {}).items():
                self._landing[key] += n

    def flush(self) -> int:
        taken, landing = self.take()
        if not taken and not landing:
            return 0
        try:
            from src.access_stats import add_landing_views, add_page_views
            from src.db.connection import connect

            with connect() as conn:
                written = add_page_views(conn, taken)
                if landing:
                    add_landing_views(conn, landing)
            self._flushed_days += written
            self._last_flush_at = _now_text()
            logger.info("access counter flushed: pv=%s landing=%s", taken, landing)
            return written
        except Exception as exc:  # 計測のためにサイトを止めない
            self.give_back(taken, landing)
            self._last_error = _safe_error(exc)
            self._last_error_at = _now_text()
            logger.warning("access counter flush failed (will retry): %s", self._last_error)
            return 0

    # ---- 定期実行 ----

    def start(self) -> None:
        if self._flush_interval <= 0:
            return
        self._started = True
        self._ensure_running()

    def _timer_is_running(self) -> bool:
        timer = self._timer
        return (
            timer is not None
            and self._timer_pid == os.getpid()
            and timer.is_alive()
        )

    def _ensure_running(self) -> None:
        """書き込み係が今のプロセスで生きていなければ起こす。

        2026-09-17 本番で、ワーカーが「開始済み」の印だけを持ち、スレッド本体が
        無い状態 (timer_alive=false・ticks=0) になり、台帳に一度も書かれなかった。
        スレッドは fork で引き継がれないため、アプリの読み込みがワーカー複製より
        前に行われると起きる。起動のタイミングに頼らず、数えるついでに確かめる
        (DB には触らない・スレッドを 1 本起こすだけ)。
        """
        if not self._started or self._flush_interval <= 0 or self._timer_is_running():
            return
        with self._start_lock:
            if self._timer_is_running():
                return
            self._schedule()

    def _after_fork_in_child(self) -> None:
        """複製直後の子プロセス: 親の数・ロック・スレッドの記録を引き継がない。"""
        self._counts = defaultdict(int)
        self._landing = defaultdict(int)
        self._lock = threading.Lock()
        self._start_lock = threading.Lock()
        self._timer = None
        self._timer_pid = None

    def _schedule(self) -> None:
        timer = threading.Timer(self._flush_interval, self._tick)
        timer.daemon = True  # 終了を妨げない
        timer.start()
        self._timer = timer
        self._timer_pid = os.getpid()

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

if hasattr(os, "register_at_fork"):  # Windows には無い
    os.register_at_fork(after_in_child=counter._after_fork_in_child)


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
                utm_source=request.args.get("utm_source"),
            )
        except Exception:  # 計測はページの成否に一切関与しない
            pass
        return response

    counter.start()
