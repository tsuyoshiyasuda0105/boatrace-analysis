"""
事前予測キャッシュ生成

ローカル SQLite (フルデータ) で予測を計算し、
predictions テーブルに保存 + Supabase へ同期。

これにより Render は predictions を読むだけで済む = Supabase Free でも動作。

使い方:
  python scripts/cache_predictions.py --date 2026-05-12
  python scripts/cache_predictions.py --date-from 2026-05-01 --date-to 2026-05-12
  python scripts/cache_predictions.py --today  # 今日
"""
import argparse
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from src.web.predictor import Predictor


def ensure_predictions_schema(conn):
    """predictions テーブルが無ければ作成"""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS predictions (
            race_id TEXT NOT NULL,
            boat_number INTEGER NOT NULL,
            model_version TEXT NOT NULL,
            prob_first REAL,
            prob_top_2 REAL,
            prob_top_3 REAL,
            predicted_at TEXT NOT NULL,
            PRIMARY KEY (race_id, boat_number, model_version)
        );
        CREATE INDEX IF NOT EXISTS idx_predictions_race ON predictions(race_id);
    """)
    conn.commit()


def cache_predictions_for_date(target_date: str, version: str = "v0.8"):
    """指定日の全レースについて予測を計算して DB に保存"""
    # 強制的にローカル SQLite を使う (DATABASE_URL があっても無視)
    saved_url = os.environ.pop("DATABASE_URL", None)
    try:
        conn = sqlite3.connect(config.DB_PATH)
        ensure_predictions_schema(conn)

        # 予測器 (ローカル SQLite + フル history)
        predictor = Predictor(version=version)
        predictor.load()

        print(f"[{target_date}] 予測計算中...")
        df = predictor.predict_date(target_date)
        if df is None or df.empty:
            print(f"  → データなし or 予測失敗")
            return 0

        now_iso = datetime.now().isoformat()
        rows = []
        for _, row in df.iterrows():
            rows.append((
                row["race_id"],
                int(row["boat_number"]),
                version,
                float(row.get("prob_first", 0) or 0),
                float(row.get("prob_top_2", 0) or 0),
                float(row.get("prob_top_3", 0) or 0),
                now_iso,
            ))

        conn.executemany("""
            INSERT OR REPLACE INTO predictions
            (race_id, boat_number, model_version, prob_first, prob_top_2, prob_top_3, predicted_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, rows)
        conn.commit()
        n_races = df["race_id"].nunique()
        print(f"  → {n_races} レース / {len(rows)} 行を保存")

        conn.close()
        return n_races
    finally:
        # DATABASE_URL を復元
        if saved_url:
            os.environ["DATABASE_URL"] = saved_url


def sync_predictions_to_supabase():
    """ローカル predictions テーブル → Supabase へ同期"""
    db_url = os.getenv("DATABASE_URL", "").strip()
    if not db_url or not db_url.startswith(("postgres://", "postgresql://")):
        print("DATABASE_URL 未設定 (Supabase 同期スキップ)")
        return 0

    from src.db.connection import connect as db_connect

    src = sqlite3.connect(config.DB_PATH)

    cur = src.execute("SELECT * FROM predictions")
    rows = cur.fetchall()
    src.close()
    if not rows:
        print("ローカルに predictions なし")
        return 0

    # CREATE TABLE はスキップ (schema.sql で既に作成済、Supabase が Read-Only 時の Fallback)
    try:
        dst = db_connect()
        # まずテーブル存在確認 (CREATE せずに SELECT)
        try:
            dst.execute("SELECT 1 FROM predictions LIMIT 1")
        except Exception as table_err:
            # テーブルなし → CREATE 試行 (失敗時はメッセージ)
            print(f"predictions テーブルが Supabase に未作成: {table_err}")
            print("init_db.py を Supabase に対して実行してください:")
            print("  python scripts/init_db.py  (DATABASE_URL 設定済の状態で)")
            return 0

        # INSERT バッチ (500行ずつ)
        BATCH = 500
        total_synced = 0
        for i in range(0, len(rows), BATCH):
            batch = rows[i:i+BATCH]
            try:
                dst.executemany("""
                    INSERT OR REPLACE INTO predictions
                    (race_id, boat_number, model_version, prob_first, prob_top_2, prob_top_3, predicted_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, batch)
                total_synced += len(batch)
                print(f"  {total_synced}/{len(rows)} 行同期...")
            except Exception as e:
                err_str = str(e)
                if "read-only" in err_str.lower() or "ReadOnlySql" in err_str:
                    print(f"\nSupabase が読み取り専用モードです (容量超過の可能性)")
                    print("対処: Supabase ダッシュボードで Database Status を確認")
                    print(f"ローカル predictions ({len(rows)} 行) は保存済。後で再同期可能。")
                    dst.close()
                    return total_synced
                else:
                    print(f"  エラー {i}-{i+BATCH}: {e}")
                    continue
        dst.close()
        print(f"Supabase に {total_synced} 行同期完了")
        return total_synced
    except Exception as e:
        print(f"Supabase 同期エラー: {e}")
        print(f"ローカル predictions は保存済 ({len(rows)} 行)")
        return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--date", type=str)
    p.add_argument("--date-from", type=str)
    p.add_argument("--date-to", type=str)
    p.add_argument("--today", action="store_true")
    p.add_argument("--tomorrow", action="store_true",
                   help="明日のみ予測キャッシュ (run_daily_collect.bat 23:30 用)")
    p.add_argument("--version", type=str, default="v0.8")
    p.add_argument("--sync", action="store_true", help="計算後 Supabase へ同期")
    args = p.parse_args()

    if args.tomorrow:
        targets = [(date.today() + timedelta(days=1)).isoformat()]
    elif args.today:
        targets = [date.today().isoformat()]
    elif args.date:
        targets = [args.date]
    elif args.date_from and args.date_to:
        d1 = date.fromisoformat(args.date_from)
        d2 = date.fromisoformat(args.date_to)
        targets = [(d1 + timedelta(days=i)).isoformat() for i in range((d2 - d1).days + 1)]
    else:
        print("--date / --date-from + --date-to / --today / --tomorrow を指定してください")
        return

    total = 0
    for d in targets:
        total += cache_predictions_for_date(d, version=args.version)
    print(f"\n合計: {total} レースの予測キャッシュ完了")

    if args.sync:
        sync_predictions_to_supabase()


if __name__ == "__main__":
    main()
