"""Render-safe odds scheduler entrypoint.

This keeps the existing odds scheduler logic, but narrows the active snapshot
labels for production so the daytime cron does less work:
  - normal races: T-5min
  - major races: T-1d / T-5min

P0-3: a pg_try_advisory_lock guard (same shape as render_maintenance_scheduler)
prevents overlapping runs when a slow pass crosses the next 5-minute tick.
A skipped tick records nothing (no fake success) and exits 0 so Render stays
healthy.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
import sys
from pathlib import Path
from typing import Iterator


REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
if __name__ == "__main__":
    os.environ.setdefault("BOATRACE_TASK_TRIGGER", "render-odds")

from scripts import odds_scheduler as base  # noqa: E402
from src.db.connection import connect as db_connect  # noqa: E402
from src.deploy_info import log_deploy_revision  # noqa: E402


LOCK_NAME = "boatrace-odds-scheduler-v1"

# tolerance は cron 間隔の半分 (2.5分) にする。
# render.yaml のオッズ cron は 5 分間隔 (*/5)。締切5分前を狙う T-5min の許容窓を
# ±2.5分にすると各レースの [close-7.5, close-2.5] の5分窓に必ず1回だけ tick が入り、
# 取りこぼしがなくなる (重複は (race_id, snapshot_label) dedup が防ぐ)。
# 旧値 0.5 (窓幅1分) は 5分間隔と噛み合わず大半を取り逃していた (2026-08-12 障害)。
RENDER_SNAPSHOT_RULES = [
    ("T-5min", 5, 2.5),
]


@contextmanager
def odds_lock() -> Iterator[bool]:
    """Postgres advisory lock。SQLite (ローカル) では常に取得扱い。

    取得失敗 = 前回の 5 分 tick がまだ実行中。呼び出し側はスキップし、
    success は記録しない (偽装成功パターンの再現禁止)。
    """
    conn = db_connect()
    locked = True
    is_postgres = getattr(conn, "_kind", "sqlite") == "postgres"
    try:
        if is_postgres:
            row = conn.execute(
                "SELECT pg_try_advisory_lock(hashtext(?))", (LOCK_NAME,)
            ).fetchone()
            locked = bool(row and row[0])
        yield locked
    finally:
        if is_postgres and locked:
            try:
                conn.execute("SELECT pg_advisory_unlock(hashtext(?))", (LOCK_NAME,))
            except Exception:  # noqa: BLE001
                pass
        conn.close()


def main() -> int:
    log_deploy_revision("boatrace-odds-cron")
    with odds_lock() as locked:
        if not locked:
            # 前回実行が継続中。何も実行せず、成功も記録しない。
            print("[odds-cron] skip: previous run still active", flush=True)
            return 0
        base.SNAPSHOT_RULES = list(RENDER_SNAPSHOT_RULES)
        base.BIG_SNAPSHOT_RULES = [("T-1d", 24 * 60, 5), *base.SNAPSHOT_RULES]
        base.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
