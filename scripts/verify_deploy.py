"""Render デプロイ後の動作確認スクリプト
すべての新機能エンドポイントが正常動作するか検証
"""
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

BASE = "https://boatrace-web.onrender.com"

ENDPOINTS = [
    ("/healthz", "ヘルスチェック", 200),
    ("/", "トップ → リダイレクト", [302, 200]),
    ("/login", "会員ログイン画面", 200),
    ("/alerts/subscribe", "メール通知登録 (Phase 2)", 200),
    ("/member/strategy", "ROI ダッシュボード (要会員ログイン)", [200, 302]),
    ("/robots.txt", "robots.txt", 200),
    ("/api/market-signals?date=2026-05-13", "市場シグナル API", 200),
]

print(f"=== Render エンドポイント動作確認 ===")
print(f"対象: {BASE}")
print()
print(f"{'パス':<48} {'期待':>8} {'実際':>6} {'判定'}")
print("-" * 78)

all_ok = True
for path, label, expected in ENDPOINTS:
    if isinstance(expected, int):
        expected = [expected]
    try:
        req = urllib.request.Request(BASE + path, method="GET")
        with urllib.request.urlopen(req, timeout=30) as r:
            status = r.status
    except urllib.error.HTTPError as e:
        status = e.code
    except Exception as e:
        status = f"ERR:{str(e)[:20]}"

    ok = status in expected
    mark = "✅" if ok else "❌"
    if not ok:
        all_ok = False
    exp_str = "/".join(str(e) for e in expected)
    print(f"  {path:<46} {exp_str:>8} {str(status):>6} {mark}  {label}")

print()
if all_ok:
    print("✅ 全エンドポイント正常応答")
else:
    print("❌ 一部失敗 - Render Manual Deploy が必要かもしれません")
