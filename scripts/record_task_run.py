"""タスク実行を task_runs に記録する軽量 CLI。

各 run_*.bat の最後に呼び、そのタスクが「今日実行された」ことを残す。
起動時キャッチアップ (startup_catchup.py) がこの記録を見て取りこぼしを判定する。

使い方:
    python scripts/record_task_run.py daily_collect success
    python scripts/record_task_run.py hourly failure --detail "exit=1"

trigger は環境変数 BOATRACE_TASK_TRIGGER で上書き可 (catchup 実行時に使用)。
本スクリプトは **絶対にタスク本体を壊さない** よう、何が起きても exit 0 で終わる。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("task_name")
    parser.add_argument("status", nargs="?", default="success",
                        choices=["success", "failure", "running"])
    parser.add_argument("--detail", default=None)
    parser.add_argument("--trigger", default=None,
                        help="未指定なら env BOATRACE_TASK_TRIGGER → 'scheduled'")
    args = parser.parse_args()

    trigger = args.trigger or os.getenv("BOATRACE_TASK_TRIGGER", "scheduled")
    try:
        from src.db import task_log
        task_log.record(args.task_name, args.status, trigger=trigger, detail=args.detail)
        print(f"[task_log] recorded {args.task_name}={args.status} (trigger={trigger})")
    except Exception as e:  # noqa: BLE001 - 記録失敗でタスクを止めない
        print(f"[task_log] WARN: failed to record {args.task_name}: "
              f"{type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(0)


if __name__ == "__main__":
    main()
