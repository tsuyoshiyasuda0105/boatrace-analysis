"""
ペーパートレード (仮想ベット) 記録・集計

T-5 オッズが取れた段階で、戦略のシグナルに従って「仮想的にベット」を記録。
実お金を使わず、戦略の Live 検証ができる。

使い方:
  python scripts/paper_trade.py record --date 2026-05-12  # 当日のシグナル記録
  python scripts/paper_trade.py settle --date 2026-05-11  # 前日結果と照合
  python scripts/paper_trade.py report                    # 累積成績
"""
import argparse
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DB = "data/boatrace.db"
JST = ZoneInfo("Asia/Tokyo")


def _now_jst() -> datetime:
    return datetime.now(JST)


def _now_jst_iso() -> str:
    return _now_jst().replace(tzinfo=None).isoformat(timespec="seconds")


def _today_jst_iso() -> str:
    return _now_jst().date().isoformat()


def ensure_schema(conn):
    """ペーパートレード記録テーブル"""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS paper_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            race_id TEXT NOT NULL,
            race_date TEXT NOT NULL,
            strategy TEXT NOT NULL,
            bet_type TEXT NOT NULL,
            combination TEXT NOT NULL,
            bet_amount INTEGER NOT NULL DEFAULT 100,
            recorded_at TEXT NOT NULL,
            -- T-5 時点の判定情報
            t5_favorite_payout INTEGER,
            t5_judgment TEXT,
            -- 結果 (settle 後)
            settled INTEGER NOT NULL DEFAULT 0,
            payout INTEGER,
            hit INTEGER,
            roi REAL,
            UNIQUE(race_id, strategy, bet_type, combination)
        );
        CREATE INDEX IF NOT EXISTS idx_paper_trades_date ON paper_trades(race_date);
        CREATE INDEX IF NOT EXISTS idx_paper_trades_strategy ON paper_trades(strategy);
    """)
    conn.commit()


def record_signals(target_date: str):
    """指定日のレースで T-5 オッズベースのシグナル判定 → ペーパー記録"""
    conn = sqlite3.connect(DB)
    ensure_schema(conn)

    # T-5 オッズが取れているレースを抽出
    cur = conn.execute("""
        WITH t5 AS (
            SELECT race_id, MIN(odds) as t5_min_odds
            FROM odds_trifecta
            WHERE snapshot_label = 'T-5min'
            GROUP BY race_id
        )
        SELECT r.race_id, r.race_grade_number, r.race_closed_at,
               e.class_number as cls1, t5.t5_min_odds
        FROM races r
        JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
        LEFT JOIN t5 ON r.race_id = t5.race_id
        WHERE r.race_date = ?
          AND t5.t5_min_odds IS NOT NULL
    """, (target_date,))

    rows = cur.fetchall()
    if not rows:
        print(f"[{target_date}] T-5 オッズ付きレースなし")
        return 0

    recorded = 0
    now = _now_jst_iso()

    for race_id, grade, closed, cls1, t5_min_odds in rows:
        t5_payout = int(t5_min_odds * 100)  # オッズ × 100円 = 払戻
        # 戦略判定
        if 500 <= t5_payout < 1000:
            # 全ての該当戦略をシグナル化

            # Strategy 1: 単勝 1
            conn.execute("""
                INSERT OR IGNORE INTO paper_trades
                (race_id, race_date, strategy, bet_type, combination, recorded_at,
                 t5_favorite_payout, t5_judgment)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (race_id, target_date, "win_1_500_1k", "win", "1", now,
                  t5_payout, "三連単本命500-1k帯 → 1号艇単勝"))

            # Strategy 2: 3連単 1-2-3
            conn.execute("""
                INSERT OR IGNORE INTO paper_trades
                (race_id, race_date, strategy, bet_type, combination, recorded_at,
                 t5_favorite_payout, t5_judgment)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (race_id, target_date, "tri_123_500_1k", "trifecta", "1-2-3", now,
                  t5_payout, "三連単本命500-1k帯 → 3連単1-2-3"))

            # Strategy 3: 2連単 1-2
            conn.execute("""
                INSERT OR IGNORE INTO paper_trades
                (race_id, race_date, strategy, bet_type, combination, recorded_at,
                 t5_favorite_payout, t5_judgment)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (race_id, target_date, "exa_12_500_1k", "exacta", "1-2", now,
                  t5_payout, "三連単本命500-1k帯 → 2連単1-2"))

            # Strategy 4 (条件付): 一般戦+B1 1号艇 → 強シグナル
            if grade == 5 and cls1 == 3:
                conn.execute("""
                    INSERT OR IGNORE INTO paper_trades
                    (race_id, race_date, strategy, bet_type, combination, recorded_at,
                     t5_favorite_payout, t5_judgment)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (race_id, target_date, "ippan_b1_win_500_1k", "win", "1", now,
                      t5_payout, "一般戦+B1+本命 → 強シグナル ROI +35.39%"))

            recorded += 1
        elif t5_payout < 500:
            # 超本命
            conn.execute("""
                INSERT OR IGNORE INTO paper_trades
                (race_id, race_date, strategy, bet_type, combination, recorded_at,
                 t5_favorite_payout, t5_judgment)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (race_id, target_date, "win_1_super_fav", "win", "1", now,
                  t5_payout, "超本命<500 → 1号艇単勝 ROI +18.45%"))
            recorded += 1
        elif 1000 <= t5_payout < 2000:
            # やや本命
            conn.execute("""
                INSERT OR IGNORE INTO paper_trades
                (race_id, race_date, strategy, bet_type, combination, recorded_at,
                 t5_favorite_payout, t5_judgment)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (race_id, target_date, "win_1_1k_2k", "win", "1", now,
                  t5_payout, "やや本命1k-2k → 1号艇単勝 ROI +17.92%"))
            recorded += 1

    conn.commit()
    print(f"[{target_date}] {recorded} レースで {recorded * 2} 件以上のシグナル記録")
    conn.close()
    return recorded


def settle(target_date: str):
    """指定日のレース結果 (race_payouts) と照合してペーパートレードを確定"""
    conn = sqlite3.connect(DB)
    ensure_schema(conn)

    cur = conn.execute("""
        SELECT pt.id, pt.race_id, pt.bet_type, pt.combination, pt.bet_amount
        FROM paper_trades pt
        WHERE pt.race_date = ? AND pt.settled = 0
    """, (target_date,))
    pending = cur.fetchall()

    settled_count = 0
    hit_count = 0
    total_bet = 0
    total_payout = 0

    for pid, race_id, bet_type, combo, amount in pending:
        # 該当する払戻を取得
        cur = conn.execute("""
            SELECT payout FROM race_payouts
            WHERE race_id = ? AND bet_type = ? AND combination = ?
        """, (race_id, bet_type, combo))
        row = cur.fetchone()
        payout = (row[0] if row else 0) or 0
        hit = 1 if payout > 0 else 0
        roi = payout / amount - 1.0

        conn.execute("""
            UPDATE paper_trades SET settled=1, payout=?, hit=?, roi=?
            WHERE id=?
        """, (payout, hit, roi, pid))

        settled_count += 1
        if hit:
            hit_count += 1
        total_bet += amount
        total_payout += payout

    conn.commit()
    if settled_count:
        actual_roi = total_payout / total_bet - 1.0 if total_bet > 0 else 0
        print(f"[{target_date}] settled={settled_count} hits={hit_count}({hit_count/settled_count*100:.1f}%) "
              f"投資=¥{total_bet:,} 払戻=¥{total_payout:,} ROI={actual_roi:+.2%}")
    else:
        print(f"[{target_date}] 未確定なし")
    conn.close()
    return settled_count


def report():
    """累積成績レポート"""
    conn = sqlite3.connect(DB)
    ensure_schema(conn)

    print("=" * 90)
    print("ペーパートレード累積成績")
    print("=" * 90)

    cur = conn.execute("""
        SELECT strategy,
               COUNT(*) as n_total,
               SUM(settled) as n_settled,
               SUM(CASE WHEN settled=1 AND hit=1 THEN 1 ELSE 0 END) as n_hit,
               SUM(bet_amount) as total_bet,
               SUM(CASE WHEN settled=1 THEN payout ELSE 0 END) as total_payout
        FROM paper_trades
        GROUP BY strategy
        ORDER BY n_total DESC
    """)
    print(f"{'戦略':<25} {'記録':>6} {'確定':>6} {'的中':>6} {'的中率':>8} {'投資':>10} {'払戻':>10} {'ROI':>10}")
    print("-" * 90)
    grand_bet = 0
    grand_pay = 0
    for strategy, n_t, n_s, n_h, bet, pay in cur.fetchall():
        hit_rate = n_h / n_s if n_s else 0
        roi = pay / bet - 1.0 if bet else 0
        print(f"{strategy:<25} {n_t:>6,} {n_s or 0:>6,} {n_h or 0:>6,} {hit_rate:>8.1%} "
              f"¥{bet or 0:>8,} ¥{pay or 0:>8,} {roi:>+10.2%}")
        grand_bet += (bet or 0)
        grand_pay += (pay or 0)

    if grand_bet > 0:
        print("-" * 90)
        grand_roi = grand_pay / grand_bet - 1.0
        print(f"{'TOTAL':<25} {'':>6} {'':>6} {'':>6} {'':>8} "
              f"¥{grand_bet:>8,} ¥{grand_pay:>8,} {grand_roi:>+10.2%}")

    conn.close()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["record", "settle", "report"])
    p.add_argument("--date", type=str, default=None,
                   help="対象日 (YYYY-MM-DD), 省略時は今日")
    args = p.parse_args()

    if args.action == "report":
        report()
        return

    target = args.date or _today_jst_iso()
    if args.action == "record":
        record_signals(target)
    elif args.action == "settle":
        settle(target)


if __name__ == "__main__":
    main()
