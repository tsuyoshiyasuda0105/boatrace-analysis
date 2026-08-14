# -*- coding: utf-8 -*-
"""P0-3 タスク1: 決まり手パーサー + 再スクレイプ収束の回帰テスト。

fixture HTML は boatrace.jp raceresult ページの決まり手テーブル構造
(<table><thead><th>決まり手</th></thead><tbody><td>値</td></tbody></table>)
を最小再現したもの。実ページの保存 HTML がローカルに存在しないため、
本物のページを保存できたら fixture を差し替えること
(reports/p0_3_work_log_20260814.md 参照)。
"""
import sqlite3
from datetime import date, datetime, timedelta

import pytest
from bs4 import BeautifulSoup

from src.collectors import result_scraper
from src.collectors.openapi import upsert_results
from src.parsers.result_html import KIMARITE_VALUES, parse_kimarite, parse_result_html


def _fixture_html(kimarite: str | None = "逃げ") -> str:
    kimarite_table = ""
    if kimarite is not None:
        kimarite_table = f"""
        <table class="is-w243 h-mt10">
          <thead><tr><th>決まり手</th></tr></thead>
          <tbody><tr><td>{kimarite}</td></tr></tbody>
        </table>
        """
    return f"""
    <html><body>
    <table class="is-w495">
      <thead><tr><th>着</th><th>枠</th><th>ボートレーサー</th><th>レースタイム</th></tr></thead>
      <tbody><tr><td>１</td><td>1</td><td>選手A</td><td>1'49"9</td></tr></tbody>
      <tbody><tr><td>２</td><td>3</td><td>選手B</td><td>1'51"2</td></tr></tbody>
      <tbody><tr><td>３</td><td>2</td><td>選手C</td><td>1'52"0</td></tr></tbody>
    </table>
    {kimarite_table}
    <table class="is-w495">
      <thead><tr><th>勝式</th><th>組番</th><th>払戻金</th><th>人気</th></tr></thead>
      <tbody><tr><td>3連単</td><td>1-3-2</td><td>&yen;1,230</td><td>2</td></tr></tbody>
    </table>
    </body></html>
    """


@pytest.mark.parametrize("kimarite", KIMARITE_VALUES)
def test_each_kimarite_value_is_extracted(kimarite):
    parsed = parse_result_html(_fixture_html(kimarite))

    assert parsed is not None
    assert parsed["race_kimarite"] == kimarite


def test_makurizashi_is_not_truncated_to_makuri():
    parsed = parse_result_html(_fixture_html("まくり差し"))

    assert parsed["race_kimarite"] == "まくり差し"


def test_missing_kimarite_table_returns_none_without_error():
    parsed = parse_result_html(_fixture_html(kimarite=None))

    assert parsed is not None
    assert parsed["race_kimarite"] is None
    assert parsed["boats"]  # 決まり手が無くても結果本体は取れる


def test_unknown_kimarite_text_is_rejected():
    parsed = parse_result_html(_fixture_html("不明な決まり手"))

    assert parsed["race_kimarite"] is None


def test_text_fallback_when_table_structure_changes():
    soup = BeautifulSoup(
        "<div><span>決まり手</span><span>まくり</span></div>", "html.parser"
    )

    assert parse_kimarite(soup) == "まくり"


def test_parse_kimarite_never_raises_on_junk():
    for junk in ("", "<p>決まり手</p>", "<table><tr><td>x</td></tr></table>"):
        assert parse_kimarite(BeautifulSoup(junk, "html.parser")) is None


def test_scrape_race_result_carries_kimarite(monkeypatch):
    monkeypatch.setattr(
        result_scraper, "fetch_html", lambda url: _fixture_html("差し")
    )

    payload = result_scraper.scrape_race_result("20260812-21-01")

    assert payload is not None
    assert payload["race_kimarite"] == "差し"


def test_kimarite_reaches_race_results_via_upsert(monkeypatch):
    """抽出した決まり手が race_results.kimarite (1着行) へ書かれる経路の確認。"""
    monkeypatch.setattr(
        result_scraper, "fetch_html", lambda url: _fixture_html("逃げ")
    )
    payload = result_scraper.scrape_race_result("20260812-21-01")

    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE race_results (
            race_id TEXT, boat_number INTEGER, finishing_position INTEGER,
            course_number INTEGER, start_timing REAL, race_time TEXT,
            remarks TEXT, kimarite TEXT,
            PRIMARY KEY (race_id, boat_number)
        );
        CREATE TABLE race_payouts (
            race_id TEXT, bet_type TEXT, combination TEXT,
            payout INTEGER, popularity INTEGER,
            PRIMARY KEY (race_id, bet_type, combination)
        );
        """
    )
    upsert_results(conn, {"results": [payload]})

    rows = dict(
        conn.execute(
            "SELECT boat_number, kimarite FROM race_results WHERE finishing_position = 1"
        ).fetchall()
    )
    assert rows == {1: "逃げ"}


# ============================================================
# 再スクレイプ収束 (受け入れ条件): 決まり手が取れないレースが
# 毎パス対象になり続けず、KIMARITE_MAX_ATTEMPTS 回で打ち止めになる
# ============================================================


def _kimarite_pending_conn():
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
        CREATE TABLE predictions (race_id TEXT, boat_number INTEGER, prob_first REAL);
        CREATE TABLE race_payouts (race_id TEXT, bet_type TEXT);
        CREATE TABLE race_results (race_id TEXT, kimarite TEXT);
        """
    )
    now = datetime(2026, 8, 12, 12, 0)
    race_id = "20260812-21-01"
    conn.execute(
        "INSERT INTO races VALUES (?, ?, ?, ?)",
        (race_id, "2026-08-12", 21, (now - timedelta(minutes=90)).isoformat()),
    )
    conn.execute("INSERT INTO race_entries VALUES (?, 1, 2)", (race_id,))
    conn.execute("INSERT INTO predictions VALUES (?, 1, 0.5)", (race_id,))
    # 払戻は取得済みだが決まり手が空 → kimarite バックフィル対象
    conn.execute("INSERT INTO race_payouts VALUES (?, 'trifecta')", (race_id,))
    conn.execute("INSERT INTO race_results VALUES (?, NULL)", (race_id,))
    return conn, now


def _payload_without_kimarite(race_id: str):
    date_str, stadium, race_no = race_id.split("-")
    return {
        "race_date": f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}",
        "race_stadium_number": int(stadium),
        "race_number": int(race_no),
        "race_kimarite": None,
        "boats": [],
        "payouts": {"trifecta": []},
    }


def test_kimarite_rescrape_stops_after_attempt_limit(monkeypatch):
    conn, now = _kimarite_pending_conn()
    monkeypatch.setattr(result_scraper, "_jst_now_naive", lambda: now)
    monkeypatch.setattr(result_scraper, "scrape_race_result", _payload_without_kimarite)
    monkeypatch.setattr(result_scraper, "_market_signal_candidate_ids", lambda *_: set())

    counts = []
    for _ in range(result_scraper.KIMARITE_MAX_ATTEMPTS + 2):
        got = result_scraper.scrape_results_for_pending_races(date(2026, 8, 12), conn)
        counts.append(got["target_count"])

    # 上限回数までは対象になり、その後は 0 に収束する
    assert counts[: result_scraper.KIMARITE_MAX_ATTEMPTS] == [1] * result_scraper.KIMARITE_MAX_ATTEMPTS
    assert counts[result_scraper.KIMARITE_MAX_ATTEMPTS :] == [0, 0]


def test_kimarite_success_does_not_consume_attempts(monkeypatch):
    """決まり手が取れたパスではカウンタを増やさない。"""
    conn, now = _kimarite_pending_conn()
    monkeypatch.setattr(result_scraper, "_jst_now_naive", lambda: now)
    monkeypatch.setattr(result_scraper, "_market_signal_candidate_ids", lambda *_: set())

    def _payload_with_kimarite(race_id: str):
        payload = _payload_without_kimarite(race_id)
        payload["race_kimarite"] = "逃げ"
        return payload

    monkeypatch.setattr(result_scraper, "scrape_race_result", _payload_with_kimarite)
    got = result_scraper.scrape_results_for_pending_races(date(2026, 8, 12), conn)

    assert got["target_count"] == 1
    row = conn.execute(
        "SELECT COUNT(*) FROM page_html_cache WHERE cache_key LIKE 'kimarite_retry:%'"
    ).fetchone()
    assert row[0] == 0
