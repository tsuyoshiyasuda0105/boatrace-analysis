import json
import sqlite3

from src.roi_history import load_roi_history_daily, replace_roi_history_snapshot
from src.web.app import _parse_market_signal_bets_for_roi


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE race_results (race_id TEXT, finishing_position INTEGER);
        CREATE TABLE race_payouts (race_id TEXT, bet_type TEXT, combination TEXT, payout INTEGER);
        """
    )
    return conn


def test_snapshot_becomes_settled_race_history_and_daily_totals():
    conn = _conn()
    conn.execute("INSERT INTO race_results VALUES ('20260731-01-12', 1)")
    conn.execute("INSERT INTO race_payouts VALUES ('20260731-01-12', 'exacta', '1-3', 540)")
    payload = {
        "date": "2026-07-31",
        "cache_version": "v27",
        "signals": {
            "20260731-01-12": {
                "race_id": "20260731-01-12",
                "l4": {"level": "general", "matched_levels": ["kiryu_13"], "bet": "2連単 1-3"},
            }
        },
    }
    count = replace_roi_history_snapshot(
        conn,
        payload,
        source_cache_key="market_signals:last-good:2026-07-31",
        capture_quality="exact_last_good",
        adopted_keys=("kiryu_13",),
        bet_unit_map={},
        parse_bets=_parse_market_signal_bets_for_roi,
        strategy_signature="sig",
    )
    assert count == 1
    row = conn.execute(
        "SELECT strategy_key, bet_json, stake_amount, payout_amount, is_hit, is_settled "
        "FROM roi_race_history"
    ).fetchone()
    assert row[0] == "kiryu_13"
    assert json.loads(row[1]) == [{"bet_type": "exacta", "combination": "1-3"}]
    assert row[2:] == (100, 540, 1, 1)
    daily = load_roi_history_daily(conn, "2026-07-01", "2026-07-31", ("kiryu_13",))
    assert daily["2026-07-31"]["kiryu_13"] == {"bets": 1, "hits": 1, "pay": 540, "stake": 100}


def test_empty_snapshot_retires_but_preserves_stale_date_rows():
    conn = _conn()
    first = {
        "date": "2026-07-31",
        "signals": {"r1": {"race_id": "r1", "l4": {"level": "s1", "bet": "3連単 1-2-3"}}},
    }
    common = dict(
        source_cache_key="cache",
        capture_quality="same_day_final_cache",
        adopted_keys=("s1",),
        bet_unit_map={},
        parse_bets=_parse_market_signal_bets_for_roi,
        strategy_signature="sig",
    )
    replace_roi_history_snapshot(conn, first, **common)
    replace_roi_history_snapshot(conn, {"date": "2026-07-31", "signals": {}}, **common)
    assert conn.execute("SELECT COUNT(*) FROM roi_race_history").fetchone()[0] == 1
    assert conn.execute("SELECT is_active FROM roi_race_history").fetchone()[0] == 0
