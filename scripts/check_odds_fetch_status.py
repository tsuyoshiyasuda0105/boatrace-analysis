# -*- coding: utf-8 -*-
"""狙ったのに取れなかったオッズを見る。

  python scripts/check_odds_fetch_status.py              # 直近24時間
  python scripts/check_odds_fetch_status.py --hours 72
  python scripts/check_odds_fetch_status.py --list       # 1件ずつ並べる

件数の鮮度 (odds_trifecta が何行増えたか) だけを見ていると、「今日は対象が
少ない」と「静かに止まっている」が見分けられない。この表は狙った回数と
失敗の理由を残しているので、止まった瞬間に理由まで分かる。

終了コード: 失敗が1件でもあれば 1。朝の確認をそのまま自動化できる。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402,F401 - .env を読み込む副作用が目的
from src import odds_fetch_status as status  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--list", action="store_true", help="失敗を1件ずつ並べる")
    ap.add_argument("--limit", type=int, default=20)
    a = ap.parse_args()

    counts = status.summarize(hours=a.hours)
    total = sum(counts.values())
    print(f"直近 {a.hours} 時間に狙ったオッズ: {total:,} 件")
    if not total:
        print("  記録がありません。cron が動いていないか、対象レースが無い時間帯です。")
        return 0
    labels = {status.STATE_OK: "取れた", status.STATE_EMPTY: "空だった",
              status.STATE_ERROR: "失敗"}
    for state, count in sorted(counts.items()):
        print(f"  {labels.get(state, state):<8} {count:>6,}  ({count / total * 100:.1f}%)")

    failures = status.recent_failures(hours=a.hours)
    if not failures:
        print("\n  取れなかったものはありません。")
        return 0

    print(f"\n取れなかった {len(failures):,} 件の理由:")
    reasons: dict[str, int] = {}
    for item in failures:
        reasons[str(item["detail"])] = reasons.get(str(item["detail"]), 0) + 1
    for reason, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
        print(f"  {count:>6,}  {reason}")

    if a.list:
        print(f"\n新しい順に {min(a.limit, len(failures))} 件:")
        for item in failures[: a.limit]:
            print(f"  {item['checked_at']}  {item['race_id']} [{item['snapshot_label']}] "
                  f"{item['state']} 試行{item['attempts']}回  {item['detail']}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
