from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.web import app as web_app


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the cached TOP page payload used by /races."
    )
    parser.add_argument("--date", required=True)
    parser.add_argument(
        "--lightweight",
        action="store_true",
        help="Skip expensive badge hydration; use already materialized badge caches only.",
    )
    parser.add_argument(
        "--environment-only",
        action="store_true",
        help="Refresh only race groups and venue environment while keeping prior market badges.",
    )
    parser.add_argument(
        "--signals-degraded",
        action="store_true",
        help="Mark signals stale and preserve the same-day last-good TOP signal payload.",
    )
    args = parser.parse_args()

    payload = web_app._build_top_page_snapshot_payload(
        args.date,
        allow_expensive_badges=not args.lightweight,
        include_market_signals=not args.environment_only,
    )
    if args.signals_degraded:
        market = payload.get("initial_market_signals")
        market = dict(market) if isinstance(market, dict) else {"date": args.date}
        market["degraded"] = True
        market["degraded_reason"] = "signal_refresh_failed"
        payload["initial_market_signals"] = market
        payload["market_signals_degraded"] = True
    groups = payload.get("stadium_groups") or []
    races = sum(len((group.get("races") or [])) for group in groups if isinstance(group, dict))
    badges = (
        (payload.get("initial_market_signals") or {}).get("race_badges") or {}
        if isinstance(payload.get("initial_market_signals"), dict)
        else {}
    )
    if races == 0:
        # レース 0 件のスナップショットを焼いてはいけない。前夜 22 時の翌日先回り
        # 生成は番組表取込 (23 時台) より先に走るため、そのまま保存すると
        # 「この日のデータはありません」が一日中出続ける (2026-09-02 の障害)。
        # 開催が本当に無い日は事実上存在しないので、書かずに終える方が安全。
        # 失敗ではなく「まだ早い」だけなので終了コードは 0 のままにする。
        print(
            "[top-snapshot] "
            f"date={args.date} SKIPPED write: no races yet "
            f"(existing snapshot left untouched) lightweight={bool(args.lightweight)}",
            flush=True,
        )
        return 0
    web_app._write_top_page_snapshot(args.date, payload)
    print(
        "[top-snapshot] "
        f"date={args.date} stadiums={len(groups)} races={races} "
        f"badged_races={len(badges)} lightweight={bool(args.lightweight)}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
