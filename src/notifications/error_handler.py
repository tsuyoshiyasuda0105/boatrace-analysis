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
from zoneinfo import ZoneInfo


JST = ZoneInfo("Asia/Tokyo")


class EmailErrorHandler(logging.Handler):
    """ERROR/CRITICAL を検知してメール送信。同一エラーの連投は抑制。"""

    def __init__(self, to_addr: str, rate_limit_sec: int = 3600):
        super().__init__(level=logging.ERROR)
        self.to_addr = to_addr
        self.rate_limit_sec = rate_limit_sec
        # メッセージ key → 最終送信時刻
        self._last_sent: dict[str, float] = defaultdict(float)

    def _key(self, record: logging.LogRecord) -> str:
        # logger 名 + module:line + メッセージ先頭 80 文字でユニーク化
        msg_head = (record.getMessage() or "")[:80]
        return f"{record.name}|{record.module}:{record.lineno}|{msg_head}"

    def emit(self, record: logging.LogRecord) -> None:
        try:
            key = self._key(record)
            now = time.time()
            last = self._last_sent.get(key, 0)
            if now - last < self.rate_limit_sec:
                # レート制限中 → スキップ (本人ログには出る)
                return
            self._last_sent[key] = now

            # 送信内容組み立て
            subject = f"[BOATRACE ERROR] {record.levelname}: {record.name} - {(record.getMessage() or '')[:80]}"
            body_lines = [
                f"時刻 : {datetime.now(JST).isoformat(timespec='seconds')}",
                f"レベル: {record.levelname}",
                f"場所  : {record.name} | {record.module}:{record.lineno} | {record.funcName}()",
                f"パス  : {record.pathname}",
                "",
                "--- メッセージ ---",
                record.getMessage(),
            ]
            if record.exc_info:
                body_lines.append("")
                body_lines.append("--- トレースバック ---")
                body_lines.append("".join(traceback.format_exception(*record.exc_info)))
            elif record.stack_info:
                body_lines.append("")
                body_lines.append("--- スタック ---")
                body_lines.append(record.stack_info)
            body_text = "\n".join(body_lines)

            # 既存の mailer 経由で送信 (Brevo/Resend/SMTP 自動切替)
            # 循環 import 防止のためここで遅延 import
            from src.notifications.mailer import _send  # noqa: WPS437
            _send(self.to_addr, subject, body_text, body_html=None)
        except Exception:
            # 通知失敗が本処理を止めないように吸収。stderr にだけ落とす
            self.handleError(record)


def install_error_notifier(logger_obj: logging.Logger) -> bool:
    """指定ロガーに EmailErrorHandler を 1 個だけ仕込む。

    BOATRACE_ERROR_NOTIFY_TO が設定されていない場合は何もしない (no-op、True を返さない)。
    既に仕込まれていれば追加しない (重複防止)。

    Returns:
        True: handler が追加された / 既に存在する
        False: 環境変数未設定で skip
    """
    to_addr = os.environ.get("BOATRACE_ERROR_NOTIFY_TO", "").strip()
    if not to_addr:
        return False
    # 重複チェック
    for h in logger_obj.handlers:
        if isinstance(h, EmailErrorHandler):
            return True
    handler = EmailErrorHandler(to_addr=to_addr)
    handler.setLevel(logging.ERROR)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    ))
    logger_obj.addHandler(handler)
    return True
