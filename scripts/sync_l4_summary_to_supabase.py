"""src (生データ) → Supabase l4_daily_summary に L4 [A1] ROI 集計のみを同期。

Supabase Free プランの 500MB 制限内に収まるよう、生データ
(races / race_entries / race_payouts / race_results) ではなく
日別の L4 [A1] ROI 集計値のみを Supabase に置く。

L4 戦略条件 (=2026-05 時点):
  - 1号艇 A1 (class_number = 1)
  - SG/G1/G2/G3 (grade_number IN 1,2,3,4、一般戦5除外)
  - B除外会場 (2,4,7,8,10,19,21,24) 除外
  - 判定優先: T-X 1-2-3 オッズ × 100 が 500-1000円帯
            (T-X 無し時のフォールバック: race_payouts MIN が 500-1000円帯)

データソース選択 (--src オプション):
  - --src supabase (default): Supabase の生データから計算 (odds_trifecta 含む)
  - --src local: ローカル SQLite から計算 (2022-2024 等、Supabase に無い期間)

使い方:
    python scripts/sync_l4_summary_to_supabase.py --start 2025-01-01 --end 2026-12-31
    python scripts/sync_l4_summary_to_supabase.py --src local --start 2022-01-01 --end 2024-12-31
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import config
from src.db.connection import connect as db_connect

EXCLUDE_B = (2, 4, 7, 8, 10, 19, 21, 24)


def compute_summary(src, start: str, end: str) -> list[dict]:
    """src (SQLite または psycopg) から日別 L4 [A1] 集計を計算"""
    placeholders = ",".join("?" for _ in EXCLUDE_B)
    sql = f"""
        SELECT r.race_date,
               COUNT(DISTINCT r.race_id) AS n_total_in_join
        FROM races r
        WHERE r.race_date BETWEEN ? AND ?
        GROUP BY r.race_date
    """
    cur = src.execute(sql, (start, end))
    n_total_by_date = {row[0]: row[1] for row in cur.fetchall()}

    # L4 該当 + 各 bet_type の hit
    # 判定優先順位: T-X 1-2-3 オッズ × 100 が 500-1000 (朝賭けた時点の本命)
    #              フォールバック: race_payouts MIN 500-1000 (過去日、1-2-3 hit ケースのみ正確)
    # 雨除外: race_previews.weather_number=3 (雨) のレースは ROI 100% で break-even のため除外
    sql = f"""
        SELECT r.race_date,
               r.race_id,
               pp.min_pay AS fav_pay,
               oo.min_odds AS fav_odds,
               res1.boat_number AS w1,
               res2.boat_number AS w2,
               res3.boat_number AS w3,
               pw.payout AS win_pay,
               pe.payout AS exa_pay,
               pt.payout AS tri_pay
        FROM races r
        JOIN race_entries e ON r.race_id=e.race_id AND e.boat_number=1
        LEFT JOIN race_previews pv ON pv.race_id=r.race_id AND pv.boat_number=1
        LEFT JOIN (SELECT race_id, MIN(payout) AS min_pay FROM race_payouts WHERE bet_type='trifecta' GROUP BY race_id) pp ON pp.race_id=r.race_id
        LEFT JOIN (SELECT race_id, MIN(odds) AS min_odds FROM odds_trifecta
                   WHERE combination='1-2-3' AND snapshot_label IN ('T-1min','T-2min','T-3min','T-4min','T-5min','T-15min','final')
                   GROUP BY race_id) oo ON oo.race_id=r.race_id
        LEFT JOIN race_results res1 ON res1.race_id=r.race_id AND res1.finishing_position=1
        LEFT JOIN race_results res2 ON res2.race_id=r.race_id AND res2.finishing_position=2
        LEFT JOIN race_results res3 ON res3.race_id=r.race_id AND res3.finishing_position=3
        LEFT JOIN race_payouts pw ON pw.race_id=r.race_id AND pw.bet_type='win' AND pw.combination='1'
        LEFT JOIN race_payouts pe ON pe.race_id=r.race_id AND pe.bet_type='exacta' AND pe.combination='1-2'
        LEFT JOIN race_payouts pt ON pt.race_id=r.race_id AND pt.bet_type='trifecta' AND pt.combination='1-2-3'
        WHERE r.race_date BETWEEN ? AND ?
          AND e.class_number = 1
          AND r.stadium_number NOT IN ({placeholders})
          AND r.race_grade_number IN (1, 2, 3, 4)
          AND (pv.weather_number IS NULL OR pv.weather_number != 3)
          AND (
              (oo.min_odds IS NOT NULL AND oo.min_odds >= 5 AND oo.min_odds < 10)
              OR
              (oo.min_odds IS NULL AND pp.min_pay BETWEEN 500 AND 999)
          )
    """
    cur = src.execute(sql, (start, end, *EXCLUDE_B))

    by_date: dict[str, dict] = {}
    for row in cur.fetchall():
        rdate, rid, fav_pay, fav_odds, w1, w2, w3, wp, ep, tp = row
        d = by_date.setdefault(rdate, {
            "date": rdate,
            "n_total": n_total_by_date.get(rdate, 0),
            "n_l4": 0,
            "win_bets": 0, "win_hits": 0, "win_pay": 0,
            "exa_bets": 0, "exa_hits": 0, "exa_pay": 0,
            "tri_bets": 0, "tri_hits": 0, "tri_pay": 0,
        })
        d["n_l4"] += 1
        # 確定済 (w1/w2/w3 揃ってる) のみ bets/hits/pay を加算
        if w1 is not None and w2 is not None and w3 is not None:
            d["win_bets"] += 1
            d["exa_bets"] += 1
            d["tri_bets"] += 1
            if w1 == 1:
                d["win_hits"] += 1
                d["win_pay"] += (wp or 0)
            if w1 == 1 and w2 == 2:
                d["exa_hits"] += 1
                d["exa_pay"] += (ep or 0)
            if w1 == 1 and w2 == 2 and w3 == 3:
                d["tri_hits"] += 1
                d["tri_pay"] += (tp or 0)
    return list(by_date.values())


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", type=str, default="2022-01-01")
    p.add_argument("--end", type=str, default=datetime.now().strftime("%Y-%m-%d"))
    p.add_argument("--src", choices=["supabase", "local"], default="supabase",
                   help="集計データ源 (default=supabase)")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    db_url = os.getenv("DATABASE_URL", "").strip()
    if not db_url or not db_url.startswith(("postgres://", "postgresql://")):
        print("ERROR: DATABASE_URL が Supabase URL に設定されていません (.env 確認)")
        sys.exit(1)

    print(f"=== L4 [A1] 集計のみ同期: {args.start} ~ {args.end} (src={args.src}) ===")
    print()

    # データ源
    if args.src == "supabase":
        # Supabase から計算 (DATABASE_URL を尊重)
        src = db_connect()
    else:
        # ローカル SQLite から計算 (2022-2024 等、Supabase に無い期間)
        src = sqlite3.connect(config.DB_PATH)
    summaries = compute_summary(src, args.start, args.end)
    try:
        src.close()
    except Exception:
        pass
    print(f"  集計対象日数: {len(summaries)}")
    n_l4_total = sum(s["n_l4"] for s in summaries)
    tri_pay_total = sum(s["tri_pay"] for s in summaries)
    tri_bets_total = sum(s["tri_bets"] for s in summaries)
    if tri_bets_total:
        print(f"  L4 通算: {n_l4_total} 件, 3連単 1-2-3 損益 {tri_pay_total - 100*tri_bets_total:+,}円")

    # Supabase に upsert
    dst = db_connect()
    now_iso = datetime.now().isoformat(timespec="seconds")
    n_upsert = 0
    BATCH = 500
    batch = []
    for s in summaries:
        batch.append((
            s["date"], s["n_total"], s["n_l4"],
            s["win_bets"], s["win_hits"], s["win_pay"],
            s["exa_bets"], s["exa_hits"], s["exa_pay"],
            s["tri_bets"], s["tri_hits"], s["tri_pay"],
            now_iso,
        ))
        if len(batch) >= BATCH:
            dst.executemany("""
                INSERT OR REPLACE INTO l4_daily_summary
                  (date, n_total, n_l4,
                   win_bets, win_hits, win_pay,
                   exa_bets, exa_hits, exa_pay,
                   tri_bets, tri_hits, tri_pay,
                   updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, batch)
            n_upsert += len(batch)
            batch.clear()
            if args.verbose:
                print(f"  upserted {n_upsert} rows")
    if batch:
        dst.executemany("""
            INSERT OR REPLACE INTO l4_daily_summary
              (date, n_total, n_l4,
               win_bets, win_hits, win_pay,
               exa_bets, exa_hits, exa_pay,
               tri_bets, tri_hits, tri_pay,
               updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, batch)
        n_upsert += len(batch)
    dst.commit()
    dst.close()
    print(f"\n  Supabase へ {n_upsert} 日分の集計を upsert 完了")


if __name__ == "__main__":
    main()
