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
import os
import argparse

# Windows cp932 環境でも絵文字を含む print が落ちないよう stdout を UTF-8 化
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

# --local フラグの場合は config import 前に DATABASE_URL を削除
# (config.py が load_dotenv で .env から DATABASE_URL を再注入してしまうため、
#  delete + load_dotenv 前に対処する必要がある)
if "--local" in sys.argv:
    os.environ.pop("DATABASE_URL", None)
    # config.py の load_dotenv(override=False) は既存の環境変数を尊重するため、
    # 環境変数に空文字をセットしておけば .env の値が読まれない
    os.environ["DATABASE_URL"] = ""

import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.collectors import openapi


JST = ZoneInfo("Asia/Tokyo")


def _today_jst() -> date:
    return datetime.now(JST).date()


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--date", type=str, help="YYYY-MM-DD 形式 (省略時は今日)")
    p.add_argument("--backfill", type=int, default=0,
                   help="指定日数分過去にさかのぼって取得 (例: 30 = 30日前まで)")
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--log-file", type=str, default=None,
                   help="ログをファイルへ出力 (UTF-8 追記)")
    p.add_argument("--local", action="store_true",
                   help="DATABASE_URL を無視してローカル SQLite に投入する "
                        "(cache_predictions.py 用に明示的にローカルへ書く時)")
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

    target = date.fromisoformat(args.date) if args.date else _today_jst()

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
