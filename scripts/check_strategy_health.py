"""戦略の健全度チェック CLI

各戦略 (L4 / L4+ / L4++ / A2派生) について、直近の実測 ROI が
検証ベースラインから逸脱していないかチェックして、停止推奨を判定する。

使い方:
  # 直近90日で評価
  python scripts/check_strategy_health.py

  # 期間指定
  python scripts/check_strategy_health.py --from 2026-01-01 --to 2026-05-13

  # critical / warning だけ表示
  python scripts/check_strategy_health.py --only-warnings

  # JSON で出力 (cron / Web API 連携用)
  python scripts/check_strategy_health.py --json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
except ImportError:
    pass

from src.evaluation.strategy_monitor import (
    evaluate_all_strategies,
    print_health_summary,
    STRATEGY_DEFINITIONS,
)


def main():
    p = argparse.ArgumentParser()
    today = date.today().isoformat()
    default_from = (date.today() - timedelta(days=90)).isoformat()
    p.add_argument("--from", dest="from_date", default=default_from,
                   help=f"開始日 (default: {default_from})")
    p.add_argument("--to", dest="to_date", default=today,
                   help=f"終了日 (default: {today})")
    p.add_argument("--only-warnings", action="store_true",
                   help="critical/warning だけ表示")
    p.add_argument("--json", dest="as_json", action="store_true",
                   help="JSON 形式で出力")
    args = p.parse_args()

    print(f"=== 戦略健全度チェック ({args.from_date} 〜 {args.to_date}) ===\n")

    results = evaluate_all_strategies(args.from_date, args.to_date)

    if args.only_warnings:
        results = [r for r in results if r["status"] in ("warning", "critical")]
        if not results:
            print("✅ 警告レベルの戦略なし、全て健全。")
            return

    if args.as_json:
        # daily は容量大きいので JSON ではカット
        slim = []
        for r in results:
            r2 = {k: v for k, v in r.items() if k != "daily"}
            slim.append(r2)
        print(json.dumps(slim, ensure_ascii=False, indent=2, default=str))
        return

    print_health_summary(results)

    # 全体サマリ
    critical = [r for r in results if r["status"] == "critical"]
    warning = [r for r in results if r["status"] == "warning"]
    print()
    print(f"=== サマリ ===")
    print(f"  🔴 critical: {len(critical)} 戦略")
    print(f"  🟠 warning : {len(warning)} 戦略")
    print(f"  全戦略数   : {len(results)}")
    if critical:
        print()
        print(f"⚠️ 停止推奨の戦略あり: {', '.join(r['name'] for r in critical)}")


if __name__ == "__main__":
    main()
