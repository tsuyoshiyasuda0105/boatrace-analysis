"""ERROR レベル以上のログを Gmail / SMTP / HTTP API でメール通知する logging handler

backlog item 6: 重大エラーが起きた時に Gmail に即時通知。

特徴:
  - ERROR レベル以上のみ送信 (CRITICAL も含む)
  - 同一メッセージ (key) は 1 時間 1 通までレート制限 (スパム防止)
  - 既存の src/notifications/mailer.py の送信バックエンドを使う
    (Brevo HTTP API / Resend HTTP API / SMTP / コンソール stub の自動切替)
  - 受信先は BOATRACE_ERROR_NOTIFY_TO 環境変数で指定

使い方 (Flask):
    from src.notifications.error_handler import install_error_notifier
    install_error_notifier(app.logger)
    # ルートロガーにも仕込む
    install_error_notifier(logging.getLogger())
"""
from __future__ import annotations

import logging
import os
import time
import traceback
from collections import defaultdict
from datetime import datetime

from src.notifications.incident_ledger import normalize_incident_key, record_incident


class EmailErrorHandler(logging.Handler):
    """ERROR/CRITICAL を検知してメール送信。同一エラーの連投は抑制。"""

    def __init__(self, to_addr: str = "", rate_limit_sec: int = 3600):
        super().__init__(level=logging.ERROR)
        self.to_addr = to_addr
        self.rate_limit_sec = rate_limit_sec
        # メッセージ key → 最終送信時刻
        self._last_sent: dict[str, float] = defaultdict(float)

    def _key(self, record: logging.LogRecord) -> str:
        """Return a stable error-family key, excluding per-event statistics."""
        error_type = (
            record.exc_info[0].__name__
            if record.exc_info and record.exc_info[0]
            else record.levelname
        )
        return normalize_incident_key(
            record.name,
            record.getMessage() or "",
            error_type=error_type,
        )

    def emit(self, record: logging.LogRecord) -> None:
        key = self._key(record)
        notified = False
        detail: dict[str, object] = {
            "level": record.levelname,
            "module": record.module,
            "line": record.lineno,
            "function": record.funcName,
            "pathname": record.pathname,
        }
        try:
            now = time.time()
            last = self._last_sent.get(key, 0)
            if now - last < self.rate_limit_sec:
                # レート制限中 → スキップ (本人ログには出る)
                return
            if not self.to_addr:
                return
            self._last_sent[key] = now

            # 送信内容組み立て
            subject = f"[BOATRACE ERROR] {record.levelname}: {record.name} - {(record.getMessage() or '')[:80]}"
            body_lines = [
                f"時刻 : {datetime.now().isoformat(timespec='seconds')}",
                f"レベル: {record.levelname}",
                f"場所  : {record.name} | {record.module}:{record.lineno} | {record.funcName}()",
                f"パス  : {record.pathname}",
                "",
                "--- メッセージ ---",
                record.getMessage(),
            ]
            if record.exc_info:
                detail["traceback"] = "".join(traceback.format_exception(*record.exc_info))
                body_lines.append("")
                body_lines.append("--- トレースバック ---")
                body_lines.append(str(detail["traceback"]))
            elif record.stack_info:
                detail["stack"] = record.stack_info
                body_lines.append("")
                body_lines.append("--- スタック ---")
                body_lines.append(record.stack_info)
            body_text = "\n".join(body_lines)

            # 既存の mailer 経由で送信 (Brevo/Resend/SMTP 自動切替)
            # 循環 import 防止のためここで遅延 import
            from src.notifications.mailer import _send  # noqa: WPS437
            notified = bool(_send(self.to_addr, subject, body_text, body_html=None))
        except Exception:
            # 通知失敗が本処理を止めないように吸収。stderr にだけ落とす
            self.handleError(record)
        finally:
            if not getattr(record, "_incident_ledger_recorded", False):
                record._incident_ledger_recorded = True
                try:
                    record_incident(
                        category="app_error",
                        source=record.name,
                        title=(record.getMessage() or record.levelname)[:160],
                        detail=detail,
                        severity="error",
                        dedup_key=key,
                        notified=notified,
                    )
                except Exception:  # noqa: BLE001
                    # A mocked or replaced ledger must still never break logging.
                    pass


def install_error_notifier(logger_obj: logging.Logger) -> bool:
    """指定ロガーに EmailErrorHandler を 1 個だけ仕込む。

    BOATRACE_ERROR_NOTIFY_TO が未設定でも台帳用 handler は追加するが False を返す。
    既に仕込まれていれば追加しない (重複防止)。

    Returns:
        True: handler が追加された / 既に存在する
        False: 環境変数未設定 (台帳記録のみ有効)
    """
    to_addr = os.environ.get("BOATRACE_ERROR_NOTIFY_TO", "").strip()
    # 重複チェック
    for h in logger_obj.handlers:
        if isinstance(h, EmailErrorHandler):
            return bool(h.to_addr)
    handler = EmailErrorHandler(to_addr=to_addr)
    handler.setLevel(logging.ERROR)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    ))
    logger_obj.addHandler(handler)
    return bool(to_addr)
