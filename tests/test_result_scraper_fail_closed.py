# -*- coding: utf-8 -*-
"""P0-3 タスク3: L4 候補クエリ失敗時の fail-closed 回帰テスト。

以前は候補クエリが失敗すると l4_only=False (全レース取得) に拡大しており、
DB 不調時ほど boatrace.jp へのリクエストが増える逆保険になっていた。
現在は fail-closed: 失敗パスでは候補以外を一切取得しない。
"""
import sqlite3
from datetime import date, datetime, timedelta

from src.collectors import result_scraper


def _conn_without_predictions(race_count: int = 3):
    """predictions テーブルが無い DB → 候補クエリが必ず失敗する。"""
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE races (
            race_id TEXT PRIMARY KEY,
            race_date TEXT,
            stadium_number INTEGER,
            race_closed_at TEXT
        );
        CREATE TABLE race_entries (race_id TEXT, boat_number INTEGER, class_number INTEGER);
        CREATE TABLE race_payouts (race_id TEXT, bet_type TEXT);
        CREATE TABLE race_results (race_id TEXT, kimarite TEXT);
        """
    )
    now = datetime(2026, 8, 12, 12, 0)
    for race_no in range(1, race_count + 1):
        race_id = f"20260812-21-{race_no:02d}"
        conn.execute(
            "INSERT INTO races VALUES (?, ?, ?, ?)",
            (race_id, "2026-08-12", 21, (now - timedelta(minutes=90)).isoformat()),
        )
        conn.execute("INSERT INTO race_entries VALUES (?, 1, 1)", (race_id,))
    return conn, now


def test_candidate_lookup_failure_does_not_expand_to_all_races(monkeypatch):
    conn, now = _conn_without_predictions()
    monkeypatch.setattr(result_scraper, "_jst_now_naive", lambda: now)

    calls = []

    def _record_call(race_id):
        calls.append(race_id)
        return None

    monkeypatch.setattr(result_scraper, "scrape_race_result", _record_call)

    got = result_scraper.scrape_results_for_pending_races(date(2026, 8, 12), conn)

    # fail-closed: 候補不明のパスでは 1 件もスクレイプしない
    assert got["target_count"] == 0
    assert calls == []


def test_healthy_lookup_still_repairs_non_candidates(monkeypatch):
    """fail-closed 化しても、正常パスの非候補リペア (60分遅延) は従来通り動く。"""
    conn, now = _conn_without_predictions(race_count=1)
    conn.execute(
        "CREATE TABLE predictions (race_id TEXT, boat_number INTEGER, prob_first REAL)"
    )
    monkeypatch.setattr(result_scraper, "_jst_now_naive", lambda: now)
    monkeypatch.setattr(result_scraper, "_market_signal_candidate_ids", lambda *_: set())

    def _payload(race_id):
        date_str, stadium, race_no = race_id.split("-")
        return {
            "race_date": f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}",
            "race_stadium_number": int(stadium),
            "race_number": int(race_no),
            "boats": [],
            "payouts": {"trifecta": []},
        }

    monkeypatch.setattr(result_scraper, "scrape_race_result", _payload)

    got = result_scraper.scrape_results_for_pending_races(date(2026, 8, 12), conn)

    assert got["target_count"] == 1
