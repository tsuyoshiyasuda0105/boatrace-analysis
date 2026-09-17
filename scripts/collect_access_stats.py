# -*- coding: utf-8 -*-
"""Cloudflare Web Analytics の日別アクセス数を DB 台帳に貯める。

Cloudflare 側の保持は 30 日だけ。毎日これを走らせておけば、それより前の推移も残る。

使い方:
    python scripts/collect_access_stats.py              # 直近30日を取り込む
    python scripts/collect_access_stats.py --days 7     # 直近7日だけ
    python scripts/collect_access_stats.py --dry-run    # 書き込まずに内容だけ見る

トークンは .env の CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID から読む（値は表示しない）。
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import config  # noqa: E402,F401  (.env を読み込む)
from src.access_stats import access_coverage, record_daily_access  # noqa: E402
from src.db.connection import connect  # noqa: E402

GQL = "https://api.cloudflare.com/client/v4/graphql"
QUERY = (
    "query($acc:String!,$site:String!,$since:Date!,$until:Date!){"
    "viewer{accounts(filter:{accountTag:$acc}){"
    "rumPageloadEventsAdaptiveGroups(limit:100,"
    "filter:{date_geq:$since,date_leq:$until,siteTag:$site},orderBy:[date_ASC])"
    "{count sum{visits} dimensions{date}}}}}"
)
# Cloudflare Web Analytics の保持期間。これより前は取りに行っても返らない。
RETENTION_DAYS = 30


def fetch_cloudflare(days: int) -> tuple[list[dict], str, str]:
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    beacon = os.environ.get("BOATRACE_CF_BEACON", "").strip()
    missing = [
        name
        for name, value in (
            ("CLOUDFLARE_API_TOKEN", token),
            ("CLOUDFLARE_ACCOUNT_ID", account),
            ("BOATRACE_CF_BEACON", beacon),
        )
        if not value
    ]
    if missing:
        raise SystemExit("NG: .env に " + " / ".join(missing) + " がありません")

    until = datetime.date.today()
    since = until - datetime.timedelta(days=days - 1)
    body = json.dumps(
        {
            "query": QUERY,
            "variables": {"acc": account, "site": beacon, "since": str(since), "until": str(until)},
        }
    ).encode()
    req = urllib.request.Request(
        GQL,
        data=body,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
    )
    try:
        payload = json.loads(urllib.request.urlopen(req, timeout=40).read())
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"HTTPエラー {exc.code}: {exc.read().decode('utf-8', 'replace')[:200]}")
    if payload.get("errors"):
        raise SystemExit("APIエラー: " + json.dumps(payload["errors"], ensure_ascii=False)[:300])

    accounts = payload["data"]["viewer"]["accounts"]
    if not accounts:
        raise SystemExit("アカウントが見つかりません（CLOUDFLARE_ACCOUNT_ID を確認）")

    rows = [
        {
            "date": g["dimensions"]["date"],
            "page_views": int(g["count"] or 0),
            "visits": int((g.get("sum") or {}).get("visits") or 0),
        }
        for g in accounts[0]["rumPageloadEventsAdaptiveGroups"]
    ]
    return rows, str(since), str(until)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=RETENTION_DAYS,
                    help=f"取り込む日数（既定 {RETENTION_DAYS}＝Cloudflareの保持期間いっぱい）")
    ap.add_argument("--dry-run", action="store_true", help="DBに書かず内容だけ表示")
    args = ap.parse_args()

    days = max(1, min(args.days, RETENTION_DAYS))
    if args.days > RETENTION_DAYS:
        print(f"※ Cloudflareの保持は{RETENTION_DAYS}日までなので {days} 日に丸めました")

    rows, since, until = fetch_cloudflare(days)
    total = sum(r["page_views"] for r in rows)
    print(f"Cloudflare {since}〜{until}: {len(rows)}日分 / 合計 {total} ページ表示")
    for r in rows:
        print(f"  {r['date']}: {r['page_views']:>5} ページ表示 / {r['visits']} 訪問")

    if args.dry_run:
        print("(--dry-run のため書き込みませんでした)")
        return 0

    with connect() as conn:
        result = record_daily_access(conn, rows, window_start=since, window_end=until)
        coverage = access_coverage(conn)

    print(
        f"DB反映: {result['written']}日分"
        f"（計測あり {result['from_source']}日 / 表示ゼロ {result['zero_filled']}日）"
    )
    print(
        f"台帳の蓄積: {coverage['first_date']} 〜 {coverage['last_date']} "
        f"= {coverage['days']}日分 / 累計 {coverage['total_views']} ページ表示"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
