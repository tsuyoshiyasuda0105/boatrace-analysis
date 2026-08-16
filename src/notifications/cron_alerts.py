"""cron の「最終失敗」を管理者へメール通知するヘルパー (P0-2 タスク3)

src/notifications/error_handler.py と同じ思想:
  - 宛先は既存の管理者宛設定 (env BOATRACE_ERROR_NOTIFY_TO) に従う。直書き禁止。
  - 送信バックエンドは src/notifications/mailer.py の既存経路
    (Brevo HTTP API / Resend HTTP API / SMTP / コンソール stub の自動切替)。
  - スパム防止: 同一 job は 6 時間に 1 通まで。クールダウン状態は
    既存 system_status テーブルの行 (check_name='cron_alert_<job>') で管理し、
    新テーブルは作らない。

呼び出し側の想定 (最終失敗のみ):
  - scripts/render_maintenance_scheduler.py (04:00-07:00 窓終了時に degraded)
  - scripts/render_program_bootstrap_scheduler.py (07:30 時点でソース未解決)

この関数は決して例外を外へ出さない (通知失敗で cron 本体を壊さない)。
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from src.db.connection import connect as db_connect


logger = logging.getLogger(__name__)

JST = ZoneInfo("Asia/Tokyo")
DEFAULT_COOLDOWN_HOURS = 6.0
_CHECK_PREFIX = "cron_alert_"


def _now_jst() -> datetime:
    return datetime.now(JST)


def _cooldown_active(
    conn, check_name: str, now: datetime, cooldown_hours: float
) -> bool:
    """system_status の最新行の checked_at (naive JST) からクールダウン中か判定。"""
    row = conn.execute(
        """
        SELECT checked_at
          FROM system_status
         WHERE check_name = ?
         ORDER BY checked_at DESC
         LIMIT 1
        """,
        (check_name,),
    ).fetchone()
    if not row or not row[0]:
        return False
    try:
        last = datetime.fromisoformat(str(row[0]))
    except ValueError:
        return False
    if last.tzinfo is not None:
        last = last.astimezone(JST).replace(tzinfo=None)
    return (now.replace(tzinfo=None) - last) < timedelta(hours=cooldown_hours)


def _record_alert_state(
    conn,
    check_name: str,
    check_date: str,
    now: datetime,
    job: str,
    message: str,
    detail: dict | None,
    sent: bool,
) -> None:
    """クールダウン状態を system_status の既存行パターンで upsert する。"""
    now_iso = now.replace(tzinfo=None).isoformat(timespec="seconds")
    payload = json.dumps(
        {"job": job, "mail_sent": bool(sent), "detail": detail or {}},
        ensure_ascii=True,
        sort_keys=True,
        default=str,
    )
    exists = conn.execute(
        "SELECT 1 FROM system_status WHERE check_name=? AND check_date=?",
        (check_name, check_date),
    ).fetchone()
    if exists:
        conn.execute(
            """
            UPDATE system_status
               SET status=?, message=?, detail_json=?, checked_at=?
             WHERE check_name=? AND check_date=?
            """,
            ("error", message, payload, now_iso, check_name, check_date),
        )
    else:
        conn.execute(
            """
            INSERT INTO system_status
                (check_name, check_date, status, message, detail_json, checked_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (check_name, check_date, "error", message, payload, now_iso),
        )
    conn.commit()


def notify_cron_failure(
    job: str,
    message: str,
    *,
    detail: dict | None = None,
    cooldown_hours: float = DEFAULT_COOLDOWN_HOURS,
    incident_category: str = "cron_failure",
) -> bool:
    """cron の最終失敗を管理者へメール通知する (同一 job は cooldown_hours に 1 通)。

    Returns:
        True:  送信を試みた (クールダウン開始)
        False: 送信しなかった (宛先未設定 / クールダウン中 / 内部エラー)
    """
    ledger_recorded = False

    def finish(result: bool, *, notified: bool) -> bool:
        nonlocal ledger_recorded
        if not ledger_recorded:
            ledger_recorded = True
            try:
                from src.notifications.incident_ledger import record_incident

                record_incident(
                    category=incident_category,
                    source=job,
                    title=message[:160],
                    detail=detail,
                    severity="error",
                    dedup_key=f"{incident_category}|{job}",
                    notified=notified,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("cron incident ledger write failed (%s): %s", job, exc)
        return result

    try:
        to_addr = os.environ.get("BOATRACE_ERROR_NOTIFY_TO", "").strip()
        if not to_addr:
            logger.warning(
                "cron failure mail skipped (BOATRACE_ERROR_NOTIFY_TO unset): %s: %s",
                job,
                message,
            )
            return finish(False, notified=False)

        now = _now_jst()
        check_name = _CHECK_PREFIX + job
        check_date = now.date().isoformat()

        try:
            with db_connect() as conn:
                if _cooldown_active(conn, check_name, now, cooldown_hours):
                    logger.info("cron failure mail suppressed by cooldown: %s", job)
                    return finish(False, notified=False)
        except Exception as exc:  # noqa: BLE001
            # 可視性優先: クールダウン状態が読めなくても通知は出す
            logger.warning("cron alert cooldown read failed (%s): %s", job, exc)

        subject = f"[BOATRACE CRON FAILURE] {job}: {message[:80]}"
        body_lines = [
            f"時刻 : {now.isoformat(timespec='seconds')}",
            f"cron : {job}",
            "",
            "--- メッセージ ---",
            message,
        ]
        if detail:
            body_lines.append("")
            body_lines.append("--- 詳細 ---")
            body_lines.append(
                json.dumps(detail, ensure_ascii=False, indent=2, sort_keys=True, default=str)
            )
        body_lines.extend(
            [
                "",
                "---",
                "この通知は「リトライが残っていない最終失敗」のみ送信されます。",
                f"同一 cron のメールは {cooldown_hours:g} 時間に 1 通に制限されます。",
            ]
        )
        body_text = "\n".join(body_lines)

        # 既存 mailer 経由で送信 (循環 import 防止のため遅延 import)
        from src.notifications.mailer import _send  # noqa: WPS437

        sent = bool(_send(to_addr, subject, body_text, None))

        # 送信を試みた時点でクールダウン開始 (送信失敗の連投も抑制)
        try:
            with db_connect() as conn:
                _record_alert_state(
                    conn, check_name, check_date, now, job, message, detail, sent
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("cron alert state write failed (%s): %s", job, exc)
        return finish(True, notified=sent)
    except Exception:  # noqa: BLE001
        logger.exception("notify_cron_failure failed for %s", job)
        return finish(False, notified=False)
