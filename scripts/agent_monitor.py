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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

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
JST = ZoneInfo("Asia/Tokyo")


def _now_jst() -> datetime:
    return datetime.now(JST)


def _normalize_jst_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=JST)
    return value.astimezone(JST)


NOW = _now_jst()
TODAY = NOW.date()
PC_PAUSED_MSG = "PC-local checks skipped; Render cron is primary"


def _truthy_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _has_local_pause_flag() -> bool:
    return (
        (ROOT / ".pc_schedule_paused").exists()
        or bool(os.getenv("RENDER", "").strip())
        or os.getenv("BOATRACE_PC_SCHEDULE_PAUSED", "").strip().lower()
        in {"1", "true", "yes", "on"}
    )


def _local_table_exists(table_name: str) -> bool:
    try:
        conn = sqlite3.connect(config.DB_PATH)
        try:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,),
            ).fetchone()
            return bool(row)
        finally:
            conn.close()
    except Exception:
        return False


def _render_primary_mode() -> bool:
    if _truthy_env("BOATRACE_RENDER_PRIMARY") or _truthy_env("BOATRACE_SUPABASE_ONLY"):
        return True
    # Current operations use Render/Supabase as the primary runtime and may
    # intentionally pause or remove local scheduler state on Windows. In that
    # mode, local task/log/work checks only create noisy false positives in the
    # member health board, so infer "Render primary" from the same pause
    # signals we already use for the local scheduler.
    return _has_local_pause_flag()


def _pc_schedule_paused() -> bool:
    if _has_local_pause_flag():
        return True
    core_tables = ("races", "predictions")
    if not all(_local_table_exists(name) for name in core_tables):
        return True
    return False


def _skip_local_taskrun_check(task_name: str) -> bool:
    return _render_primary_mode() and task_name in {"daily_collect", "morning", "hourly", "poll_results"}


def _skip_local_log_check(log_name: str) -> bool:
    return _render_primary_mode() and log_name in {"odds_scheduler", "beforeinfo_live"}


def _skip_local_work_check() -> bool:
    return _render_primary_mode()

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
    if _skip_local_taskrun_check(task["name"]):
        return "ok", "local task_runs check disabled; Render cron is primary"
    if _pc_schedule_paused():
        return "ok", PC_PAUSED_MSG
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
    last = _normalize_jst_datetime(last)
    age_h = (NOW - last).total_seconds() / 3600
    if age_h <= task["ok_h"]:
        return "ok", f"{age_h:.1f}h前に成功 ({last:%m-%d %H:%M})"
    if age_h <= task["warn_h"]:
        return "warning", f"{age_h:.1f}h前から停止 (期待 ≤{task['ok_h']}h)"
    return "error", f"{age_h:.1f}h前から停止 (期待 ≤{task['ok_h']}h)"


def check_log(spec: dict):
    if _skip_local_log_check(spec["glob"]):
        return "ok", "local log check disabled; Render cron is primary"
    if _pc_schedule_paused():
        return "ok", PC_PAUSED_MSG
    if not _in_active(spec["active"]):
        return "ok", f"稼働時間外 (現在 {_hour_now():.1f}h)"
    pat = spec["glob"]
    candidates: list[Path] = []
    # 日付付きログ (例: beforeinfo_live_20260530.log)
    for d in (TODAY, TODAY - timedelta(days=1)):
        candidates.extend(LOG_DIR.glob(f"{pat}_{d.strftime('%Y%m%d')}*.log"))
    # 日付なし固定名ログも候補に (例: odds_scheduler.log)
    # 2026-05-30: 監視ロジック修正 — odds_scheduler は単一ファイルに append する
    # 設計なので日付付きでは見つからない → 固定名も追加
    candidates.extend(LOG_DIR.glob(f"{pat}.log"))
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
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                payload = {}
            checks = payload.get("checks") if isinstance(payload, dict) else {}
            if r.status == 200 and isinstance(checks, dict) and checks.get("db") == "ok":
                return "ok", "healthz 200 (db:ok)"
            if r.status == 200 and payload.get("status") in {"ok", "warning", "degraded"}:
                return "ok", f"healthz 200 (status:{payload.get('status')})"
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


# ======================================================================
# サボリ検知 (タスクが "成功" したと record していても実際に成果物が
# 出ていないケース = 仕事をしていない/サボリ を検出する)
# ======================================================================

def _local_conn():
    return sqlite3.connect(config.DB_PATH)


def _local_checks_paused():
    if _pc_schedule_paused():
        return True
    return False


def _supabase_conn():
    if not os.getenv("DATABASE_URL", "").strip():
        return None
    from src.db.connection import connect as db_connect
    return db_connect()


def work_daily_collect():
    """daily_collect が「今日のレース」を実際に取り込んでいるか。
    成功記録あっても races が 0 件ならサボリ。"""
    today = TODAY.isoformat()
    if _skip_local_work_check():
        return "ok", "local work check disabled; Render cron is primary"
    if _local_checks_paused():
        return "ok", PC_PAUSED_MSG
    try:
        c = _local_conn()
        n_races = c.execute("SELECT COUNT(*) FROM races WHERE race_date=?",
                            (today,)).fetchone()[0]
        # 翌日分の事前取得もチェック (前夜 23:30 で取るはず)
        tmr = (TODAY + timedelta(days=1)).isoformat()
        n_tmr = c.execute("SELECT COUNT(*) FROM races WHERE race_date=?",
                          (tmr,)).fetchone()[0]
        c.close()
    except Exception as e:  # noqa: BLE001
        return "error", f"DB エラー: {e}"
    if n_races == 0:
        # 6時以降に今日のレースが0件 = 明らかにサボリ
        if _hour_now() >= 6:
            return "error", f"今日のレース 0 件 (取込サボリ?)"
        return "ok", "未取込 (朝6時前は正常)"
    msg = f"今日 {n_races} R"
    # 翌日分は 23:30 daily_collect で投入。22時以降にゼロは怪しい
    if _hour_now() >= 22 and n_tmr == 0:
        return "warning", f"{msg} / 明日 0 件 (前夜先行投入の取りこぼし?)"
    if n_tmr > 0:
        msg += f" + 明日 {n_tmr} R"
    return "ok", msg


def work_morning_predict():
    """morning が「今日の予測」を実際に生成しているか。
    成功記録ありで predictions が極端に少ないならサボリ。"""
    today = TODAY.isoformat()
    if _skip_local_work_check():
        return "ok", "local work check disabled; Render cron is primary"
    if _local_checks_paused():
        return "ok", PC_PAUSED_MSG
    try:
        c = _local_conn()
        n = c.execute(
            "SELECT COUNT(DISTINCT p.race_id) FROM predictions p "
            "JOIN races r ON p.race_id=r.race_id WHERE r.race_date=?",
            (today,),
        ).fetchone()[0]
        n_races = c.execute("SELECT COUNT(*) FROM races WHERE race_date=?",
                            (today,)).fetchone()[0]
        c.close()
    except Exception as e:  # noqa: BLE001
        return "error", f"DB エラー: {e}"
    if n_races == 0:
        return "ok", "今日レースなし"
    # 7時以降 (morning は 06:30) に予測ゼロ = サボリ
    if _hour_now() >= 7 and n == 0:
        return "error", "今日の予測 0 件 (生成サボリ?)"
    cov = n / n_races * 100 if n_races else 0
    if _hour_now() >= 8 and cov < 50:
        return "warning", f"予測カバレッジ低 {n}/{n_races} ({cov:.0f}%)"
    return "ok", f"予測 {n}/{n_races} ({cov:.0f}%)"


def work_odds_scheduler():
    """odds_scheduler が直近実際にスナップを書いているか。
    Supabase にしか書かないので Supabase をチェック。
    レース時間中に直近 10 分のスナップが 0 件 = サボリ。"""
    if _local_checks_paused():
        return "ok", PC_PAUSED_MSG
    if not (8.5 <= _hour_now() <= 22.5):
        return "ok", "稼働時間外"
    pg = _supabase_conn()
    if pg is None:
        return "warning", "DATABASE_URL 未設定 (確認不可)"
    try:
        # Postgres でも SQLite でも "直近 N 分" は recorded_at との差で取る。
        # 重要: odds_trifecta.recorded_at は collectors/odds.py が
        # datetime.utcnow() で書く UTC の ISO 文字列。一方 NOW は
        # datetime.now() = ローカル (JST=UTC+9)。ここで NOW から cutoff を
        # 作ると JST 基準になり、UTC で記録された recorded_at とは常に 9h
        # ずれて COUNT=0 → レース時間中でも恒常的に誤 ERROR を出していた
        # (2026-05-30 19:16 の「直近10分0件(サボリ?)」誤検知の根本原因)。
        # cutoff も UTC で作って recorded_at と同一時系で比較する。
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(timespec="seconds")
        cur = pg.execute(
            "SELECT COUNT(*) FROM odds_trifecta WHERE recorded_at >= ?",
            (cutoff,),
        )
        n = cur.fetchone()[0]
        pg.close()
    except Exception as e:  # noqa: BLE001
        return "error", f"クエリ失敗: {e}"
    if n == 0:
        return "warning", "直近10分のオッズ取得 0 件 (対象レースなしの可能性)"
    if n < 20:
        return "warning", f"直近10分 {n} 件 (少ない)"
    return "ok", f"直近10分 {n} スナップ"


def work_beforeinfo_live():
    """beforeinfo_live が締切間近のレースに live データを書いているか。
    締切が 5-9 分後のレースを対象 (live スクレイパーの target window)。
    そのレースに live_updated_at が無ければサボリ。"""
    if not (8 <= _hour_now() <= 22):
        return "ok", "稼働時間外"
    if _skip_local_work_check():
        return "ok", "local work check disabled; Render cron is primary"
    if _local_checks_paused():
        return "ok", PC_PAUSED_MSG
    today = TODAY.isoformat()
    now_s = NOW.strftime("%Y-%m-%d %H:%M:%S")
    later_s = (NOW + timedelta(minutes=9)).strftime("%Y-%m-%d %H:%M:%S")
    soon_s = (NOW + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        c = _local_conn()
        # 締切まで 5-9 分のレース (target window)
        rows = c.execute(
            """SELECT r.race_id, MAX(pv.live_updated_at)
                 FROM races r
                 LEFT JOIN race_previews pv ON pv.race_id=r.race_id
                WHERE r.race_date=? AND r.race_closed_at BETWEEN ? AND ?
                GROUP BY r.race_id""",
            (today, soon_s, later_s),
        ).fetchall()
        c.close()
    except Exception as e:  # noqa: BLE001
        return "error", f"DB エラー: {e}"
    if not rows:
        return "ok", "対象レースなし (締切5-9分のレースが今ない)"
    total = len(rows)
    no_live = [rid for rid, upd in rows if not upd]
    if no_live and len(no_live) == total:
        return "error", f"対象 {total} レース全部 live未更新 (サボリ?)"
    if no_live:
        return "warning", f"{len(no_live)}/{total} レース live未更新"
    return "ok", f"対象 {total} レース全部 live更新済"


WORK_CHECKS = [
    ("agent_work_daily_collect", "daily_collect の仕事", work_daily_collect),
    ("agent_work_morning",        "morning の仕事",     work_morning_predict),
    ("agent_work_odds_scheduler", "odds_scheduler の仕事", work_odds_scheduler),
    ("agent_work_beforeinfo",     "beforeinfo_live の仕事", work_beforeinfo_live),
]


def _upsert_status(conn, check_name: str, status: str, message: str,
                   detail: dict | None = None) -> None:
    """system_status へ upsert (check_data_quality.py と同様)。"""
    now_iso = _now_jst().replace(tzinfo=None).isoformat(timespec="seconds")
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
    # ▼ サボリ検知 (タスクが「成功」記録していても成果物が無いケース検出)
    for cn, label, fn in WORK_CHECKS:
        try:
            s, m = fn()
        except Exception as e:  # noqa: BLE001
            s, m = "error", f"check失敗: {type(e).__name__}: {e}"
        results.append((cn, label, s, m))

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
