from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from src.collectors.tide import load_tide_station_map
from src.db import task_log

ROOT = Path(__file__).resolve().parents[1]
MORNING_BAT = ROOT / "scripts" / "run_morning_task.bat"
SELF_HEAL_TASK = "self_heal_today_data"
RETRY_MINUTES = 30
PRELOAD_HOUR = 0
PRELOAD_MINUTE = 5
MORNING_DUE_HOUR = 6
MORNING_DUE_MINUTE = 30


def _now() -> datetime:
    return datetime.now()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=config.SQLITE_CONNECT_TIMEOUT_SECONDS)
    conn.execute(f"PRAGMA busy_timeout={config.SQLITE_BUSY_TIMEOUT_MS};")
    return conn


def _tide_stadiums() -> list[int]:
    mapping = load_tide_station_map()
    return sorted(int(k) for k in mapping.keys() if str(k).isdigit())


def _count(conn: sqlite3.Connection, sql: str, params: tuple) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0] or 0) if row else 0


def _today_state(target_date: str) -> dict:
    tide_stadiums = _tide_stadiums()
    placeholders = ",".join("?" for _ in tide_stadiums) or "NULL"
    with _conn() as conn:
        races = _count(conn, "SELECT COUNT(*) FROM races WHERE race_date=?", (target_date,))
        predictions = _count(
            conn,
            """
            SELECT COUNT(DISTINCT p.race_id)
              FROM predictions p
              JOIN races r ON r.race_id = p.race_id
             WHERE r.race_date=?
            """,
            (target_date,),
        )
        previews = _count(
            conn,
            """
            SELECT COUNT(DISTINCT pv.race_id)
              FROM race_previews pv
              JOIN races r ON r.race_id = pv.race_id
             WHERE r.race_date=?
            """,
            (target_date,),
        )
        if tide_stadiums:
            tide_expected = _count(
                conn,
                f"""
                SELECT COUNT(*) FROM races
                 WHERE race_date=?
                   AND stadium_number IN ({placeholders})
                """,
                (target_date, *tide_stadiums),
            )
            tides = _count(
                conn,
                f"""
                SELECT COUNT(DISTINCT rt.race_id)
                  FROM race_tides rt
                  JOIN races r ON r.race_id = rt.race_id
                 WHERE r.race_date=?
                   AND r.stadium_number IN ({placeholders})
                """,
                (target_date, *tide_stadiums),
            )
        else:
            tide_expected = 0
            tides = 0
    return {
        "races": races,
        "predictions": predictions,
        "previews": previews,
        "tide_expected": tide_expected,
        "tides": tides,
    }


def _recent_attempt_exists(now: datetime) -> bool:
    row = task_log.get_today(SELF_HEAL_TASK)
    if not row:
        return False
    finished_at = row.get("finished_at")
    if not finished_at:
        return False
    try:
        finished = datetime.fromisoformat(finished_at)
    except ValueError:
        return False
    age_min = (now - finished).total_seconds() / 60
    return age_min < RETRY_MINUTES


def _should_heal(state: dict, now: datetime) -> tuple[bool, str]:
    preload_due = now.replace(hour=PRELOAD_HOUR, minute=PRELOAD_MINUTE, second=0, microsecond=0)
    if now < preload_due:
        return False, "before preload due"
    morning_ok = task_log.last_success_at("morning", run_date=now.strftime("%Y-%m-%d"))
    if morning_ok and state["races"] > 0 and state["predictions"] > 0:
        if state["tide_expected"] == 0 or state["tides"] > 0:
            return False, "morning already healthy"
    reasons: list[str] = []
    if state["races"] == 0:
        reasons.append("races=0")
    if state["predictions"] == 0:
        reasons.append("predictions=0")
    if state["tide_expected"] > 0 and state["tides"] == 0:
        reasons.append("tides=0")
    if not reasons and not morning_ok:
        reasons.append("morning_missing")
    return bool(reasons), ",".join(reasons) if reasons else "no critical gap"


def _run_bat(path: Path) -> tuple[bool, str]:
    env = os.environ.copy()
    env["BOATRACE_TASK_TRIGGER"] = "self_heal"
    proc = subprocess.run(
        ["cmd", "/c", str(path)],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=3600,
    )
    if proc.returncode == 0:
        return True, "exit=0"
    tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
    return False, f"exit={proc.returncode} tail={' | '.join(tail)}"


def _run_py(args: list[str], *, timeout: int = 3600) -> tuple[bool, str]:
    env = os.environ.copy()
    proc = subprocess.run(
        [str(ROOT / ".venv" / "Scripts" / "python.exe"), *args],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode == 0:
        return True, "exit=0"
    tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
    return False, f"exit={proc.returncode} tail={' | '.join(tail)}"


def _run_pre_morning_chain(today: str) -> tuple[bool, str]:
    steps = [
        (["scripts/daily_collect.py", "--date", today], 1800, "daily_collect_supabase"),
        (["scripts/daily_collect.py", "--date", today, "--local"], 1800, "daily_collect_local"),
        (
            ["scripts/fetch_and_import_jma_tides.py", "--year-from", today[:4], "--year-to", today[:4], "--only-missing", "--timeout", "30"],
            1800,
            "tides_supabase",
        ),
        (
            ["scripts/fetch_and_import_jma_tides.py", "--db", str(ROOT / "data" / "boatrace.db"), "--year-from", today[:4], "--year-to", today[:4], "--only-missing", "--timeout", "30"],
            1800,
            "tides_local",
        ),
        (["scripts/cache_predictions.py", "--today", "--sync"], 1800, "cache_predictions"),
    ]
    traces: list[str] = []
    for args, timeout, label in steps:
        ok, detail = _run_py(args, timeout=timeout)
        traces.append(f"{label}:{detail}")
        if not ok:
            return False, " / ".join(traces)
    return True, " / ".join(traces)


def main() -> int:
    now = _now()
    today = date.today().isoformat()
    state = _today_state(today)
    need_heal, reason = _should_heal(state, now)
    print(f"[self-heal] state={state}")
    if not need_heal:
        print(f"[self-heal] skip: {reason}")
        return 0
    if _recent_attempt_exists(now):
        print(f"[self-heal] skip: attempted within {RETRY_MINUTES} min")
        return 0
    task_log.record(SELF_HEAL_TASK, "running", trigger="self_heal", detail=reason)
    preload_due = now.replace(hour=PRELOAD_HOUR, minute=PRELOAD_MINUTE, second=0, microsecond=0)
    morning_due = now.replace(hour=MORNING_DUE_HOUR, minute=MORNING_DUE_MINUTE, second=0, microsecond=0)
    if now < preload_due:
        ok, detail = True, "before preload window"
    elif now < morning_due:
        ok, detail = _run_pre_morning_chain(today)
    else:
        ok, detail = _run_bat(MORNING_BAT)
    task_log.record(
        SELF_HEAL_TASK,
        "success" if ok else "failure",
        trigger="self_heal",
        detail=f"{reason} / {detail}",
    )
    print(f"[self-heal] {'ok' if ok else 'ng'}: {reason} / {detail}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
