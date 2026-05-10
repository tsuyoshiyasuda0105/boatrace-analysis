"""
SQLite 接続ヘルパー

全コレクター共通で使う。WAL モード + busy_timeout により、
Open API バッチと Layer 3 スクレイパーの同時実行に耐える。
"""
from __future__ import annotations

import sqlite3
from typing import Optional

import config


def connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    """
    プロジェクト共通の SQLite 接続を返す。

    - journal_mode=WAL: 読み書き同時を許可（マルチプロセス対策）
    - busy_timeout: 他プロセスのロック解放まで待機
    - foreign_keys=ON: FK制約を有効化
    """
    path = db_path or config.DB_PATH
    conn = sqlite3.connect(path, timeout=config.SQLITE_CONNECT_TIMEOUT_SECONDS)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute(f"PRAGMA busy_timeout={config.SQLITE_BUSY_TIMEOUT_MS};")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn
