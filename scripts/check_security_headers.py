"""デプロイ後のセキュリティヘッダ確認スクリプト"""
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

URL = "https://boatrace-web.onrender.com/healthz"

EXPECTED = {
    "Strict-Transport-Security": "max-age=31536000",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin",
    "Content-Security-Policy": "default-src 'self'",
}

req = urllib.request.Request(URL)
with urllib.request.urlopen(req, timeout=15) as r:
    headers = dict(r.headers.items())

print(f"対象: {URL}")
print(f"ステータス: {r.status}")
print()
print(f"{'ヘッダ':<32} {'状態':<8} 値")
print("-" * 90)
all_ok = True
for key, expected in EXPECTED.items():
    val = headers.get(key, "")
    if val and expected in val:
        mark = "✅ OK"
    elif val:
        mark = "⚠️ 部分"
    else:
        mark = "❌ 無し"
        all_ok = False
    short_val = (val[:60] + "...") if len(val) > 60 else val
    print(f"{key:<32} {mark:<8} {short_val}")

print()
print(f"{'CORS / その他':<32}")
for k in ["Server", "X-Render-Origin-Server"]:
    print(f"  {k}: {headers.get(k, '-')}")

print()
print("=" * 90)
print("✅ 全項目 OK!" if all_ok else "❌ デプロイ未完了 or 失敗")
