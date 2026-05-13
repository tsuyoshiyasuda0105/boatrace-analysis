"""未認証の購読者の verify_token を取り出して認証 URL を表示する。

SMTP がまだ動かない時の代替認証手段。
Supabase に直接接続して未認証行をリストアップする。

実行:
  .venv\\Scripts\\python.exe scripts\\get_pending_verify_url.py
"""
from __future__ import annotations

import os
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

site = os.environ.get("BOATRACE_SITE_URL", "https://boatrace-web.onrender.com").rstrip("/")

conn = connect()
cur = conn.execute(
    "SELECT email_hash, verification_token, verification_expires_at, "
    "       is_verified, created_at "
    "FROM alert_subscribers WHERE is_active = 1 "
    "ORDER BY created_at DESC"
)
rows = cur.fetchall()

print(f"=== Active subscribers ({len(rows)}) ===")
print(f"Site: {site}")
print()

if not rows:
    print("(購読者なし)")
    sys.exit(0)

for eh, tok, exp, ver, cre in rows:
    print(f"  hash:     {eh[:12]}...")
    print(f"  created:  {cre}")
    print(f"  verified: {'YES' if ver else 'NO (要認証)'}")
    if not ver and tok:
        print(f"  expires:  {exp}")
        print(f"  認証 URL:")
        print(f"    {site}/alerts/verify?token={tok}")
    print()

conn.close()
