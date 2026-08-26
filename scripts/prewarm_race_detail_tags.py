"""Precompute race-detail display tags for every race on one date."""
from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
from datetime import date, datetime
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
os.environ.setdefault("BOATRACE_TASK_TRIGGER", "render-prewarm")

from src.db.connection import connect as db_connect  # noqa: E402
from src.web.app import (  # noqa: E402
    RACE_DETAIL_TAG_CACHE_VERSION,
    JST,
    _prefetch_race_detail_tag_inputs,
    _race_detail_tag_cache_key,
    _race_detail_tag_snapshot,
    _use_race_detail_prewarm_context,
)


def _entry_change_snapshot_written_at(target_date: str, *, conn) -> float:
    """進入変更スナップショットが最後に書かれた時刻 (epoch 秒, 無ければ 0)。"""
    try:
        row = conn.execute(
            """
            SELECT MAX(updated_at)
              FROM racer_entry_change_snapshots
             WHERE snapshot_date = ?
            """,
            (target_date,),
        ).fetchone()
    except Exception:
        return 0.0
    raw = row[0] if row else None
    if not raw:
        return 0.0
    try:
        return datetime.fromisoformat(str(raw)).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _missing_cached_race_ids(
    race_ids: list[str],
    *,
    conn=None,
    target_date: str | None = None,
) -> list[str]:
    """作り直しが要るレースを返す。

    「まだ無い」だけでなく「元データより古い」も作り直しの対象にする。
    2026-08-24 に判明: タグの生成は朝 5:50 に走るのに、進入変更スナップショット
    が書かれるのは 6:30。タグは常に 40 分前の状態で焼き付けられ、既に保存済み
    という理由で二度と作り直されないため、「進入注意 !」が 4 日連続で 1 件も
    表示されていなかった (該当選手は本日だけで 21 レースに乗っていた)。
    実行順に頼らず、元データが新しければ作り直す。
    """
    if not race_ids:
        return []
    keyed_ids = {_race_detail_tag_cache_key(race_id): race_id for race_id in race_ids}
    cache_keys = list(keyed_ids)
    fresh_enough: set[str] = set()
    owns_connection = conn is None
    if owns_connection:
        conn = db_connect()
    try:
        source_written_at = (
            _entry_change_snapshot_written_at(target_date, conn=conn)
            if target_date
            else 0.0
        )
        for start in range(0, len(cache_keys), 900):
            chunk = cache_keys[start : start + 900]
            placeholders = ",".join("?" for _ in chunk)
            rows = conn.execute(
                f"SELECT cache_key, updated_at FROM page_html_cache "
                f"WHERE cache_key IN ({placeholders})",
                tuple(chunk),
            ).fetchall()
            for row in rows:
                try:
                    written_at = float(row[1] or 0)
                except (TypeError, ValueError):
                    written_at = 0.0
                if written_at >= source_written_at:
                    fresh_enough.add(str(row[0]))
    finally:
        if owns_connection:
            conn.close()
    return [keyed_ids[key] for key in cache_keys if key not in fresh_enough]


def _entry_change_expectation(target_date: str, *, conn) -> int:
    """その日「進入注意」が付くはずのレース数 (元データから直接数える)。"""
    try:
        row = conn.execute(
            """
            SELECT COUNT(DISTINCT e.race_id)
              FROM race_entries e
              JOIN races r ON r.race_id = e.race_id
              JOIN racer_entry_change_snapshots s
                ON s.racer_number = e.racer_number
               AND s.snapshot_date = ?
             WHERE r.race_date = ?
               AND s.starts_count >= 100
               AND s.change_rate >= 0.20
               AND s.inner_change_rate >= 0.10
               AND s.inner_change_rate >= s.outer_change_rate
            """,
            (target_date, target_date),
        ).fetchone()
    except Exception:
        return 0
    return int(row[0] or 0) if row else 0


def _entry_change_tags_written(target_date: str, *, conn) -> int:
    prefix = f"race_detail_tags:{RACE_DETAIL_TAG_CACHE_VERSION}:{target_date.replace('-', '')}"
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM page_html_cache "
            "WHERE cache_key LIKE ? AND html LIKE ?",
            (prefix + "%", "%entry_change_tag%"),
        ).fetchone()
    except Exception:
        return -1
    return int(row[0] or 0) if row else 0


def prewarm(
    target_date: str,
    *,
    budget_sec: float | None = None,
    force: bool = False,
) -> dict[str, object]:
    if budget_sec is not None and budget_sec <= 0:
        raise ValueError("budget_sec must be positive")
    conn = db_connect()
    try:
        race_rows = conn.execute(
            """
            SELECT race_id
              FROM races
             WHERE race_date = ?
             ORDER BY stadium_number, race_number
            """,
            (target_date,),
        ).fetchall()
        race_ids = [str(row[0]) for row in race_rows]
        missing_ids = (
            list(race_ids)
            if force
            else _missing_cached_race_ids(
                race_ids, conn=conn, target_date=target_date
            )
        )
        summary: dict[str, object] = {
            "races": len(race_ids),
            "skipped_existing": len(race_ids) - len(missing_ids),
            "attempted": 0,
            "cached": 0,
            "failed": 0,
            "remaining": len(missing_ids),
            "budget_exhausted": False,
        }
        if not race_ids:
            print(f"[race-detail-tags] date={target_date} {summary}", flush=True)
            return summary

        print(
            f"[race-detail-tags] start date={target_date} races={len(race_ids)} "
            f"missing={len(missing_ids)} skipped_existing={summary['skipped_existing']} "
            f"budget_sec={budget_sec}",
            flush=True,
        )
        started = time.perf_counter()
        durations: list[float] = []
        # The prefetch and every per-race cache write borrow this one connection.
        with _use_race_detail_prewarm_context(conn, {}):
            prefetched = _prefetch_race_detail_tag_inputs(missing_ids, conn)
        context = _use_race_detail_prewarm_context(conn, prefetched)
        with context:
            for idx, race_id in enumerate(missing_ids, start=1):
                race_started = time.perf_counter()
                try:
                    payload = _race_detail_tag_snapshot(str(race_id), recompute=True)
                    if not isinstance(payload, dict) or not payload.get("boats"):
                        summary["failed"] = int(summary["failed"]) + 1
                        print(
                            f"[race-detail-tags] empty payload race_id={race_id}",
                            flush=True,
                        )
                    else:
                        # _race_detail_tag_snapshot writes through page_html_cache and
                        # commits before returning, so every completed race is durable.
                        summary["cached"] = int(summary["cached"]) + 1
                except Exception as exc:  # noqa: BLE001
                    summary["failed"] = int(summary["failed"]) + 1
                    print(
                        f"[race-detail-tags] failed race_id={race_id} "
                        f"error={type(exc).__name__}: {exc}",
                        flush=True,
                    )
                elapsed = time.perf_counter() - race_started
                durations.append(elapsed)
                summary["attempted"] = idx
                summary["remaining"] = len(missing_ids) - int(summary["cached"])
                print(
                    f"[race-detail-tags] race {idx}/{len(missing_ids)} race_id={race_id} "
                    f"elapsed={elapsed:.3f}s cached={summary['cached']} "
                    f"failed={summary['failed']} remaining={summary['remaining']}",
                    flush=True,
                )
                if budget_sec is not None and time.perf_counter() - started >= budget_sec:
                    summary["budget_exhausted"] = int(summary["remaining"]) > 0
                    break
        total = time.perf_counter() - started
        summary.update(
            {
                "elapsed_seconds": round(total, 3),
                "average_seconds": round(statistics.mean(durations), 3) if durations else 0.0,
                "median_seconds": round(statistics.median(durations), 3) if durations else 0.0,
                "min_seconds": round(min(durations), 3) if durations else 0.0,
                "max_seconds": round(max(durations), 3) if durations else 0.0,
            }
        )
        # 作った物が正しいかを、元データと突き合わせて自分で確かめる。
        # 2026-08-25 と 08-27 の朝、生成は「成功」を報告しながら進入注意を
        # 1 件も含まないタグを焼き、鮮度チェックがそれを完成品と見なしたため
        # 「!」が終日出なかった (どちらの日も本来 21 レース)。手で --force を
        # 打つまで直らない。数が合わなければ失敗として扱い、再試行に委ねる。
        expected = _entry_change_expectation(target_date, conn=conn)
        written = _entry_change_tags_written(target_date, conn=conn)
        summary["entry_change_expected"] = expected
        summary["entry_change_written"] = written
        if expected > 0 and written == 0:
            summary["failed"] = int(summary["failed"]) + 1
            summary["entry_change_missing"] = True
            print(
                f"[race-detail-tags] entry-change tags missing: "
                f"expected={expected} written={written}",
                flush=True,
            )
        print(f"[race-detail-tags] date={target_date} {summary}", flush=True)
        return summary
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--budget-sec", type=float)
    parser.add_argument(
        "--force",
        action="store_true",
        help="既存のタグも作り直す (壊れた内容が焼き付いた日の修復用)",
    )
    args = parser.parse_args()
    summary = prewarm(args.date, budget_sec=args.budget_sec, force=args.force)
    return 0 if int(summary["races"]) > 0 and int(summary["failed"]) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
