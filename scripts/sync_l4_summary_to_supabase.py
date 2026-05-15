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
    # サブカテゴリ集計: 1c80 / L4 PRO / SG/G1/G2 を 1 クエリで取得
    sql = f"""
        SELECT r.race_date,
               r.race_id,
               r.race_grade_number,
               e.racer_number, e.avg_start_timing, e.age,
               pv.start_timing_exhibition,
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

    # 選手の過去 180 日 1コース 1着率を一括計算 (1c80 判定用)
    # racer × race_date → past 180 days winrate
    import datetime as _dt
    course1_hist = {}  # racer_number -> [(date, is_1st)]
    try:
        hist_cur = src.execute("""
            SELECT e.racer_number, r.race_date, res.finishing_position
            FROM race_entries e
            JOIN races r ON e.race_id=r.race_id
            JOIN race_results res ON res.race_id=e.race_id AND res.boat_number=1
            WHERE e.boat_number=1 AND res.finishing_position IS NOT NULL
            ORDER BY e.racer_number, r.race_date
        """)
        for racer, rd, pos in hist_cur.fetchall():
            course1_hist.setdefault(racer, []).append((str(rd), 1 if pos == 1 else 0))
    except Exception as e:
        print(f"  course1 history skip: {e}")

    def _is_1c80(racer, race_date):
        rh = course1_hist.get(racer, [])
        if not rh: return False
        try:
            rd = _dt.date.fromisoformat(str(race_date))
            cutoff = (rd - _dt.timedelta(days=180)).isoformat()
            past = [w for d, w in rh if cutoff <= d < str(race_date)]
        except Exception:
            return False
        if len(past) < 20: return False
        return (sum(past) / len(past)) >= 0.80

    def _is_l4_pro(avg_st, age, ex_st):
        try:
            if avg_st is None or age is None: return False
            ast, a = float(avg_st), int(age)
            if ast >= 0.16 or not (30 <= a <= 49): return False
            if ex_st is None: return True
            return float(ex_st) < 0.18
        except Exception:
            return False

    by_date: dict[str, dict] = {}
    for row in cur.fetchall():
        (rdate, rid, grade, racer, avg_st, age, ex_st,
         fav_pay, fav_odds, w1, w2, w3, wp, ep, tp) = row
        d = by_date.setdefault(rdate, {
            "date": rdate,
            "n_total": n_total_by_date.get(rdate, 0),
            "n_l4": 0,
            "win_bets": 0, "win_hits": 0, "win_pay": 0,
            "exa_bets": 0, "exa_hits": 0, "exa_pay": 0,
            "tri_bets": 0, "tri_hits": 0, "tri_pay": 0,
            # サブカテゴリ集計 (3 連単 1-2-3 のみ)
            "c80_bets": 0, "c80_hits": 0, "c80_pay": 0,
            "pro_bets": 0, "pro_hits": 0, "pro_pay": 0,
            "sgg12_bets": 0, "sgg12_hits": 0, "sgg12_pay": 0,
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
            tri_hit = (w1 == 1 and w2 == 2 and w3 == 3)
            if tri_hit:
                d["tri_hits"] += 1
                d["tri_pay"] += (tp or 0)
            # ▼ サブカテゴリ判定 (確定済のみ集計)
            # 1c80 (1コース 1着率 80%+)
            if _is_1c80(racer, rdate):
                d["c80_bets"] += 1
                if tri_hit:
                    d["c80_hits"] += 1
                    d["c80_pay"] += (tp or 0)
            # L4 PRO
            if _is_l4_pro(avg_st, age, ex_st):
                d["pro_bets"] += 1
                if tri_hit:
                    d["pro_hits"] += 1
                    d["pro_pay"] += (tp or 0)
            # SG/G1/G2 (高グレード)
            if grade in (1, 2, 3):
                d["sgg12_bets"] += 1
                if tri_hit:
                    d["sgg12_hits"] += 1
                    d["sgg12_pay"] += (tp or 0)
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
    UPSERT_SQL = """
        INSERT OR REPLACE INTO l4_daily_summary
          (date, n_total, n_l4,
           win_bets, win_hits, win_pay,
           exa_bets, exa_hits, exa_pay,
           tri_bets, tri_hits, tri_pay,
           c80_bets, c80_hits, c80_pay,
           pro_bets, pro_hits, pro_pay,
           sgg12_bets, sgg12_hits, sgg12_pay,
           updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    for s in summaries:
        batch.append((
            s["date"], s["n_total"], s["n_l4"],
            s["win_bets"], s["win_hits"], s["win_pay"],
            s["exa_bets"], s["exa_hits"], s["exa_pay"],
            s["tri_bets"], s["tri_hits"], s["tri_pay"],
            s.get("c80_bets",0), s.get("c80_hits",0), s.get("c80_pay",0),
            s.get("pro_bets",0), s.get("pro_hits",0), s.get("pro_pay",0),
            s.get("sgg12_bets",0), s.get("sgg12_hits",0), s.get("sgg12_pay",0),
            now_iso,
        ))
        if len(batch) >= BATCH:
            dst.executemany(UPSERT_SQL, batch)
            n_upsert += len(batch)
            batch.clear()
            if args.verbose:
                print(f"  upserted {n_upsert} rows")
    if batch:
        dst.executemany(UPSERT_SQL, batch)
        n_upsert += len(batch)
    dst.commit()
    dst.close()
    print(f"\n  Supabase へ {n_upsert} 日分の集計を upsert 完了")


if __name__ == "__main__":
    main()
