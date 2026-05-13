"""購読者の管理用スクリプト

メアド or email_hash を指定して購読を解除 / 削除 / 状態確認できる。
通常はメール内の解除リンクを使うが、トークンを失くした場合や
管理目的での操作に使う。

使い方:
  # 一覧表示 (メアドは復号して表示)
  python scripts/manage_subscribers.py list

  # 特定メアドの解除 (is_active = 0、行は残す)
  python scripts/manage_subscribers.py unsubscribe your@email.com

  # 特定メアドの完全削除 (行ごと消す、送信履歴も消す)
  python scripts/manage_subscribers.py delete your@email.com

  # 統計
  python scripts/manage_subscribers.py stats
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
except ImportError:
    pass

from src.db.connection import connect
from src.notifications.crypto import (
    EncryptionError,
    decrypt_email,
    hash_email,
    normalize_email,
)


def cmd_list():
    conn = connect()
    cur = conn.execute(
        "SELECT email_hash, email_encrypted, is_active, is_verified, "
        "       created_at, last_notified_at, notify_count "
        "FROM alert_subscribers ORDER BY created_at DESC"
    )
    rows = cur.fetchall()
    print(f"=== 全購読者 ({len(rows)} 件) ===\n")
    for eh, enc, act, ver, cre, last, cnt in rows:
        try:
            email = decrypt_email(enc)
        except EncryptionError as e:
            email = f"(decrypt error: {e})"
        status = []
        status.append("active" if act else "inactive")
        status.append("verified" if ver else "pending")
        print(f"  {email}")
        print(f"    hash:     {eh[:16]}...")
        print(f"    status:   {' / '.join(status)}")
        print(f"    created:  {cre}")
        if last:
            print(f"    notified: {last} ({cnt} 回)")
        print()
    conn.close()


def cmd_unsubscribe(email: str):
    email = normalize_email(email)
    eh = hash_email(email)
    conn = connect()
    row = conn.execute(
        "SELECT is_active, is_verified FROM alert_subscribers WHERE email_hash = ?",
        (eh,),
    ).fetchone()
    if not row:
        print(f"❌ {email} は登録されていません (hash={eh[:8]}...)")
        conn.close()
        sys.exit(1)
    act, ver = row
    if not act:
        print(f"⚠️ {email} は既に解除済み (再度 unsubscribe しても no-op)")
    conn.execute(
        "UPDATE alert_subscribers SET is_active = 0 WHERE email_hash = ?",
        (eh,),
    )
    conn.commit()
    print(f"✅ {email} を解除しました (is_active = 0)")
    print(f"   再登録するには /alerts/subscribe から同じメアドで登録してください")
    conn.close()


def cmd_delete(email: str):
    email = normalize_email(email)
    eh = hash_email(email)
    conn = connect()
    row = conn.execute(
        "SELECT email_hash FROM alert_subscribers WHERE email_hash = ?",
        (eh,),
    ).fetchone()
    if not row:
        print(f"❌ {email} は登録されていません")
        conn.close()
        sys.exit(1)

    # 送信履歴も削除
    n_sent = conn.execute(
        "SELECT COUNT(*) FROM alert_sent WHERE email_hash = ?",
        (eh,),
    ).fetchone()[0]
    conn.execute("DELETE FROM alert_sent WHERE email_hash = ?", (eh,))
    conn.execute("DELETE FROM alert_subscribers WHERE email_hash = ?", (eh,))
    conn.commit()
    print(f"✅ {email} を完全削除しました")
    print(f"   購読者行: 1 件削除")
    print(f"   送信履歴: {n_sent} 件削除")
    conn.close()


def cmd_stats():
    conn = connect()
    n_total = conn.execute("SELECT COUNT(*) FROM alert_subscribers").fetchone()[0]
    n_active = conn.execute(
        "SELECT COUNT(*) FROM alert_subscribers WHERE is_active=1 AND is_verified=1"
    ).fetchone()[0]
    n_pending = conn.execute(
        "SELECT COUNT(*) FROM alert_subscribers WHERE is_active=1 AND is_verified=0"
    ).fetchone()[0]
    n_inactive = conn.execute(
        "SELECT COUNT(*) FROM alert_subscribers WHERE is_active=0"
    ).fetchone()[0]
    n_sent_total = conn.execute("SELECT COUNT(*) FROM alert_sent").fetchone()[0]
    print("=== 購読統計 ===")
    print(f"  総登録数:         {n_total}")
    print(f"  認証済み・有効:   {n_active}")
    print(f"  認証待ち:         {n_pending}")
    print(f"  解除済み:         {n_inactive}")
    print(f"  累計送信件数:     {n_sent_total}")
    conn.close()


USAGE = """使い方:
  python scripts/manage_subscribers.py list
  python scripts/manage_subscribers.py unsubscribe メアド
  python scripts/manage_subscribers.py delete       メアド
  python scripts/manage_subscribers.py stats
"""


def main():
    if len(sys.argv) < 2:
        print(USAGE)
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "list":
        cmd_list()
    elif cmd == "stats":
        cmd_stats()
    elif cmd == "unsubscribe":
        if len(sys.argv) < 3:
            print("❌ メアドを指定してください"); sys.exit(1)
        cmd_unsubscribe(sys.argv[2])
    elif cmd == "delete":
        if len(sys.argv) < 3:
            print("❌ メアドを指定してください"); sys.exit(1)
        cmd_delete(sys.argv[2])
    else:
        print(f"❌ 不明なコマンド: {cmd}")
        print(USAGE)
        sys.exit(1)


if __name__ == "__main__":
    main()
