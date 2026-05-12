"""
メールアドレス暗号化 (AES-GCM)

セキュリティモデル:
  - DB には平文メアドを保存しない
  - AES-256-GCM で暗号化、認証タグ込み
  - 鍵は環境変数 BOATRACE_EMAIL_KEY (32 byte / 64 hex chars)
  - DB レイヤだけ漏れても平文は読めない (鍵が必要)
  - 送信時のみ Python メモリ上で複号
  - email_hash (SHA-256) で重複登録検出 (鍵なくても可能)

鍵生成方法:
  python -c "import secrets; print(secrets.token_hex(32))"
"""
from __future__ import annotations

import hashlib
import logging
import os
from base64 import urlsafe_b64decode, urlsafe_b64encode

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)

_KEY_ENV = "BOATRACE_EMAIL_KEY"
_NONCE_SIZE = 12  # AES-GCM 推奨ノンス長


class EncryptionError(Exception):
    """暗号化/複号失敗時"""


def _get_key() -> bytes:
    """環境変数から AES 鍵をロード。本番では必ず設定必須。"""
    hex_key = os.environ.get(_KEY_ENV, "").strip()
    if not hex_key:
        # 開発用: 一時鍵 (再起動でリセットされる)
        if os.environ.get("RENDER"):
            raise EncryptionError(
                f"{_KEY_ENV} 環境変数が未設定です。本番では必ず設定してください。\n"
                "生成: python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        logger.warning(
            "%s not set, using EPHEMERAL dev key (not persistent!)", _KEY_ENV
        )
        # 開発用ダミー鍵 (本番では使わない)
        return b"\x00" * 32

    try:
        key = bytes.fromhex(hex_key)
    except ValueError as e:
        raise EncryptionError(f"{_KEY_ENV} が hex 形式ではありません: {e}")

    if len(key) != 32:
        raise EncryptionError(f"{_KEY_ENV} は 32 byte (64 hex chars) 必要、実際 {len(key)} byte")

    return key


def hash_email(email: str) -> str:
    """メアドの SHA-256 ハッシュ (DB の PK 用、検索可能)。
    通常レベルのハッシュ (レインボーテーブル対策なし) なので、
    機密性は暗号化に依存。これは DB 内の重複検出用のみ。"""
    return hashlib.sha256(email.lower().strip().encode("utf-8")).hexdigest()


def encrypt_email(email: str) -> str:
    """メアドを AES-GCM 暗号化して urlsafe-base64 で返す"""
    if not email:
        raise EncryptionError("空のメアドは暗号化不可")
    key = _get_key()
    nonce = os.urandom(_NONCE_SIZE)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, email.encode("utf-8"), associated_data=None)
    # nonce + ciphertext を結合して base64
    return urlsafe_b64encode(nonce + ciphertext).decode("ascii")


def decrypt_email(token: str) -> str:
    """暗号化済みメアドを複号"""
    if not token:
        raise EncryptionError("空のトークンは複号不可")
    key = _get_key()
    try:
        raw = urlsafe_b64decode(token.encode("ascii"))
    except Exception as e:
        raise EncryptionError(f"base64 デコード失敗: {e}")
    if len(raw) < _NONCE_SIZE + 16:  # ciphertext は最低 GCM タグ 16 byte
        raise EncryptionError("トークンが短すぎ")
    nonce, ciphertext = raw[:_NONCE_SIZE], raw[_NONCE_SIZE:]
    aesgcm = AESGCM(key)
    try:
        plaintext = aesgcm.decrypt(nonce, ciphertext, associated_data=None)
    except InvalidTag:
        raise EncryptionError("認証タグ不一致 (改ざん or 鍵違い)")
    return plaintext.decode("utf-8")


def normalize_email(email: str) -> str:
    """メアド正規化 (小文字化、前後の空白除去)"""
    return email.lower().strip()


def is_valid_email(email: str) -> bool:
    """簡易メアド形式チェック (RFC 完全準拠ではない)"""
    if not email or len(email) > 254:
        return False
    if email.count("@") != 1:
        return False
    local, domain = email.split("@")
    if not local or not domain:
        return False
    if "." not in domain:
        return False
    if any(ch in email for ch in " \t\r\n<>"):
        return False
    # local-part は印字可能 ASCII (RFC 5321 簡略)
    if not all(0x20 < ord(ch) < 0x7F for ch in local):
        return False
    return True
