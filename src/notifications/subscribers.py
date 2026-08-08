"""
Subscriber storage helpers for adopted strategy email alerts.

This module keeps backward compatibility with the existing alert tables while
switching the default alert flow from L4-specific notifications to
"adopted-confirmed" notifications.
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import datetime, timedelta
from typing import Any

from src.db.connection import connect as db_connect
from src.notifications.crypto import (
    EncryptionError,
    decrypt_email,
    encrypt_email,
    hash_email,
    is_valid_email,
    normalize_email,
)

logger = logging.getLogger(__name__)

DEFAULT_ALERT_TYPES = ["adopted_confirmed"]
ALL_ALERT_TYPES = {
    "adopted_confirmed": "採用確定レース通知",
}

DEFAULT_SUBJECT_TEMPLATE = "[BOATRACE] 採用確定 {date} {count}件"
DEFAULT_BODY_TEMPLATE = (
    "採用確定レースが {count} 件あります。\n\n"
    "{items_text}\n"
    "---\n"
    "配信停止: {unsubscribe_url}\n"
    "サイト: {site_url}\n"
)


def _hash_ip(ip: str) -> str:
    if not ip:
        return ""
    return hashlib.sha256(ip.encode("utf-8")).hexdigest()[:16]


def _table_columns(conn: Any, table_name: str) -> set[str]:
    try:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    except Exception:
        return set()
    cols = set()
    for row in rows:
        try:
            cols.add(str(row[1]))
        except Exception:
            continue
    return cols


def _ensure_schema(conn: Any) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS alert_subscribers (
          email_hash         TEXT PRIMARY KEY,
          email_encrypted    TEXT NOT NULL,
          alert_types        TEXT NOT NULL DEFAULT '["adopted_confirmed"]',
          min_recovery_rate  REAL NOT NULL DEFAULT 150.0,
          is_active          INTEGER NOT NULL DEFAULT 1,
          is_verified        INTEGER NOT NULL DEFAULT 0,
          verification_token TEXT,
          verification_expires_at TEXT,
          unsubscribe_token  TEXT,
          created_at         TEXT NOT NULL,
          last_notified_at   TEXT,
          notify_count       INTEGER NOT NULL DEFAULT 0,
          ip_at_signup       TEXT,
          subject_template   TEXT,
          body_template      TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_alert_sub_active
        ON alert_subscribers(is_active, is_verified)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS alert_sent (
          email_hash  TEXT NOT NULL,
          race_id     TEXT NOT NULL,
          alert_type  TEXT NOT NULL,
          sent_at     TEXT NOT NULL,
          PRIMARY KEY (email_hash, race_id, alert_type)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_alert_sent_at
        ON alert_sent(sent_at)
        """
    )

    cols = _table_columns(conn, "alert_subscribers")
    if "subject_template" not in cols:
        conn.execute("ALTER TABLE alert_subscribers ADD COLUMN subject_template TEXT")
    if "body_template" not in cols:
        conn.execute("ALTER TABLE alert_subscribers ADD COLUMN body_template TEXT")

    conn.execute(
        """
        UPDATE alert_subscribers
           SET subject_template = COALESCE(NULLIF(subject_template, ''), ?),
               body_template = COALESCE(NULLIF(body_template, ''), ?)
        """,
        (DEFAULT_SUBJECT_TEMPLATE, DEFAULT_BODY_TEMPLATE),
    )


def subscribe(
    email: str,
    alert_types: list[str],
    min_recovery_rate: float = 150.0,
    ip: str = "",
    subject_template: str = "",
    body_template: str = "",
) -> tuple[str, str]:
    email = normalize_email(email)
    if not is_valid_email(email):
        raise ValueError("有効なメールアドレスを入力してください。")

    if not alert_types:
        alert_types = list(DEFAULT_ALERT_TYPES)
    for alert_type in alert_types:
        if alert_type not in ALL_ALERT_TYPES:
            raise ValueError(f"未対応の通知種別です: {alert_type}")

    if not (0 <= min_recovery_rate <= 500):
        raise ValueError("min_recovery_rate は 0-500 の範囲で指定してください。")

    subject_template = (subject_template or DEFAULT_SUBJECT_TEMPLATE).strip()
    body_template = (body_template or DEFAULT_BODY_TEMPLATE).strip()

    email_hash_value = hash_email(email)
    encrypted_email = encrypt_email(email)
    verification_token = secrets.token_urlsafe(32)
    unsubscribe_token = secrets.token_urlsafe(32)
    now = datetime.utcnow().isoformat()
    expires = (datetime.utcnow() + timedelta(days=2)).isoformat()
    ip_hashed = _hash_ip(ip)

    with db_connect() as conn:
        _ensure_schema(conn)
        existing = conn.execute(
            "SELECT email_hash, is_verified FROM alert_subscribers WHERE email_hash = ?",
            (email_hash_value,),
        ).fetchone()

        payload = (
            verification_token,
            expires,
            json.dumps(alert_types, ensure_ascii=False),
            min_recovery_rate,
            subject_template,
            body_template,
            email_hash_value,
        )
        if existing:
            conn.execute(
                """
                UPDATE alert_subscribers
                   SET verification_token = ?,
                       verification_expires_at = ?,
                       alert_types = ?,
                       min_recovery_rate = ?,
                       subject_template = ?,
                       body_template = ?,
                       is_active = 1
                 WHERE email_hash = ?
                """,
                payload,
            )
        else:
            conn.execute(
                """
                INSERT INTO alert_subscribers (
                  email_hash,
                  email_encrypted,
                  alert_types,
                  min_recovery_rate,
                  is_active,
                  is_verified,
                  verification_token,
                  verification_expires_at,
                  unsubscribe_token,
                  created_at,
                  ip_at_signup,
                  subject_template,
                  body_template
                ) VALUES (?, ?, ?, ?, 1, 0, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    email_hash_value,
                    encrypted_email,
                    json.dumps(alert_types, ensure_ascii=False),
                    min_recovery_rate,
                    verification_token,
                    expires,
                    unsubscribe_token,
                    now,
                    ip_hashed,
                    subject_template,
                    body_template,
                ),
            )
        conn.commit()

    logger.info(
        "subscriber registered hash=%s verified=%s",
        email_hash_value[:8],
        bool(existing and existing[1]),
    )
    return email_hash_value, verification_token


def verify(token: str) -> str | None:
    if not token:
        return None
    with db_connect() as conn:
        _ensure_schema(conn)
        row = conn.execute(
            """
            SELECT email_hash, verification_expires_at
              FROM alert_subscribers
             WHERE verification_token = ?
               AND is_active = 1
            """,
            (token,),
        ).fetchone()
        if not row:
            return None
        email_hash_value, expires = row
        if expires and expires < datetime.utcnow().isoformat():
            logger.warning("verification token expired for %s", str(email_hash_value)[:8])
            return None
        conn.execute(
            """
            UPDATE alert_subscribers
               SET is_verified = 1,
                   verification_token = NULL
             WHERE email_hash = ?
            """,
            (email_hash_value,),
        )
        conn.commit()
    return str(email_hash_value)


def unsubscribe(token: str) -> bool:
    if not token:
        return False
    with db_connect() as conn:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT email_hash FROM alert_subscribers WHERE unsubscribe_token = ?",
            (token,),
        ).fetchone()
        if not row:
            return False
        conn.execute(
            "UPDATE alert_subscribers SET is_active = 0 WHERE email_hash = ?",
            (row[0],),
        )
        conn.commit()
    return True


def list_active_subscribers() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with db_connect() as conn:
        _ensure_schema(conn)
        cur = conn.execute(
            """
            SELECT email_hash,
                   email_encrypted,
                   alert_types,
                   min_recovery_rate,
                   unsubscribe_token,
                   subject_template,
                   body_template
              FROM alert_subscribers
             WHERE is_active = 1
               AND is_verified = 1
            """
        )
        for row in cur.fetchall():
            (
                email_hash_value,
                encrypted_email,
                alert_types_raw,
                min_recovery_rate,
                unsubscribe_token,
                subject_template,
                body_template,
            ) = row
            try:
                email = decrypt_email(encrypted_email)
            except EncryptionError as exc:
                logger.error("decrypt failed for %s: %s", str(email_hash_value)[:8], exc)
                continue
            try:
                alert_types = json.loads(alert_types_raw) if alert_types_raw else list(DEFAULT_ALERT_TYPES)
            except Exception:
                alert_types = list(DEFAULT_ALERT_TYPES)
            out.append(
                {
                    "email": email,
                    "email_hash": email_hash_value,
                    "alert_types": alert_types,
                    "min_recovery_rate": min_recovery_rate,
                    "unsubscribe_token": unsubscribe_token,
                    "subject_template": subject_template or DEFAULT_SUBJECT_TEMPLATE,
                    "body_template": body_template or DEFAULT_BODY_TEMPLATE,
                }
            )
    return out


def mark_sent(email_hash: str, race_id: str, alert_type: str) -> None:
    now = datetime.utcnow().isoformat()
    with db_connect() as conn:
        _ensure_schema(conn)
        conn.execute(
            """
            INSERT OR IGNORE INTO alert_sent (email_hash, race_id, alert_type, sent_at)
            VALUES (?, ?, ?, ?)
            """,
            (email_hash, race_id, alert_type, now),
        )
        conn.execute(
            """
            UPDATE alert_subscribers
               SET last_notified_at = ?,
                   notify_count = notify_count + 1
             WHERE email_hash = ?
            """,
            (now, email_hash),
        )
        conn.commit()


def already_sent(email_hash: str, race_id: str, alert_type: str) -> bool:
    with db_connect() as conn:
        _ensure_schema(conn)
        row = conn.execute(
            """
            SELECT 1
              FROM alert_sent
             WHERE email_hash = ?
               AND race_id = ?
               AND alert_type = ?
            """,
            (email_hash, race_id, alert_type),
        ).fetchone()
    return row is not None


def stats() -> dict[str, int]:
    with db_connect() as conn:
        _ensure_schema(conn)
        total = conn.execute("SELECT COUNT(*) FROM alert_subscribers").fetchone()[0]
        active = conn.execute(
            "SELECT COUNT(*) FROM alert_subscribers WHERE is_active = 1 AND is_verified = 1"
        ).fetchone()[0]
        pending = conn.execute(
            "SELECT COUNT(*) FROM alert_subscribers WHERE is_active = 1 AND is_verified = 0"
        ).fetchone()[0]
        sent_30d = conn.execute(
            """
            SELECT COUNT(*)
              FROM alert_sent
             WHERE sent_at >= datetime('now', '-30 days')
            """
        ).fetchone()[0]
    return {
        "total": int(total or 0),
        "active_verified": int(active or 0),
        "pending_verification": int(pending or 0),
        "sent_last_30d": int(sent_30d or 0),
    }
