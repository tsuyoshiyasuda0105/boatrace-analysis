"""SMTP 送信テスト

ローカル .env の BOATRACE_SMTP_* 設定で 1 通テスト送信する。
Gmail アプリパスワード / Brevo / Resend など、SMTP プロバイダの動作確認用。

実行:
  .venv\\Scripts\\python.exe scripts\\test_smtp.py your@email.com
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

import os
import socket

from src.notifications.mailer import _get_config, _send


def show_config():
    cfg = _get_config()
    print("=== SMTP 設定 ===")
    print(f"  HOST: {cfg['host']!r}")
    print(f"  PORT: {cfg['port']}")
    print(f"  USER: {cfg['user']!r}")
    pw = cfg["password"]
    masked = f"{'*' * (len(pw) - 4)}{pw[-4:]}" if len(pw) > 4 else "(空)"
    print(f"  PASS: {masked}  (length={len(pw)})")
    print(f"  FROM: {cfg['from_addr']!r}")
    print(f"  SITE: {cfg['site_url']!r}")
    print()

    # 隠れた文字チェック
    for key in ("host", "user", "from_addr"):
        v = cfg[key]
        if not v:
            continue
        chars = [(i, c, ord(c)) for i, c in enumerate(v) if ord(c) > 127 or ord(c) < 32]
        if chars:
            print(f"  ⚠️ {key} に非ASCII/制御文字あり:")
            for i, c, o in chars:
                print(f"      pos {i}: {c!r} (U+{o:04X})")
            print()


def check_dns(host: str) -> bool:
    try:
        socket.getaddrinfo(host, 587, 0, socket.SOCK_STREAM)
        return True
    except socket.gaierror as e:
        print(f"❌ DNS 解決失敗 ({host}): {e}")
        return False


def main():
    if len(sys.argv) < 2:
        print("使い方: python scripts/test_smtp.py 送信先アドレス")
        print()
        show_config()
        sys.exit(1)

    to = sys.argv[1]

    show_config()

    cfg = _get_config()
    if not cfg["host"]:
        print("❌ BOATRACE_SMTP_HOST が未設定です。.env を確認してください。")
        sys.exit(2)

    print(f"→ DNS 解決テスト: {cfg['host']}")
    if not check_dns(cfg["host"]):
        print()
        print("対処: ホスト名が正しいか、隠れた文字 (NBSP/改行) が無いか確認")
        sys.exit(3)
    print("✅ DNS OK")
    print()

    print(f"→ {to} 宛にテスト送信 ...")
    subject = "[BOATRACE] SMTP テスト送信"
    text = (
        "これは SMTP 設定確認用のテストメールです。\n\n"
        "このメールが届けば、SMTP 設定は正しく動作しています。\n\n"
        "-- BOATRACE 予測 v0.8\n"
    )
    html = """<!DOCTYPE html>
<html><body style="font-family:sans-serif;max-width:600px;margin:auto">
<h2>✅ SMTP テスト送信</h2>
<p>このメールが届けば、SMTP 設定は正しく動作しています。</p>
<hr>
<p style="font-size:12px;color:#999">BOATRACE 予測 v0.8</p>
</body></html>"""

    ok = _send(to, subject, text, html)
    print()
    if ok:
        print(f"✅ 送信成功 → {to} の受信箱を確認してください")
    else:
        print("❌ 送信失敗 (上のエラーログを確認)")
        sys.exit(4)


if __name__ == "__main__":
    main()
