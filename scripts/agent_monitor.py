"""各エージェント (スケジュールタスク / 常駐スクレイパー) の死活監視。

ローカル PC で動いている全エージェントを 1 本でチェックし、結果を
`system_status` テーブル (check_name='agent_*') に書き込む。
Web UI の品質バナーが自動でこれを表示し、ローカルの不調を Render 側でも
ひと目で気付けるようにする。

監視対象:
  ▼ task_runs ベース (record_task_run.py が書き込んだ最終成功時刻)
    - daily_collect          : 日次データ収集 (06:00)
    - morning                : 朝 L4 候補 (06:30)
    - hourly                 : 時間別結果リフレッシュ (09-23 2h枠)
    - poll_results           : 結果ポーリング (5min, 08:30-23:00)
  ▼ ログ更新時刻ベース (task_runs に書かない常駐プロセス)
    - odds_scheduler         : 毎分オッズスナップ
    - beforeinfo_live        : 直前情報スクレイプ (10min, 08:00-22:00)
  ▼ HTTP プローブ
    - Render Web /healthz
  ▼ DB プローブ
    - Supabase Postgres 接続

判定:
  ok      : 期待頻度内に動いている
  warning : ok 閾値超過だが許容上限内 (遅延あり)
  error   : 許容上限超過 (停止疑い)

使い方:
    python scripts/agent_monitor.py             # 通常実行 (system_status へ書込)
    python scripts/agent_monitor.py --quiet     # 出力抑制、終了コードのみ
    python scripts/agent_monitor.py --no-write  # 書込みせず表示のみ (テスト用)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

import config
from src.db import task_log

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"
NOW = datetime.now()
TODAY = NOW.date()

# 期待スケジュール (task_runs ベース)
#   ok_h    : この時間以内なら ok
#   warn_h  : この時間以内なら warning (越えると error)
#   active  : (start_hour, end_hour) この時間帯外は判定をスキップして ok 扱い
TASK_CHECKS = [
    {"name": "daily_collect", "label": "日次データ収集",
     "ok_h": 24, "warn_h": 30, "active": None},
    {"name": "morning", "label": "朝L4候補",
     "ok_h": 24, "warn_h": 30, "active": None},
    {"name": "hourly", "label": "時間別結果",
     "ok_h": 3, "warn_h": 6, "active": (9, 23.5)},
    {"name": "poll_results", "label": "結果ポーリング",
     "ok_h": 0.3, "warn_h": 1, "active": (8.5, 23)},
]

# ログ更新時刻ベース (常駐スクレイパー、task_runs に書き込まないもの)
LOG_CHECKS = [
    {"glob": "odds_scheduler", "label": "オッズスナップ",
     "ok_min": 5, "warn_min": 15, "active": (8.5, 22.5)},
    {"glob": "beforeinfo_live", "label": "直前情報スクレイプ",
     "ok_min": 15, "warn_min": 30, "active": (8, 22)},
]

ICON = {"ok": "OK  ", "warning": "WARN", "error": "ERR "}


def _hour_now() -> float:
    return NOW.hour + NOW.minute / 60


def _in_active(active):
    if active is None:
        return True
    return active[0] <= _hour_now() <= active[1]


def check_task(task: dict):
    if not _in_active(task["active"]):
        return "ok", f"稼働時間外 (現在 {_hour_now():.1f}h)"
    last = task_log.last_success_at(task["name"], run_date=TODAY.isoformat())
    if last is None:
        last = task_log.last_success_at(
            task["name"],
            run_date=(TODAY - timedelta(days=1)).isoformat(),
        )
    if last is None:
        return "error", "24h以内に成功記録なし (task_runs)"
    age_h = (NOW - last).total_seconds() / 3600
    if age_h <= task["ok_h"]:
        return "ok", f"{age_h:.1f}h前に成功 ({last:%m-%d %H:%M})"
    if age_h <= task["warn_h"]:
        return "warning", f"{age_h:.1f}h前から停止 (期待 ≤{task['ok_h']}h)"
    return "error", f"{age_h:.1f}h前から停止 (期待 ≤{task['ok_h']}h)"


def check_log(spec: dict):
    if not _in_active(spec["active"]):
        return "ok", f"稼働時間外 (現在 {_hour_now():.1f}h)"
    pat = spec["glob"]
    candidates: list[Path] = []
    for d in (TODAY, TODAY - timedelta(days=1)):
        candidates.extend(LOG_DIR.glob(f"{pat}_{d.strftime('%Y%m%d')}*.log"))
    if not candidates:
        return "error", "ログファイルなし"
    latest = max(candidates, key=lambda p: p.stat().st_mtime)
    age_min = (NOW.timestamp() - latest.stat().st_mtime) / 60
    if age_min <= spec["ok_min"]:
        return "ok", f"{age_min:.0f}分前に更新 ({latest.name})"
    if age_min <= spec["warn_min"]:
        return "warning", f"{age_min:.0f}分前から更新なし (期待 ≤{spec['ok_min']}分)"
    return "error", f"{age_min:.0f}分前から更新なし (期待 ≤{spec['ok_min']}分)"


def check_render():
    try:
        req = urllib.request.Request(
            "https://boatrace-web.onrender.com/healthz",
            headers={"User-Agent": "boatrace-monitor/1.0"},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            body = r.read().decode("utf-8", errors="replace")
            if r.status == 200 and '"status":"ok"' in body:
                return "ok", "healthz 200 (status:ok)"
            return "warning", f"status={r.status} body={body[:80]}"
    except (urllib.error.URLError, TimeoutError) as e:
        return "error", f"unreachable: {type(e).__name__}: {e}"
    except Exception as e:  # noqa: BLE001
        return "error", f"{type(e).__name__}: {e}"


def check_supabase():
    if not os.getenv("DATABASE_URL", "").strip():
        return "warning", "DATABASE_URL 未設定 (検査不可)"
    try:
        from src.db.connection import connect as db_connect
        conn = db_connect()
        conn.execute("SELECT 1").fetchone()
        conn.close()
        return "ok", "接続 OK"
    except Exception as e:  # noqa: BLE001
        return "error", f"接続失敗: {type(e).__name__}: {e}"


def _upsert_status(conn, check_name: str, status: str, message: str,
                   detail: dict | None = None) -> None:
    """system_status へ upsert (check_data_quality.py と同様)。"""
    now_iso = datetime.now().isoformat(timespec="seconds")
    today_iso = TODAY.isoformat()
    detail_json = json.dumps(detail or {}, ensure_ascii=False)
    row = conn.execute(
        "SELECT 1 FROM system_status WHERE check_name=? AND check_date=?",
        (check_name, today_iso),
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE system_status SET status=?, message=?, detail_json=?, "
            "checked_at=? WHERE check_name=? AND check_date=?",
            (status, message, detail_json, now_iso, check_name, today_iso),
        )
    else:
        conn.execute(
            "INSERT INTO system_status (check_name, check_date, status, message, "
            "detail_json, checked_at) VALUES (?, ?, ?, ?, ?, ?)",
            (check_name, today_iso, status, message, detail_json, now_iso),
        )
    conn.commit()


def _write_results(results: list[tuple], target: str) -> None:
    """ローカル SQLite + (DATABASE_URL あれば) Supabase に書込み。"""
    # Local
    try:
        local = sqlite3.connect(config.DB_PATH)
        for cn, _, status, msg in results:
            _upsert_status(local, cn, status, msg)
        local.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("local system_status write failed: %s", e)
    # Supabase
    if target == "both" and os.getenv("DATABASE_URL", "").strip():
        try:
            from src.db.connection import connect as db_connect
            pg = db_connect()
            for cn, _, status, msg in results:
                try:
                    _upsert_status(pg, cn, status, msg)
                except Exception as e:  # noqa: BLE001
                    logger.warning("supabase write failed for %s: %s", cn, e)
            pg.close()
        except Exception as e:  # noqa: BLE001
            logger.warning("supabase connect failed: %s", e)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--no-write", action="store_true",
                        help="system_status に書かない (テスト用)")
    args = parser.parse_args()

    results: list[tuple[str, str, str, str]] = []  # (check_name, label, status, msg)

    if not args.quiet:
        print(f"=== エージェント監視 {NOW:%Y-%m-%d %H:%M:%S} ===\n")

    for t in TASK_CHECKS:
        s, m = check_task(t)
        results.append((f"agent_{t['name']}", t["label"], s, m))
    for lg in LOG_CHECKS:
        s, m = check_log(lg)
        results.append((f"agent_{lg['glob']}", lg["label"], s, m))
    s, m = check_render()
    results.append(("agent_render_web", "Render Web", s, m))
    s, m = check_supabase()
    results.append(("agent_supabase", "Supabase接続", s, m))

    n_warn = n_err = 0
    for cn, label, status, msg in results:
        if not args.quiet:
            print(f"  [{ICON[status]}] {label:<18} {msg}")
        if status == "warning":
            n_warn += 1
        if status == "error":
            n_err += 1

    if not args.no_write:
        _write_results(results, target="both")

    if not args.quiet:
        print()
        if n_err:
            print(f"完了: ERROR {n_err} / WARNING {n_warn}")
        elif n_warn:
            print(f"完了: WARNING {n_warn}")
        else:
            print("完了: すべて OK")

    if n_err:
        return 2
    if n_warn:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
