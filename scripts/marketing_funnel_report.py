"""マーケのじょうご（見た→登録した）を数字で見る日報。読み取り専用。

出すもの（直近 N 日）:
  1. サイト全体のページ表示（site_access_daily・server + cloudflare 合算）
  2. LP(/start) の訪問を流入元(utm_source)別に（site_landing_daily）
  3. 新規登録数（profiles.created_at）

  python scripts/marketing_funnel_report.py --days 14

本番 Postgres に対して読むだけ。テスト時は DATABASE_URL 空で SQLite を見る。
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402,F401  (.env を読み込む)

from src.access_stats import load_daily_access, load_landing  # noqa: E402
from src.access_stats import SOURCE_SERVER, SOURCE_CLOUDFLARE
from src.db.connection import connect


def _signups_by_day(conn, start: str, end: str) -> dict[str, int]:
    """profiles.created_at から日別の新規登録数（無ければ空）。"""
    try:
        cur = conn.execute(
            # SQLite/Postgres 双方で日付部分だけ取り出す
            "SELECT substr(CAST(created_at AS TEXT),1,10) d, COUNT(*) "
            "FROM profiles WHERE substr(CAST(created_at AS TEXT),1,10) >= ? "
            "AND substr(CAST(created_at AS TEXT),1,10) <= ? GROUP BY d",
            (start, end),
        )
        return {str(r[0]): int(r[1]) for r in cur.fetchall()}
    except Exception:
        return {}


def build_report(conn, days: int) -> str:
    end = date.today()
    start = end - timedelta(days=days - 1)
    s, e = start.isoformat(), end.isoformat()

    server = {r["date"]: r["views"] for r in load_daily_access(conn, start=s, end=e, source=SOURCE_SERVER)}
    cloud = {r["date"]: r["views"] for r in load_daily_access(conn, start=s, end=e, source=SOURCE_CLOUDFLARE)}
    landing = load_landing(conn, start=s, end=e)
    signups = _signups_by_day(conn, s, e)

    lines = [f"=== マーケじょうご日報 {s}〜{e}（{days}日） ==="]
    lines.append("\n[1] サイト全体ページ表示（日別・アプリ計測）")
    total_pv = 0
    for d in (start + timedelta(n) for n in range(days)):
        k = d.isoformat()
        pv = server.get(k, 0) + cloud.get(k, 0)
        total_pv += pv
        su = signups.get(k, 0)
        bar = "#" * min(pv, 50)
        lines.append(f"  {k}  PV {pv:>4}  登録 {su:>2}  {bar}")
    lines.append(f"  合計PV {total_pv} / 期間の新規登録 {sum(signups.values())}")

    lines.append("\n[2] LP(/start) 訪問 流入元別")
    if landing:
        tot = sum(x["views"] for x in landing)
        for x in landing:
            lines.append(f"  {x['source']:<12} {x['views']:>4}")
        lines.append(f"  LP合計 {tot}")
    else:
        lines.append("  （まだ記録がありません。動画概要欄に /start?utm_source=… のリンクを入れると溜まります）")

    lines.append("\n[3] 新規登録（合計）")
    lines.append(f"  {sum(signups.values())} 人" + ("" if signups else "（この期間は0、または profiles を読めず）"))
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=14)
    a = ap.parse_args()
    with connect() as conn:
        print(build_report(conn, a.days))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
