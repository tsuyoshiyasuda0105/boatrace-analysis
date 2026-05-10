"""
日次データ取得スクリプト

1日の終わりに cron 等で走らせる想定。

使い方:
    python scripts/daily_collect.py                    # 今日のデータ
    python scripts/daily_collect.py --date 2026-05-08  # 特定日
    python scripts/daily_collect.py --backfill 30      # 過去30日分

全レイヤー (Layer 2 Open API) の取得を行う。
Layer 1 (公式DL) と Layer 3 (スクレイピング) は別スクリプトで実行する設計。
"""
import sys
import argparse
import logging
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.collectors import openapi


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--date", type=str, help="YYYY-MM-DD 形式 (省略時は今日)")
    p.add_argument("--backfill", type=int, default=0,
                   help="指定日数分過去にさかのぼって取得 (例: 30 = 30日前まで)")
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--log-file", type=str, default=None,
                   help="ログをファイルへ出力 (UTF-8 追記)")
    return p.parse_args()


def main():
    args = parse_args()
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if args.log_file:
        handlers.append(logging.FileHandler(args.log_file, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers,
        force=True,
    )

    target = date.fromisoformat(args.date) if args.date else date.today()

    if args.backfill > 0:
        targets = [target - timedelta(days=i) for i in range(args.backfill + 1)]
    else:
        targets = [target]

    for d in targets:
        summary = openapi.collect_all(d)
        print(
            f"{summary['date']}: "
            f"programs={summary['programs']}, "
            f"previews={summary['previews']}, "
            f"results={summary['results']}"
        )


if __name__ == "__main__":
    main()
