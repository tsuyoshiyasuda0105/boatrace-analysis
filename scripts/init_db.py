"""
DB初期化スクリプト
- スキーマ作成
- 会場マスタの投入

使い方:
    python scripts/init_db.py
    DATABASE_URL=postgresql://... python scripts/init_db.py  # Postgres
"""
import os
import sys
import json
from pathlib import Path

# プロジェクトルートを sys.path に追加
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from src.db.connection import connect


def init_schema(conn) -> None:
    """schema.sqlを実行してテーブル作成"""
    schema_path = config.ROOT_DIR / "src" / "db" / "schema.sql"
    with open(schema_path, encoding="utf-8") as f:
        conn.executescript(f.read())
    print(f"[OK] スキーマ作成: {schema_path}")


def load_stadiums(conn) -> None:
    """master/stadiums.jsonを読み込んでstadiumsテーブルに投入"""
    path = config.MASTER_DIR / "stadiums.json"
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    rows = []
    for k, v in data.items():
        if k.startswith("_"):
            continue
        rows.append((
            int(k),
            v["name"],
            v["water"],
            1 if v["is_night"] else 0,
            v["in_strength"],
            v["tide_effect"],
            1 if v.get("altitude_high") else 0,
            v.get("notes"),
        ))

    conn.executemany("""
        INSERT OR REPLACE INTO stadiums
            (stadium_number, name, water, is_night, in_strength, tide_effect, altitude_high, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, rows)
    print(f"[OK] 会場マスタ投入: {len(rows)}件")


def main() -> None:
    config.ensure_dirs()
    db_url = os.getenv("DATABASE_URL", "")
    target = db_url if db_url else str(config.DB_PATH)
    conn = connect()
    try:
        init_schema(conn)
        load_stadiums(conn)
        conn.commit()
        print(f"[DONE] DB初期化完了: {target}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
