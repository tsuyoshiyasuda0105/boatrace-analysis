from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
JST = timezone(timedelta(hours=9))


def _run_local(args: list[str], *, allow_prod_sync: bool = False) -> bool:
    env = os.environ.copy()
    if not allow_prod_sync:
        env["DATABASE_URL"] = ""
    cmd = [sys.executable, *args]
    print("$ " + " ".join(args), flush=True)
    proc = subprocess.run(cmd, cwd=ROOT, env=env, check=False)
    print(f"exit={proc.returncode}", flush=True)
    return proc.returncode == 0


def _default_target_date(now: datetime | None = None) -> str:
    """準備対象日を返す。

    このバッチは「これから始まるレース日」を準備する。夕方〜深夜0時前に
    走れば翌日、深夜0時過ぎ (定時 01:00) に走れば「その日」が対象。
    素朴な today+1 だと 01:00 実行時に一日先 (未公開の番組表) を狙って
    毎晩失敗する (2026-08-14 の障害)。正午を境に切り替える。
    """
    current = now or datetime.now()
    base = current.date() if current.hour < 12 else current.date() + timedelta(days=1)
    return base.isoformat()


def _completed_date(now: datetime | None = None) -> str:
    current = now or datetime.now(JST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=JST)
    else:
        current = current.astimezone(JST)
    return (current.date() - timedelta(days=1)).isoformat()


def _run_kachisuji_daily(completed_date: str) -> bool:
    compact_date = completed_date.replace("-", "")
    delta_path = ROOT / "data" / f"kachisuji_delta_{compact_date}.db"
    if delta_path.exists():
        print(f"[kachisuji] reusing existing delta: {delta_path}", flush=True)
    else:
        refresh_ok = _run_local(
            [
                "scripts/refresh_kachisuji_daily.py",
                "--date",
                completed_date,
                "--emit-delta",
                str(delta_path),
            ]
        )
        if not refresh_ok:
            print("[kachisuji] refresh failed; upload skipped", flush=True)
            return False
    # Storage 版 (upload_kachisuji_delta.py) は SERVICE キー未配布で不稼働だった。
    # DATABASE_URL だけで動く Postgres 輸送に切替 (2026-08-20)。
    upload_ok = _run_local(
        ["scripts/upload_kachisuji_delta_pg.py", "--delta", str(delta_path)],
        allow_prod_sync=True,
    )
    print(f"[kachisuji] {'ok' if upload_ok else 'upload failed'}", flush=True)
    return upload_ok


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare next-day boatrace data on the PC-local SQLite source of truth.",
    )
    parser.add_argument(
        "--date",
        default=_default_target_date(),
        help="Target race date to prepare on local SQLite "
             "(default: today before noon, tomorrow from noon).",
    )
    parser.add_argument(
        "--sync-start",
        default=None,
        help="Optional sync range start for SQLite -> Supabase. Defaults to target date.",
    )
    parser.add_argument(
        "--sync-end",
        default=None,
        help="Optional sync range end for SQLite -> Supabase. Defaults to target date.",
    )
    parser.add_argument(
        "--skip-sync",
        action="store_true",
        help="Prepare local SQLite only and skip SQLite -> Supabase diff sync.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target_date = args.date
    sync_start = args.sync_start or target_date
    sync_end = args.sync_end or target_date
    today = datetime.now().date().isoformat()

    steps = [
        ["scripts/backfill_official.py", "--start", target_date, "--end", target_date, "--local"],
        ["scripts/daily_collect.py", "--date", target_date],
        ["scripts/fetch_and_import_jma_tides.py", "--db", str(ROOT / "data" / "boatrace.db"), "--year-from", target_date[:4], "--year-to", target_date[:4], "--only-missing", "--timeout", "30"],
        ["scripts/build_racer_entry_change_stats.py", "--date", target_date],
        ["scripts/rebuild_racer_accident_stats.py", "--local", "--from", today, "--to", target_date],
        ["scripts/cache_racer_accident_rank_snapshot.py", "--date", target_date, "--db-path", str(ROOT / "data" / "boatrace.db")],
        ["scripts/prewarm_race_detail_tags.py", "--date", target_date],
        ["scripts/cache_predictions.py", "--date", target_date],
        ["scripts/prewarm_strategy_pages.py", "--mode", "signals", "--date", target_date, "--full"],
        ["scripts/prewarm_strategy_pages.py", "--mode", "history", "--date", target_date],
        ["scripts/build_top_page_snapshot.py", "--date", target_date],
    ]

    ok = True
    for step in steps:
        ok &= _run_local(step)
        if not ok:
            return 1

    if not args.skip_sync:
        sync_tables = ",".join(
            [
                "races",
                "race_entries",
                "race_previews",
                "race_tides",
                "race_original_exhibitions",
                "predictions",
                "derived_start_stats",
                "racer_accident_point_rules",
                "racer_accident_events",
                "racer_accident_period_stats",
                "racer_accident_rank_snapshots",
            ]
        )
        sync_ok = _run_local(
            [
                "scripts/sync_to_supabase.py",
                "--start",
                sync_start,
                "--end",
                sync_end,
                "--tables",
                sync_tables,
            ],
            allow_prod_sync=True,
        )
        if not sync_ok:
            return 1

    # This transport is intentionally isolated from the established nightly
    # outcome. A failed upload remains visible in logs and can be retried by
    # rerunning the task; the dated local delta is deliberately retained.
    _run_kachisuji_daily(_completed_date())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
