"""サーバー (データ収集を回しているローカル Windows PC) 起動時のタスク・キャッチアップ。

PC がスケジュール時刻にダウン/スリープしていて実行されなかったタスクを、
起動時に検出して実行する。判定根拠は task_runs テーブル (ローカル SQLite) の
「今日の成功記録」(src/db/task_log.py)。

Task Scheduler の ONSTART トリガで run_startup_catchup.bat 経由で呼ばれる想定
(install_all_tasks.ps1 が BoatraceStartupCatchup として登録)。

手動実行 / 動作確認:
    python scripts/startup_catchup.py --dry-run        # 判定だけ表示 (実行しない)
    python scripts/startup_catchup.py                  # 取りこぼしを実行
    python scripts/startup_catchup.py --only morning   # 特定タスクのみ
    python scripts/startup_catchup.py --force          # 判定無視で対象を全実行

対象タスク (odds 毎分・sync は対象外):
    daily_collect  06:00 once   … 番組表/結果/予測キャッシュ等
    morning        06:30 once   … 朝 L4 候補 + アラート
    hourly         09-23 2h枠   … 結果リフレッシュ (最新枠が未取得なら実行)
    poll_results   08:30-23:00  … 結果ポーリング (鮮度切れなら実行)
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# schtasks リダイレクト先 (ファイル) でも UTF-8 で出せるように。失敗しても無視。
try:  # pragma: no cover
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

from src.db import task_log  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"

# キャッチアップ対象タスク定義 (単一の真実)
#   strategy:
#     daily_once : times[0] を過ぎていて当日未成功なら実行 (daily_collect, morning)
#     windows    : 直近に過ぎた枠より後の成功が無ければ実行 (hourly)
#     interval   : 稼働時間内で、最後の成功が stale_min 分より古ければ実行 (poll_results)
TASKS = [
    {"name": "daily_collect", "label": "daily_collect", "bat": "run_daily_collect.bat",
     "strategy": "daily_once", "times": ["06:00"]},
    {"name": "morning", "label": "morning", "bat": "run_morning_task.bat",
     "strategy": "daily_once", "times": ["06:30"]},
    {"name": "hourly", "label": "hourly", "bat": "run_hourly_task.bat",
     "strategy": "windows",
     "times": ["09:00", "11:00", "13:00", "15:00", "17:00", "19:00", "21:00", "23:00"]},
    {"name": "beforeinfo_live", "label": "beforeinfo_live", "bat": "run_beforeinfo_live.bat",
     "strategy": "log_interval", "active": ["08:00", "22:00"], "stale_min": 20,
     "log_glob": "beforeinfo_live_*.log"},
    {"name": "poll_results", "label": "poll_results", "bat": "run_poll_results.bat",
     "strategy": "interval", "active": ["08:30", "23:00"], "stale_min": 15},
]


def _at(hhmm: str, base: datetime) -> datetime:
    """base と同じ日付の hh:mm を返す。"""
    h, m = (int(x) for x in hhmm.split(":"))
    return base.replace(hour=h, minute=m, second=0, microsecond=0)


def last_log_update(task: dict) -> datetime | None:
    """Return the latest log mtime for a task."""
    log_glob = task.get("log_glob")
    if not log_glob:
        return None
    log_dir = ROOT / "logs"
    latest = None
    for p in log_dir.glob(log_glob):
        try:
            dt = datetime.fromtimestamp(p.stat().st_mtime)
        except OSError:
            continue
        if latest is None or dt > latest:
            latest = dt
    return latest


def needs_catchup(task: dict, now: datetime):
    """(必要か, 理由) を返す。last_success は task_runs から取得。"""
    last = task_log.last_success_at(task["name"])
    strat = task["strategy"]

    if strat == "daily_once":
        due = _at(task["times"][0], now)
        if now < due:
            return False, f"予定時刻前 ({task['times'][0]})"
        if last is None:
            return True, f"{task['times'][0]} 予定が未実行"
        return False, f"実行済 ({last:%H:%M})"

    if strat == "windows":
        passed = [_at(t, now) for t in task["times"] if _at(t, now) <= now]
        if not passed:
            return False, f"最初の枠前 ({task['times'][0]})"
        recent = max(passed)
        if last is None or last < recent:
            return True, f"{recent:%H:%M} 枠が未取得"
        return False, f"最新枠取得済 ({last:%H:%M})"

    if strat == "interval":
        start = _at(task["active"][0], now)
        end = _at(task["active"][1], now)
        if not (start <= now <= end):
            return False, "outside active window"
        if last is None:
            return True, "not run today"
        gap_min = (now - last).total_seconds() / 60
        if gap_min > task.get("stale_min", 15):
            return True, f"last success {gap_min:.0f} min ago (stale)"
        return False, f"recent success exists ({last:%H:%M})"

    if strat == "log_interval":
        start = _at(task["active"][0], now)
        end = _at(task["active"][1], now)
        if not (start <= now <= end):
            return False, "outside active window"
        last_log = last_log_update(task)
        if last_log is None:
            return True, "log missing"
        gap_min = (now - last_log).total_seconds() / 60
        if gap_min > task.get("stale_min", 20):
            return True, f"last update {gap_min:.0f} min ago (log stale)"
        return False, f"recent log exists ({last_log:%H:%M})"

    return False, f"unknown strategy={strat}"


def run_task(task: dict, log) -> bool:
    """対象タスクの bat を subprocess 実行。bat 末尾の record_task_run.py が
    task_runs に成功を記録する (trigger=catchup を env で伝える)。"""
    bat = SCRIPTS / task["bat"]
    if not bat.exists():
        log(f"  [SKIP] bat が見つかりません: {bat}")
        return False
    env = os.environ.copy()
    env["BOATRACE_TASK_TRIGGER"] = "catchup"
    log(f"  [RUN ] {task['bat']} 実行中...")
    try:
        proc = subprocess.run(
            ["cmd", "/c", str(bat)],
            cwd=str(ROOT), env=env,
            capture_output=True, text=True, timeout=3600,
        )
        ok = proc.returncode == 0
        log(f"  [{'OK  ' if ok else 'NG  '}] {task['bat']} exit={proc.returncode}")
        if not ok:
            # 失敗時は task_runs にも failure を残す (bat 側が記録しない場合の保険)
            try:
                task_log.record(task["name"], "failure", trigger="catchup",
                                detail=f"catchup exit={proc.returncode}")
            except Exception:  # noqa: BLE001
                pass
        return ok
    except subprocess.TimeoutExpired:
        log(f"  [NG  ] {task['bat']} タイムアウト (60分)")
        return False
    except Exception as e:  # noqa: BLE001
        log(f"  [NG  ] {task['bat']} 実行失敗: {type(e).__name__}: {e}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="起動時タスクキャッチアップ")
    parser.add_argument("--dry-run", action="store_true", help="判定のみ。実行しない")
    parser.add_argument("--force", action="store_true", help="判定を無視して対象を全実行")
    parser.add_argument("--only", help="特定タスクのみ (name)")
    args = parser.parse_args()

    def log(msg: str) -> None:
        print(f"{datetime.now():%H:%M:%S} {msg}", flush=True)

    now = datetime.now()
    log(f"=== 起動時タスクキャッチアップ {now:%Y-%m-%d %H:%M:%S} ===")

    ran = caught = skipped = 0
    for task in TASKS:
        if args.only and task["name"] != args.only:
            continue
        if args.force:
            need, why = True, "強制 (--force)"
        else:
            need, why = needs_catchup(task, now)
        log(f"[{task['label']:<10}] {'要実行' if need else '不要 '}: {why}")
        if not need:
            skipped += 1
            continue
        if args.dry_run:
            log("  (--dry-run のため実行せず)")
            continue
        ran += 1
        if run_task(task, log):
            caught += 1

    log(f"完了: 実行 {ran} (成功 {caught}) / 不要 {skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
