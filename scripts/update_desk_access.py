# -*- coding: utf-8 -*-
"""「リンのデスク」のアクセス数パネル（access.js）を DB 台帳から生成する。

data.js（リンが手で書く内容）には触らない。access.js だけを丸ごと作り直す。

台帳は scripts/collect_access_stats.py が貯めている。Cloudflare の保持は 30 日だが、
台帳には過去ぶんが残るので、ここでは 30 日より前の推移も出せる。

使い方:
    python scripts/update_desk_access.py                 # 直近14日
    python scripts/update_desk_access.py --days 60       # 直近60日
    python scripts/update_desk_access.py --collect       # 先に最新を取り込んでから生成
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import config  # noqa: E402,F401  (.env を読み込む)
from src.access_stats import access_coverage, load_daily_access  # noqa: E402
from src.db.connection import connect  # noqa: E402

DEFAULT_OUT = os.path.join(
    os.path.expanduser("~"), "OneDrive", "デスクトップ", "rin_desk", "access.js"
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14, help="グラフに出す日数（既定14）")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--collect", action="store_true",
                    help="生成の前に collect_access_stats.py で最新を取り込む")
    args = ap.parse_args()

    if args.collect:
        print("最新のアクセス数を取り込みます…")
        rc = subprocess.run(
            [sys.executable, str(Path(__file__).with_name("collect_access_stats.py"))],
            cwd=str(Path(__file__).resolve().parents[1]),
        ).returncode
        if rc != 0:
            print(f"※ 取り込みに失敗しました（終了コード {rc}）。台帳の現在値で生成します。")

    days = max(1, args.days)
    until = datetime.date.today()
    since = until - datetime.timedelta(days=days - 1)

    with connect() as conn:
        rows = load_daily_access(conn, start=str(since), end=str(until))
        coverage = access_coverage(conn)

    entries = [{"date": r["date"], "views": r["views"]} for r in rows]
    total = sum(r["views"] for r in entries)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    note = (
        f"{stamp} 更新（ページ表示数）。"
        f"台帳には {coverage['first_date']}〜{coverage['last_date']} の {coverage['days']}日分 "
        f"／累計 {coverage['total_views']} 表示を保存済み。"
        "Cloudflare本体の保持は30日ですが、台帳は消えません。"
    )
    payload = {"days": entries, "total": total, "updated": stamp, "note": note,
               "coverage": coverage}

    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("// このファイルは scripts/update_desk_access.py が自動生成します。手で書き換えないでください。\n")
        fh.write("// 手で書く内容は data.js のほうです。\n")
        fh.write("const DESK_ACCESS = " + json.dumps(payload, ensure_ascii=False, indent=2) + ";\n")

    print(f"OK: {out}")
    print(f"    直近{days}日で {total} ページ表示")
    print(f"    台帳: {coverage['first_date']}〜{coverage['last_date']} = {coverage['days']}日分 "
          f"/ 累計 {coverage['total_views']} 表示")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
