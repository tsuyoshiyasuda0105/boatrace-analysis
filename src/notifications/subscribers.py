"""
アラート購読者の DB 操作

セキュリティ:
  - メアドは AES-GCM 暗号化して保存
  - email_hash でルックアップ (鍵なしで重複検出可能)
  - verification_token / unsubscribe_token は secrets で生成
  - IP もハッシュ化保存 (アクセス元の追跡防止)
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional

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

DEFAULT_ALERT_TYPES = ["L4_SG", "L4_G1", "L4_G2"]
ALL_ALERT_TYPES = {
    "L4_SG": "L4 SG×A1 (検証回収率 258.2%)",
    "L4_G1": "L4 G1×A1 (検証回収率 242.8%)",
    "L4_G2": "L4 G2×A1 (検証回収率 242.7%)",
    "L4_G3": "L4 G3×A1 (検証回収率 149.2%)",
    "L4_general": "L4 一般戦×A1 (検証回収率 147.7%)",
    "L4_default": "L4 通算 A1 (検証回収率 160.8%)",
}


def _hash_ip(ip: str) -> str:
    """IP もハッシュ化 (追跡防止)"""
    if not ip:
        return ""
    return hashlib.sha256(ip.encode("utf-8")).hexdigest()[:16]


def subscribe(
    email: str,
    alert_types: list[str],
    min_recovery_rate: float = 150.0,
    ip: str = "",
) -> tuple[str, str]:
    """新規購読登録。確認メール送信用のトークンを返す。
    Returns: (email_hash, verification_token)
    Raises:
      ValueError - 無効なメアドや alert_types
      EncryptionError - 鍵未設定など
    """
    email = normalize_email(email)
    if not is_valid_email(email):
        raise ValueError("無効なメールアドレス形式です")

    if not alert_types:
        alert_types = DEFAULT_ALERT_TYPES
    for at in alert_types:
        if at not in ALL_ALERT_TYPES:
            raise ValueError(f"不明なアラート種別: {at}")

    if not (0 <= min_recovery_rate <= 500):
        raise ValueError("min_recovery_rate は 0-500 の範囲")

    eh = hash_email(email)
    enc = encrypt_email(email)
    verify_token = secrets.token_urlsafe(32)
    unsub_token = secrets.token_urlsafe(32)
    now = datetime.utcnow().isoformat()
    expires = (datetime.utcnow() + timedelta(days=2)).isoformat()
    ip_hashed = _hash_ip(ip)

    with db_connect() as conn:
        # 既存登録があれば「再認証」扱いで token を上書き
        existing = conn.execute(
            "SELECT email_hash, is_verified FROM alert_subscribers WHERE email_hash = ?",
            (eh,),
        ).fetchone()

        if existing:
            # 認証済みなら何もせず終了 (既登録)
            if existing[1]:
                logger.info("subscribe: already verified, sending re-verification")
            # 認証 token と有効期限を更新
            conn.execute(
                """UPDATE alert_subscribers
                   SET verification_token = ?,
                       verification_expires_at = ?,
                       alert_types = ?,
                       min_recovery_rate = ?,
                       is_active = 1
                   WHERE email_hash = ?""",
                (verify_token, expires, json.dumps(alert_types),
                 min_recovery_rate, eh),
            )
        else:
            conn.execute(
                """INSERT INTO alert_subscribers
                   (email_hash, email_encrypted, alert_types,
                    min_recovery_rate, is_active, is_verified,
                    verification_token, verification_expires_at,
                    unsubscribe_token, created_at, ip_at_signup)
                   VALUES (?, ?, ?, ?, 1, 0, ?, ?, ?, ?, ?)""",
                (eh, enc, json.dumps(alert_types), min_recovery_rate,
                 verify_token, expires, unsub_token, now, ip_hashed),
            )
        conn.commit()

    logger.info("subscriber registered (hash=%s, verified=%s)",
                eh[:8], existing is not None and existing[1])
    return eh, verify_token


def verify(token: str) -> Optional[str]:
    """確認メールリンクのトークンを検証。
    成功時は email_hash を返し is_verified=1 にする。"""
    if not token:
        return None
    with db_connect() as conn:
        row = conn.execute(
            """SELECT email_hash, verification_expires_at FROM alert_subscribers
               WHERE verification_token = ? AND is_active = 1""",
            (token,),
        ).fetchone()
        if not row:
            return None
        eh, expires = row
        if expires and expires < datetime.utcnow().isoformat():
            logger.warning("verification token expired for %s", eh[:8])
            return None
        conn.execute(
            """UPDATE alert_subscribers
               SET is_verified = 1, verification_token = NULL
               WHERE email_hash = ?""",
            (eh,),
        )
        conn.commit()
    logger.info("subscriber verified: %s", eh[:8])
    return eh


def unsubscribe(token: str) -> bool:
    """ワンクリック解除"""
    if not token:
        return False
    with db_connect() as conn:
        row = conn.execute(
            "SELECT email_hash FROM alert_subscribers WHERE unsubscribe_token = ?",
            (token,),
        ).fetchone()
        if not row:
            return False
        eh = row[0]
        conn.execute(
            "UPDATE alert_subscribers SET is_active = 0 WHERE email_hash = ?",
            (eh,),
        )
        conn.commit()
    logger.info("subscriber unsubscribed: %s", eh[:8])
    return True


def list_active_subscribers() -> list[dict]:
    """全アクティブ・認証済み購読者を返す。
    Returns: [{email, alert_types(list), min_recovery_rate, unsubscribe_token, email_hash}]
    複号はここで実施 (送信処理から呼ばれる)。"""
    out = []
    with db_connect() as conn:
        cur = conn.execute(
            """SELECT email_hash, email_encrypted, alert_types,
                      min_recovery_rate, unsubscribe_token
               FROM alert_subscribers
               WHERE is_active = 1 AND is_verified = 1"""
        )
        for eh, enc, types, min_rate, unsub in cur.fetchall():
            try:
                email = decrypt_email(enc)
            except EncryptionError as e:
                logger.error("decrypt failed for %s: %s", eh[:8], e)
                continue
            try:
                types_list = json.loads(types) if types else DEFAULT_ALERT_TYPES
            except Exception:
                types_list = DEFAULT_ALERT_TYPES
            out.append({
                "email": email,
                "email_hash": eh,
                "alert_types": types_list,
                "min_recovery_rate": min_rate,
                "unsubscribe_token": unsub,
            })
    return out


def mark_sent(email_hash: str, race_id: str, alert_type: str):
    """送信履歴を記録 (重複送信防止)"""
    now = datetime.utcnow().isoformat()
    with db_connect() as conn:
        try:
            conn.execute(
                """INSERT OR IGNORE INTO alert_sent (email_hash, race_id, alert_type, sent_at)
                   VALUES (?, ?, ?, ?)""",
                (email_hash, race_id, alert_type, now),
            )
            conn.execute(
                """UPDATE alert_subscribers
                   SET last_notified_at = ?, notify_count = notify_count + 1
                   WHERE email_hash = ?""",
                (now, email_hash),
            )
            conn.commit()
        except Exception as e:
            logger.error("mark_sent failed: %s", e)


def already_sent(email_hash: str, race_id: str, alert_type: str) -> bool:
    """既送信か確認"""
    with db_connect() as conn:
        row = conn.execute(
            """SELECT 1 FROM alert_sent
               WHERE email_hash = ? AND race_id = ? AND alert_type = ?""",
            (email_hash, race_id, alert_type),
        ).fetchone()
    return row is not None


def stats() -> dict:
    """購読者統計"""
    with db_connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM alert_subscribers").fetchone()[0]
        active = conn.execute(
            "SELECT COUNT(*) FROM alert_subscribers WHERE is_active=1 AND is_verified=1"
        ).fetchone()[0]
        pending = conn.execute(
            "SELECT COUNT(*) FROM alert_subscribers WHERE is_active=1 AND is_verified=0"
        ).fetchone()[0]
        sent_30d = conn.execute(
            """SELECT COUNT(*) FROM alert_sent
               WHERE sent_at >= datetime('now', '-30 days')"""
        ).fetchone()[0]
    return {
        "total": total,
        "active_verified": active,
        "pending_verification": pending,
        "sent_last_30d": sent_30d,
    }
