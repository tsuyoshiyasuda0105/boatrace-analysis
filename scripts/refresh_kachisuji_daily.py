"""Daily incremental refresh of the kachisuji backtest dataset.

For a range of *completed* race days this:

1. builds leakage-safe as-of rows into ``data/kachisuji_search.db``
   (``asof_builder.build_features`` -- idempotent, skips race_ids already
   present).  Wind direction is classified here by the course-relative
   ``relative_wind_direction`` and needs no extra step.
2. appends the new rows into the served slim DB (``data/kachisuji_slim.db``)
   with ``INSERT OR IGNORE`` so a full 500MB+ re-export is never needed.
3. optionally writes a tiny delta DB (only the new as-of rows + any new
   racers) for shipping to production.

Because as-of rows also carry the ACTUAL race result and the ACTUAL race-day
wind, only fully-finished days should be built -- the default target is
yesterday (JST-naive local clock).  The 365-day racer aggregates still use
only ``[asof_date-364, asof_date)``; no future information leaks in.

Two opt-in modes exist for the same-day matching flow (2026-09-08):

* ``--forward`` builds a day whose results are not in yet.  The rows carry
  the prior-day columns (class, rates, ST) and leave result/payout and the
  same-day observations NULL, which the matcher reports as "pending".
* ``--rebuild`` deletes the date range first and builds it again, so the
  forward rows of yesterday are replaced by the completed rows tonight.

Both modes write the slim DB with INSERT OR REPLACE instead of OR IGNORE,
and the caller must name the delta ``backfill_*.db`` so production replaces
too (``delta_transport._delta_wants_replace``).

    python scripts/refresh_kachisuji_daily.py                 # yesterday
    python scripts/refresh_kachisuji_daily.py --date 2026-08-18
    python scripts/refresh_kachisuji_daily.py --date-from 2026-08-10 --date-to 2026-08-18
    python scripts/refresh_kachisuji_daily.py --date 2026-08-18 --emit-delta data/kachisuji_delta_20260818.db
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from src.db.connection import connect  # noqa: E402
from src.features.asof_builder import build_features  # noqa: E402

SEARCH_DB = ROOT / "data" / "kachisuji_search.db"
SLIM_DB = ROOT / "data" / "kachisuji_slim.db"
SLIM_TABLES = ("asof_race_features", "racers")


def _readonly_uri(path: Path) -> str:
    return path.resolve().as_uri() + "?mode=ro"


def _rows_in_range(db: Path, date_from: str, date_to: str) -> int:
    if not Path(db).is_file():
        return 0
    conn = sqlite3.connect(_readonly_uri(Path(db)), uri=True)
    try:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM asof_race_features WHERE race_date BETWEEN ? AND ?",
                (date_from, date_to),
            ).fetchone()[0]
        )
    finally:
        conn.close()


def _append_to_slim(
    search_db: Path, slim_db: Path, date_from: str, date_to: str, *, replace: bool = False
) -> dict[str, int]:
    """Copy rows for the date range into the served slim DB, idempotently.

    ``replace`` swaps OR IGNORE for OR REPLACE so a day already present as
    forward rows is overwritten by its completed rows.  Ordinary nightly
    appends keep OR IGNORE.
    """
    # f-string で SQL を組まない (tests/test_source_regression.py の監査対象)。
    insert_sql = (
        "INSERT OR REPLACE INTO main.asof_race_features "
        "SELECT * FROM src.asof_race_features WHERE race_date BETWEEN ? AND ?"
        if replace else
        "INSERT OR IGNORE INTO main.asof_race_features "
        "SELECT * FROM src.asof_race_features WHERE race_date BETWEEN ? AND ?"
    )
    # ``uri=True`` on the main connection is required for SQLite to honor the
    # ``mode=ro`` URI on the subsequently attached source (notably on Windows).
    conn = sqlite3.connect(slim_db, uri=True)
    try:
        conn.execute("ATTACH DATABASE ? AS src", (_readonly_uri(search_db),))
        before_asof = conn.execute("SELECT COUNT(*) FROM asof_race_features").fetchone()[0]
        before_racers = conn.execute("SELECT COUNT(*) FROM racers").fetchone()[0]
        conn.execute(insert_sql, (date_from, date_to))
        # New racers can debut on any day; a full-table OR IGNORE is cheap.
        conn.execute("INSERT OR IGNORE INTO main.racers SELECT * FROM src.racers")
        conn.commit()
        after_asof = conn.execute("SELECT COUNT(*) FROM asof_race_features").fetchone()[0]
        after_racers = conn.execute("SELECT COUNT(*) FROM racers").fetchone()[0]
        in_range = conn.execute(
            "SELECT COUNT(*) FROM main.asof_race_features WHERE race_date BETWEEN ? AND ?",
            (date_from, date_to),
        ).fetchone()[0]
        conn.execute("DETACH DATABASE src")
    finally:
        conn.close()
    return {
        "asof_added": after_asof - before_asof,
        "racers_added": after_racers - before_racers,
        "asof_in_range": int(in_range),
    }


def _emit_delta(search_db: Path, delta_db: Path, date_from: str, date_to: str) -> int:
    if delta_db.exists():
        raise FileExistsError(f"delta already exists: {delta_db}")
    conn = sqlite3.connect(str(delta_db), uri=True)
    try:
        conn.execute("ATTACH DATABASE ? AS src", (_readonly_uri(search_db),))
        for table in SLIM_TABLES:
            create_sql = conn.execute(
                "SELECT sql FROM src.sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()[0]
            conn.execute(create_sql)
        conn.execute(
            "INSERT INTO asof_race_features "
            "SELECT * FROM src.asof_race_features WHERE race_date BETWEEN ? AND ?",
            (date_from, date_to),
        )
        conn.execute("INSERT INTO racers SELECT * FROM src.racers")
        count = conn.execute("SELECT COUNT(*) FROM asof_race_features").fetchone()[0]
        conn.commit()
        conn.execute("DETACH DATABASE src")
    finally:
        conn.close()
    return int(count)


def _wind_distribution(slim_db: Path, date_from: str, date_to: str) -> list[tuple[str, int]]:
    conn = sqlite3.connect(_readonly_uri(slim_db), uri=True)
    try:
        return conn.execute(
            "SELECT COALESCE(wind_dir,'(none)'), COUNT(*) FROM asof_race_features "
            "WHERE race_date BETWEEN ? AND ? GROUP BY 1 ORDER BY 2 DESC",
            (date_from, date_to),
        ).fetchall()
    finally:
        conn.close()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    parser.add_argument("--date", help="Single completed race day (YYYY-MM-DD).")
    parser.add_argument("--date-from", help="Range start (with --date-to).")
    parser.add_argument("--date-to", help="Range end (with --date-from).")
    parser.add_argument("--emit-delta", type=Path, help="Also write a small delta DB for production.")
    parser.add_argument("--skip-slim", action="store_true", help="Build search DB only; do not touch the slim DB.")
    parser.add_argument(
        "--forward", action="store_true",
        help="Build a day whose results are not in yet (same-day matching). Slim rows are replaced.",
    )
    parser.add_argument(
        "--rebuild", action="store_true",
        help="Delete the date range first and build it again (replaces forward rows). Slim rows are replaced.",
    )
    parser.set_defaults(_yesterday=yesterday)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.date_from or args.date_to:
        if not (args.date_from and args.date_to):
            print("error: --date-from requires --date-to", file=sys.stderr)
            return 2
        date_from, date_to = args.date_from, args.date_to
    else:
        target = args.date or args._yesterday
        date_from = date_to = target

    replace = bool(args.forward or args.rebuild)
    mode = "forward" if args.forward else ("rebuild" if args.rebuild else "completed")
    print(f"[info] target range: {date_from} .. {date_to} mode={mode}")

    before_rows = _rows_in_range(SEARCH_DB, date_from, date_to) if args.rebuild else 0
    source = connect(str(config.DB_PATH))
    try:
        source.execute("PRAGMA query_only=ON")
        result = build_features(source, SEARCH_DB, date_from, date_to, rebuild=args.rebuild)
    finally:
        source.close()
    print("[build] " + " ".join(f"{k}={v}" for k, v in result.items()))
    if args.rebuild:
        after_rows = _rows_in_range(SEARCH_DB, date_from, date_to)
        if after_rows < before_rows:
            # 元データが一時的に欠けたまま作り直すと、良い行を消して少ない行で
            # 置き換えてしまう。ここで止めれば slim と本番へは伝播しない
            # (検索DBはいつでも作り直せる)。
            print(
                f"error: rebuild produced fewer rows ({before_rows} -> {after_rows}). "
                "Source data looks incomplete; slim and delta were skipped.",
                file=sys.stderr,
            )
            return 4
    if args.forward and result.get("inserted", 0) == 0 and result.get("selected", 0) == 0:
        # 番組表がまだ無い日を forward で作ろうとした。空の差分を本番へ送っても
        # 意味が無いので、ここで分かるように失敗させる。
        print("error: no races found for the forward day (program not loaded yet?)", file=sys.stderr)
        return 3

    if not args.skip_slim:
        added = _append_to_slim(SEARCH_DB, SLIM_DB, date_from, date_to, replace=replace)
        print(
            f"[slim] asof_added={added['asof_added']} racers_added={added['racers_added']} "
            f"asof_in_range={added['asof_in_range']} replace={replace}"
        )
        print("[wind] distribution for range in slim DB:")
        for label, count in _wind_distribution(SLIM_DB, date_from, date_to):
            print(f"    {label}: {count}")

    if args.emit_delta:
        try:
            count = _emit_delta(SEARCH_DB, args.emit_delta, date_from, date_to)
        except FileExistsError as exc:
            # 夜間バッチは存在チェックをしてから呼ぶので通常は来ない。手動で同じ名前を
            # 指定したときに traceback ではなく理由を返す (古い差分を送らないための保護)。
            print(f"error: {exc}. Remove it or choose another name.", file=sys.stderr)
            return 2
        print(f"[delta] wrote {args.emit_delta} ({count} asof rows)")

    print("[done]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
