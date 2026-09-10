# -*- coding: utf-8 -*-
"""本番の web に、未適用の kachisuji デルタを当てさせる。

  python scripts/trigger_kachisuji_apply.py            # 適用させる
  python scripts/trigger_kachisuji_apply.py --dry-run  # 何が未適用かだけ見る

slim DB は web サービスの /data にしか無いので、外から当てる方法はこの内部
エンドポイントを叩くことだけ。ふだんはメンテ用の定期実行 (JST 04:00-06:59)
が同じことをするので、急いで反映したいときだけ使う。

注意: レース終盤〜夜 (20-23時台) は cron が集中しており、この時間に叩くと
DB 接続を奪い合って失敗する。静かな時間帯に実行すること。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# トークンは DATABASE_URL から導き、os.environ を直接見る。PC から手で叩く
# ときは .env にしか無いので、config を読み込んで環境変数に載せておく。
import config  # noqa: E402,F401 - .env を os.environ へ読み込む副作用が目的

DEFAULT_BASE = "https://boatrace-web.onrender.com"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default=os.getenv("BOATRACE_WEB_BASE_URL", DEFAULT_BASE))
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="適用せず、輸送テーブルに何が残っているかだけを見る",
    )
    a = ap.parse_args()

    from src.kachisuji.delta_transport import (  # noqa: PLC0415 - 遅延読み込み
        TRANSPORT_TABLE,
        internal_token,
        _default_conn,
    )

    if a.dry_run:
        conn = _default_conn()
        try:
            rows = conn.execute(
                f"SELECT name, size_bytes, created_at FROM {TRANSPORT_TABLE} "
                "ORDER BY created_at"
            ).fetchall()
        finally:
            conn.close()
        if not rows:
            print("輸送テーブルは空です。")
            return 0
        print(f"輸送テーブルにあるファイル ({len(rows)} 件):")
        for name, size, created in rows:
            print(f"  {name:<40} {int(size) / 1e6:>7.1f} MB  {created}")
        print("\n  --dry-run のため適用しません。")
        return 0

    import requests  # noqa: PLC0415 - 遅延読み込み

    url = f"{a.base_url.rstrip('/')}/kachisuji/internal/apply-deltas"
    print(f"適用を依頼します: {url}", flush=True)
    response = requests.post(
        url, headers={"X-Internal-Token": internal_token()}, timeout=a.timeout
    )
    try:
        payload = response.json()
    except ValueError:
        payload = {"body": response.text[:500]}
    print(f"  HTTP {response.status_code}")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if response.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
