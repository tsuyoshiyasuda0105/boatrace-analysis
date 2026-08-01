import json
import sqlite3

from src.roi_history import (
    load_roi_history_daily,
    load_roi_history_races,
    replace_roi_history_snapshot,
)
from src.web.app import _parse_market_signal_bets_for_roi


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE race_results (race_id TEXT, finishing_position INTEGER);
        CREATE TABLE race_payouts (race_id TEXT, bet_type TEXT, combination TEXT, payout INTEGER);
        CREATE TABLE stadiums (stadium_number INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE races (
            race_id TEXT,
            race_date TEXT,
            stadium_number INTEGER,
            race_number INTEGER,
            race_closed_at TEXT
        );
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


def test_legacy_hamanako_exacta_signal_imports_as_current_roi_key():
    conn = _conn()
    conn.execute("INSERT INTO stadiums VALUES (6, '浜名湖')")
    conn.execute(
        "INSERT INTO races VALUES ('20260704-06-12', '2026-07-04', 6, 12, '2026-07-04 17:04:00')"
    )
    conn.execute("INSERT INTO race_results VALUES ('20260704-06-12', 1)")
    conn.execute("INSERT INTO race_payouts VALUES ('20260704-06-12', 'exacta', '1-4', 290)")
    payload = {
        "date": "2026-07-04",
        "cache_version": "legacy",
        "signals": {
            "20260704-06-12": {
                "race_id": "20260704-06-12",
                "l4": {
                    "level": "exacta_niche_hamanako14",
                    "label": "浜名湖 2連単1-4",
                    "bet": "2連単 1-4",
                },
            }
        },
    }

    count = replace_roi_history_snapshot(
        conn,
        payload,
        source_cache_key="market_signals:2026-07-04",
        capture_quality="same_day_final_cache",
        adopted_keys=("hamanako_14_exa",),
        bet_unit_map={},
        parse_bets=_parse_market_signal_bets_for_roi,
        strategy_signature="sig",
    )

    assert count == 1
    row = conn.execute(
        "SELECT strategy_key, stake_amount, payout_amount, is_hit, is_settled "
        "FROM roi_race_history"
    ).fetchone()
    assert row == ("hamanako_14_exa", 100, 290, 1, 1)
    daily = load_roi_history_daily(conn, "2026-07-01", "2026-07-31", ("hamanako_14_exa",))
    assert daily["2026-07-04"]["hamanako_14_exa"] == {
        "bets": 1,
        "hits": 1,
        "pay": 290,
        "stake": 100,
    }


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


def test_load_roi_history_races_formats_active_settled_rows_only():
    conn = _conn()
    conn.execute("INSERT INTO stadiums VALUES (1, 'Kiryu')")
    conn.execute(
        "INSERT INTO races VALUES ('20260731-01-12', '2026-07-31', 1, 12, '2026-07-31 20:45:00')"
    )
    conn.execute("INSERT INTO race_results VALUES ('20260731-01-12', 1)")
    conn.execute("INSERT INTO race_payouts VALUES ('20260731-01-12', 'exacta', '1-3', 540)")
    payload = {
        "date": "2026-07-31",
        "signals": {
            "20260731-01-12": {
                "race_id": "20260731-01-12",
                "l4": {"level": "kiryu_13", "label": "Kiryu 1-3", "bet": "2騾｣蜊・1-3"},
            }
        },
    }
    replace_roi_history_snapshot(
        conn,
        payload,
        source_cache_key="cache",
        capture_quality="exact_last_good",
        adopted_keys=("kiryu_13",),
        bet_unit_map={},
        parse_bets=_parse_market_signal_bets_for_roi,
        strategy_signature="sig",
    )

    rows = load_roi_history_races(conn, "2026-07-01", "2026-07-31", ("kiryu_13",))

    assert len(rows) == 1
    assert rows[0]["race_date"] == "2026-07-31"
    assert rows[0]["stadium_name"] == "Kiryu"
    assert rows[0]["race_number"] == 12
    assert rows[0]["bet"] == "exacta 1-3"
    assert rows[0]["payout"] == 540
    assert rows[0]["recovery"] == 540.0
