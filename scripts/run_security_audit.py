"""
ワンコマンド セキュリティ監査

実行:
    python scripts/run_security_audit.py

含まれるチェック:
1. bandit  - コード静的解析
2. pip-audit - 依存パッケージ CVE 検出
3. detect-secrets - シークレット漏洩検出
4. semgrep - OWASP ルール検査
5. ruff (S) - 軽量セキュリティパターン
6. HTTP セキュリティヘッダ (デプロイ済 Web)
"""
import json
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "Scripts" / "python.exe"


def run(label: str, cmd: list[str]) -> tuple[int, str]:
    """サブプロセスを実行して結果を返す"""
    print(f"\n{'=' * 80}")
    print(f"  {label}")
    print('=' * 80)
    try:
        res = subprocess.run(
            cmd, cwd=ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=600,
        )
        return res.returncode, res.stdout + res.stderr
    except Exception as e:
        return 1, f"ERROR: {e}"


def main():
    results: dict[str, str] = {}

    # 1. bandit
    rc, out = run("1. bandit (Python コード静的セキュリティ解析)",
                  [str(PY), "-m", "bandit", "-r", "src/", "scripts/", "-ll", "-q"])
    # 末尾のサマリだけ表示
    for line in out.splitlines()[-15:]:
        print(line)
    results["bandit"] = "OK (Medium+ 件数を確認)" if rc == 0 else f"検出あり (RC={rc})"

    # 2. pip-audit
    rc, out = run("2. pip-audit (依存パッケージ脆弱性検出)",
                  [str(PY), "-m", "pip_audit", "--format=columns"])
    print(out[-1500:])
    results["pip-audit"] = "OK" if "No known vulnerabilities" in out else "脆弱性あり (上記参照)"

    # 3. detect-secrets
    print(f"\n{'=' * 80}")
    print("  3. detect-secrets (シークレット漏洩検出)")
    print('=' * 80)
    try:
        res = subprocess.run(
            [str(PY), "-m", "detect_secrets", "scan"],
            cwd=ROOT, capture_output=True, text=True, timeout=120,
        )
        data = json.loads(res.stdout) if res.stdout else {}
        secs = data.get("results", {})
        total = sum(len(v) for v in secs.values())
        print(f"  検出: {total} 件")
        if total:
            for fn, sl in secs.items():
                print(f"    {fn}: {len(sl)} 件")
            results["detect-secrets"] = f"⚠️ {total} 件"
        else:
            print("  ✅ 漏洩なし")
            results["detect-secrets"] = "OK"
    except Exception as e:
        print(f"  ERROR: {e}")
        results["detect-secrets"] = "実行失敗"

    # 4. ruff S
    rc, out = run("4. ruff S (軽量セキュリティパターン)",
                  [str(PY), "-m", "ruff", "check", "--select=S", "--statistics",
                   "src/", "scripts/"])
    print(out[-1500:])
    results["ruff-S"] = "OK" if "All checks passed" in out else "検出あり (上記参照)"

    # 5. semgrep (重い)
    semgrep_path = ROOT / ".venv" / "Scripts" / "semgrep.exe"
    if semgrep_path.exists():
        rc, out = run("5. semgrep (OWASP Top10 / security-audit ルール)",
                      [str(semgrep_path), "scan",
                       "--config=p/security-audit",
                       "--config=p/owasp-top-ten",
                       "--severity=ERROR", "--metrics=off", "--quiet",
                       "src/"])
        # 末尾のサマリ抽出
        tail = "\n".join(out.splitlines()[-30:])
        print(tail)
        results["semgrep"] = "OK" if "0 findings" in out or "Ran" in tail else "検出あり"
    else:
        results["semgrep"] = "未インストール"

    # 6. Web セキュリティヘッダ (デプロイ済サイト)
    print(f"\n{'=' * 80}")
    print("  6. HTTP セキュリティヘッダ (https://boatrace-web.onrender.com)")
    print('=' * 80)
    import urllib.request
    try:
        with urllib.request.urlopen(
            "https://boatrace-web.onrender.com/healthz", timeout=15
        ) as r:
            h = dict(r.headers.items())
        for key in ["Strict-Transport-Security", "X-Content-Type-Options",
                    "X-Frame-Options", "Referrer-Policy",
                    "Content-Security-Policy"]:
            v = h.get(key, "")
            mark = "✅" if v else "❌"
            print(f"  {mark} {key}: {v[:80] if v else '(無し)'}")
        ok_count = sum(1 for k in h if k in [
            "Strict-Transport-Security", "X-Content-Type-Options",
            "X-Frame-Options", "Referrer-Policy", "Content-Security-Policy"
        ])
        results["web-headers"] = f"{ok_count}/5 実装済"
    except Exception as e:
        print(f"  ERROR: {e}")
        results["web-headers"] = "確認失敗"

    # サマリ
    print(f"\n{'=' * 80}")
    print("  📊 セキュリティ監査サマリ")
    print('=' * 80)
    for tool, status in results.items():
        print(f"  {tool:<20} {status}")
    print()
    print("外部の Web 採点サイト (ブラウザで開く):")
    print("  https://observatory.mozilla.org/?host=boatrace-web.onrender.com")
    print("  https://securityheaders.com/?q=boatrace-web.onrender.com")
    print("  https://www.ssllabs.com/ssltest/analyze.html?d=boatrace-web.onrender.com")


if __name__ == "__main__":
    main()
