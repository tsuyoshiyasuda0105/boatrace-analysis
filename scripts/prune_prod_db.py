# -*- coding: utf-8 -*-
"""本番DB（Supabase）の古いデータを消して、無料枠（500MB）に収める。

PC（data/boatrace.db）が正本、Supabase は表示用の写し（2026-10-10 決定）。
消す前に必ず控え（data/prod_backup/backup_prod.py）と PC への取り込み
（merge_prod_into_local.py）を済ませること。

  # お試し: 表ごとに「消える行数」と「残る見込みの大きさ」を出すだけ
  python scripts/prune_prod_db.py
  # 消す（消したあと容量を詰める VACUUM FULL もする）
  python scripts/prune_prod_db.py --apply
  # 毎晩用（容量を詰めるのは省く）
  python scripts/prune_prod_db.py --apply --no-vacuum

残す期間の理由は data/prod_backup/audit_history_usage.md（朝の集計が読む期間）:
- 主要5表は 200 日: 事故率の作り直しが期の初めから最大184日を読むため。平均ST(370日)・
  コース別成績(365日)は約半年の計算になる（承知のうえ）。
- 払戻・予測・潮・タグ・スタート予想は 30 日（当日〜数日しか読まない）。
- 3連単オッズは日中の取得を止めたので全部（PCと控えに残っている）。
- ページの一時保存は 2 日（当日・翌日は作り直される。1日約15MB）。
- 事故率の集計は、今の期は新しい版3日分、前の期はそれぞれ最後の版だけ。
接続文字列は表示しない。
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]

# races を親に持つ表（外部キー・削除の連動なし）は races より先に消すこと。
RACE_ID_DAYS = {
    "race_parts": 200, "value_bets": 200,
    "race_entries": 200, "race_results": 200, "race_previews": 200,
    "race_payouts": 30, "predictions": 30, "derived_start_stats": 30, "race_tides": 30,
    "race_start_predictions": 30, "odds_exacta": 30, "odds_trio": 30, "odds_quinella": 30,
    "odds_trifecta": 0,
}
RACE_DATE_DAYS = {
    "race_original_exhibitions": 200,
    "race_program_tags": 30, "exhibition_ranks": 30,
    "races": 200,  # 親なので最後（子はすべて 200 日以下で先に消える）
}
PAGE_CACHE_DAYS = 2
SNAPSHOT_DAYS = 30  # racer_accident_external_snapshots.snapshot_date


def plans(days_override: int | None = None) -> list[tuple[str, str, tuple]]:
    """(表, 消す条件の WHERE, パラメータ) の並び。"""
    out = []
    for table, days in RACE_ID_DAYS.items():
        if days == 0:
            out.append((table, "TRUE", ()))
        else:
            out.append((table, "substr(race_id,1,8) < to_char(current_date - %s, 'YYYYMMDD')", (days,)))
    for table, days in RACE_DATE_DAYS.items():
        out.append((table, "race_date::text < to_char(current_date - %s, 'YYYY-MM-DD')", (days,)))
    out.append(("page_html_cache", "updated_at < extract(epoch from now() - make_interval(days => %s))",
                (PAGE_CACHE_DAYS,)))
    out.append(("racer_accident_external_snapshots",
                "snapshot_date::text < to_char(current_date - %s, 'YYYY-MM-DD')", (SNAPSHOT_DAYS,)))
    # 今の期は新しい period_end 3つ、前の期は最後の版だけ残す
    out.append(("racer_accident_period_stats", """
        (period_start, period_end) NOT IN (
          SELECT period_start, period_end FROM (
            SELECT period_start, period_end,
                   row_number() OVER (PARTITION BY period_start ORDER BY period_end DESC) AS rn,
                   period_start = (SELECT max(period_start) FROM racer_accident_period_stats) AS is_current
            FROM (SELECT DISTINCT period_start, period_end FROM racer_accident_period_stats) d
          ) x WHERE rn = 1 OR (is_current AND rn <= 3))""", ()))
    # スタート予想の子表（scenarios/boats/evaluations）は親を消すと一緒に消える
    # （ON DELETE CASCADE）ので、ここでは扱わない。
    # races は親なので、最後に回す（RACE_DATE_DAYS の races を末尾へ）。
    races = [x for x in out if x[0] == "races"]
    return [x for x in out if x[0] != "races"] + races


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--no-vacuum", action="store_true")
    args = ap.parse_args()
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith(("postgres://", "postgresql://")):
        print("DATABASE_URL が本番の Postgres を指していません")
        return 2
    with psycopg.connect(url, connect_timeout=30, autocommit=True) as pg, pg.cursor() as cur:
        cur.execute("SELECT pg_database_size(current_database())")
        before = cur.fetchone()[0]
        print(f"{'消します' if args.apply else 'お試し（消しません）'}／今の大きさ {before/1e6:.0f}MB", flush=True)
        est_after = before
        for table, where, params in plans():
            cur.execute("SELECT to_regclass(%s)", (f'public."{table}"',))
            if cur.fetchone()[0] is None:
                continue
            cur.execute(f'SELECT count(*) FROM public."{table}"')
            total = cur.fetchone()[0]
            cur.execute(f'SELECT count(*) FROM public."{table}" WHERE {where}', params)
            gone = cur.fetchone()[0]
            cur.execute("SELECT pg_total_relation_size(%s::regclass)", (f'public."{table}"',))
            size = cur.fetchone()[0]
            freed = size * gone / total if total else 0
            est_after -= freed
            line = f"  {table:<36} {total:>10,}行 → 消す {gone:>10,}行（約{freed/1e6:6.1f}MB）"
            if args.apply and gone:
                started = time.time()
                cur.execute(f'DELETE FROM public."{table}" WHERE {where}', params)
                if not args.no_vacuum:
                    cur.execute(f'VACUUM (FULL, ANALYZE) public."{table}"')
                line += f" 済み {time.time() - started:.0f}秒"
            print(line, flush=True)
        if args.apply:
            cur.execute("SELECT pg_database_size(current_database())")
            print(f"消した後の大きさ {cur.fetchone()[0]/1e6:.0f}MB（無料枠 500MB）")
        else:
            print(f"残る見込み 約{est_after/1e6:.0f}MB（無料枠 500MB・容量を詰めた後）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
