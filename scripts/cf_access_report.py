# -*- coding: utf-8 -*-
"""Cloudflare Web Analytics から日別アクセス数を取得して表示する。
トークンは .env の CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID から読む(値は表示しない)。
使い方: python scripts/cf_access_report.py [--days N] [--until YYYY-MM-DD]
"""
import os, sys, json, argparse, datetime, urllib.request, urllib.error
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
import config  # noqa: E402  (.env を読み込む)

BEACON = os.environ.get("BOATRACE_CF_BEACON", "2b6f7957877e46b8869ac37704072de2").strip()  # 本物のsite tag(2026-08-31確定)
GQL = "https://api.cloudflare.com/client/v4/graphql"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--until", default=None, help="YYYY-MM-DD (省略時は今日)")
    a = ap.parse_args()
    tok = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    acc = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    if not tok or not acc:
        print("NG: .env の CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID が空です")
        return 1
    until = datetime.date.fromisoformat(a.until) if a.until else datetime.date.today()
    since = until - datetime.timedelta(days=a.days - 1)
    q = ("query($acc:String!,$site:String!,$since:Date!,$until:Date!){viewer{accounts(filter:{accountTag:$acc})"
         "{rumPageloadEventsAdaptiveGroups(limit:100,filter:{date_geq:$since,date_leq:$until,siteTag:$site},"
         "orderBy:[date_ASC]){count sum{visits} dimensions{date}}}}}")
    body = json.dumps({"query": q, "variables": {"acc": acc, "site": BEACON,
                                                 "since": str(since), "until": str(until)}}).encode()
    req = urllib.request.Request(GQL, data=body,
                                 headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"})
    try:
        d = json.loads(urllib.request.urlopen(req, timeout=40).read())
    except urllib.error.HTTPError as e:
        print("HTTPエラー", e.code, e.read().decode("utf-8", "replace")[:300]); return 1
    if d.get("errors"):
        print("APIエラー:", json.dumps(d["errors"], ensure_ascii=False)[:400]); return 1
    accs = d["data"]["viewer"]["accounts"]
    if not accs:
        print("アカウントが見つかりません(Account IDを確認)"); return 1
    rows = accs[0]["rumPageloadEventsAdaptiveGroups"]
    print(f"=== アクセス数 {since}〜{until} ({a.days}日間) ===")
    tv = tp = 0
    for g in rows:
        pv = g["count"]; v = g["sum"]["visits"]; dt = g["dimensions"]["date"]
        tv += v; tp += pv
        print(f"  {dt}:  {v:>5} 訪問 / {pv:>5} ページ表示")
    if not rows:
        print("  (この期間の記録はまだありません)")
    print(f"  合計:  {tv} 訪問 / {tp} ページ表示")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
