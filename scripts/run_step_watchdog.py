"""夜間処理の 1 手順を、固まったときに「どこで固まったか」を残して走らせる。

2026-09-15・09-19・09-20 の 3 晩、初手の backfill_official が固まって夜間処理が
丸ごと止まった。ログには手順名の行しか残らず、原因が追えなかった。理由は 2 つ:

1. 子プロセスの標準出力がまとめ書き (ブロックバッファ) で、強制終了と同時に
   消えていた。→ 親が ``-u`` 付きで呼ぶ。
2. 強制終了 (Windows は TerminateProcess) では、どの行で待っていたかが残らない。
   → この見張り役が、親に切られる前に自分で全スレッドの居場所を吐いて終わる。

使い方 (親から):
    python -u scripts/run_step_watchdog.py <秒数> scripts/backfill_official.py --start ...
"""
from __future__ import annotations

import faulthandler
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: run_step_watchdog.py <watchdog-seconds> <script> [args...]", file=sys.stderr)
        return 2
    try:
        watchdog = float(argv[0])
    except ValueError:
        print(f"watchdog seconds must be a number: {argv[0]!r}", file=sys.stderr)
        return 2
    script = argv[1]
    if watchdog > 0:
        # 期限が来たら全スレッドの現在地を stderr に吐いて終了する。親の制限時間
        # より前に鳴らすこと。exit=True なので、固まったままでも必ず終わる。
        faulthandler.dump_traceback_later(watchdog, exit=True)
    sys.argv = [script, *argv[2:]]
    path = Path(script)
    if not path.is_absolute():
        path = ROOT / path
    runpy.run_path(str(path), run_name="__main__")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except SystemExit:
        raise
    except BaseException:  # noqa: BLE001 - 子スクリプトの失敗はそのまま見せる
        import traceback

        traceback.print_exc()
        raise SystemExit(1)
