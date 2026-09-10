from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
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


def _kachisuji_history_steps(
    completed_date: str, *, include_racers: bool = True
) -> list[list[str]]:
    """完成した日の履歴 (出走・事故・ST・選手名) を検索DBへ進める手順。

    決まり手率や平均STは検索DB内の履歴テーブルから計算するのに、これを
    毎晩進める処理が無く 2026-08-16 で止まっていた。K 成績ファイルを
    その日ぶんだけ取り直してから (早朝に空ファイルを掴む事故があるため
    --skip-existing は付けない)、日単位で置き換える。
    """
    steps = [
        ["scripts/backfill_official.py", "--start", completed_date, "--end", completed_date,
         "--local", "--targets", "k"],
        ["scripts/restore_accident_history.py", "--from", completed_date, "--to", completed_date],
        ["scripts/restore_start_timing.py", "--from", completed_date, "--to", completed_date],
    ]
    # 選手名の同期は日付に関係ない。穴埋めで日数ぶん繰り返す意味がないので、
    # 完成日の手順にだけ付ける。
    if include_racers:
        steps.append(["scripts/sync_kachisuji_racers.py"])
    return steps


# さかのぼって埋める日数と、一晩に埋める上限。上限は夜間バッチが長引いて
# 朝の取り込みに食い込まないようにするため。埋め残しは翌晩に持ち越す。
HISTORY_LOOKBACK_DAYS = 14
HISTORY_CATCHUP_LIMIT = 5


def _dates_with_races(db: Path, date_from: str, date_to: str) -> set[str]:
    import sqlite3

    if not db.is_file():
        return set()
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30)
    try:
        return {
            str(row[0])
            for row in conn.execute(
                "SELECT DISTINCT race_date FROM races WHERE race_date BETWEEN ? AND ?",
                (date_from, date_to),
            )
        }
    finally:
        conn.close()


def _dates_with_history(db: Path, date_from: str, date_to: str) -> set[str]:
    import sqlite3

    if not db.is_file():
        return set()
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30)
    try:
        return {
            str(row[0])
            for row in conn.execute(
                "SELECT DISTINCT race_date FROM start_timing_events "
                "WHERE race_date BETWEEN ? AND ?",
                (date_from, date_to),
            )
        }
    except sqlite3.OperationalError:
        # 履歴テーブルがまだ無い DB。復元スクリプトが作るので、
        # 「全部が穴」として扱えばそのまま埋まる。夜間を落とさない。
        return set()
    finally:
        conn.close()


def _dates_missing_results(db: Path, dates: set[str]) -> set[str]:
    """レースがあったのに、着順に決まり手が1件も無い日を返す。

    着順の行そのものは別経路 (Open API) でも作られるので、行があるだけでは
    K 成績ファイルが取り込まれたとは言えない。決まり手と備考 (事故) は
    K からしか入らない。2025-07〜2026-04 の10か月は、進入データはあるのに
    決まり手が0件で、1号艇の逃げ率が本来の52%から9%まで落ちていた
    (2026-09-10 発覚)。進入データの有無だけを見る穴探しでは見つからない。
    """
    import sqlite3

    if not dates or not db.is_file():
        return set()
    lo = min(dates).replace("-", "")
    hi = max(dates).replace("-", "") + "~"
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30)
    try:
        have = {
            f"{key[:4]}-{key[4:6]}-{key[6:8]}"
            for key, wins in conn.execute(
                "SELECT substr(race_id, 1, 8), "
                "SUM(kimarite IS NOT NULL AND kimarite <> '') "
                "FROM race_results WHERE race_id BETWEEN ? AND ? GROUP BY 1",
                (lo, hi),
            )
            if wins
        }
    except sqlite3.OperationalError:
        # 着順の表が無い DB では判断できない。穴扱いにはしない。
        return set()
    finally:
        conn.close()
    return dates - have


def history_gap_days(
    completed_date: str,
    *,
    source_db: Path | None = None,
    search_db: Path | None = None,
    lookback: int = HISTORY_LOOKBACK_DAYS,
    limit: int = HISTORY_CATCHUP_LIMIT,
) -> list[str]:
    """履歴が入っていない日を古い順に返す (完成日そのものは含めない)。

    夜間は「昨日」ぶんしか進めないので、01:00 の時点で K 成績ファイルが
    まだ公開されていないと、その日の履歴は誰も取りに戻らず永久に欠ける。
    2026-07-22〜09-07 に 30 日ぶん欠け、平均ST・決まり手率・事故率が薄い
    履歴で計算されていた (2026-09-10 に手作業で復旧)。

    レースがあったのに履歴が無い日を毎晩さかのぼって拾う。
    """
    end = date.fromisoformat(completed_date) - timedelta(days=1)
    start = date.fromisoformat(completed_date) - timedelta(days=lookback)
    if start > end:
        return []
    source = source_db or (ROOT / "data" / "boatrace.db")
    search = search_db or (ROOT / "data" / "kachisuji_search.db")
    raced = _dates_with_races(source, start.isoformat(), end.isoformat())
    have = _dates_with_history(search, start.isoformat(), end.isoformat())
    no_results = _dates_missing_results(source, raced)
    return sorted((raced - have) | no_results)[:limit]


def _kachisuji_delta_path(kind: str, day: str) -> Path:
    """差分ファイル名。``backfill_`` で始めると本番側が既存行を置き換える。

    day  = 完成した日 (結果つき)。前夜に forward で入れた同じ race_id を上書きする。
    fwd  = これから走る日 (結果なし)。当日の合致レース表示に使う。
    """
    compact = day.replace("-", "")
    return ROOT / "data" / f"backfill_{kind}_{compact}.db"


def _refresh_and_upload(day: str, delta_path: Path, extra_args: list[str]) -> bool:
    if delta_path.exists():
        print(f"[kachisuji] reusing existing delta: {delta_path}", flush=True)
    else:
        refresh_ok = _run_local(
            ["scripts/refresh_kachisuji_daily.py", "--date", day,
             "--emit-delta", str(delta_path), *extra_args]
        )
        if not refresh_ok:
            print(f"[kachisuji] refresh failed for {day}; upload skipped", flush=True)
            return False
    # Storage 版 (upload_kachisuji_delta.py) は SERVICE キー未配布で不稼働だった。
    # DATABASE_URL だけで動く Postgres 輸送に切替 (2026-08-20)。
    upload_ok = _run_local(
        ["scripts/upload_kachisuji_delta_pg.py", "--delta", str(delta_path)],
        allow_prod_sync=True,
    )
    print(f"[kachisuji] {day} {'ok' if upload_ok else 'upload failed'}", flush=True)
    return upload_ok


def _run_kachisuji_daily(completed_date: str, forward_date: str | None = None) -> bool:
    """検索DBを進めて本番へ差分を送る。順番に意味がある。

    1. 履歴テーブルを完成日ぶん進める (決まり手率・ST の材料)
    2. 完成日を rebuild で作り直す (前夜の forward 行を結果つきに置き換える)
    3. これから走る日を forward で作る (当日の合致レース表示用)
    履歴が失敗しても 2, 3 は続ける (率が一日ぶん古いだけで照合は動く)。
    """
    # 取りこぼした日を先に埋めてから完成日を進める。古い日から順に入れないと
    # 365 日窓の集計が歯抜けのまま完成日を計算してしまう。
    for gap_day in history_gap_days(completed_date):
        print(f"[kachisuji] filling missing history: {gap_day}", flush=True)
        for step in _kachisuji_history_steps(gap_day, include_racers=False):
            if not _run_local(step):
                print(f"[kachisuji] catch-up step failed (continuing): {' '.join(step)}",
                      flush=True)
    for step in _kachisuji_history_steps(completed_date):
        if not _run_local(step):
            print(f"[kachisuji] history step failed (continuing): {' '.join(step)}", flush=True)
    ok = _refresh_and_upload(
        completed_date, _kachisuji_delta_path("day", completed_date), ["--rebuild"]
    )
    if forward_date and forward_date > completed_date:
        ok &= _refresh_and_upload(
            forward_date, _kachisuji_delta_path("fwd", forward_date), ["--forward"]
        )
    return ok


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
    _run_kachisuji_daily(_completed_date(), target_date)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
