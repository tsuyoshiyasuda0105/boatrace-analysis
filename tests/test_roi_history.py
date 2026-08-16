import json
import sqlite3

from src.roi_history import (
    load_roi_history_daily,
    load_roi_history_races,
    replace_roi_history_snapshot,
    settle_roi_history_for_date,
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


def test_daily_loader_returns_real_registry_key_and_ignores_unsettled_row():
    conn = _conn()
    assert load_roi_history_daily(
        conn,
        "2026-08-16",
        "2026-08-16",
        ("a1_ace_motor_123_corr_tri",),
    ) == {}
    common = (
        "a1_ace_motor_123_corr_tri",
        "A1 ace motor 1-2-3",
        '[{"bet_type":"trifecta","combination":"1-2-3"}]',
        100,
        "market_signals:last-good:2026-08-16",
        "v27",
        "sig",
        "2026-08-16T10:00:00",
        "live_last_good",
        "hash",
        "2026-08-16T19:58:38",
    )
    conn.execute(
        """
        INSERT INTO roi_race_history (
            race_date, race_id, strategy_key, strategy_label, bet_json,
            stake_amount, payout_amount, is_hit, is_settled, is_active,
            source_cache_key, source_cache_version, strategy_signature,
            snapshot_computed_at, capture_quality, payload_hash, updated_at
        ) VALUES ('2026-08-16', '20260816-01-01', ?, ?, ?, ?, 640, 1, 1, 1,
                  ?, ?, ?, ?, ?, ?, ?)
        """,
        common,
    )
    conn.execute(
        """
        INSERT INTO roi_race_history (
            race_date, race_id, strategy_key, strategy_label, bet_json,
            stake_amount, payout_amount, is_hit, is_settled, is_active,
            source_cache_key, source_cache_version, strategy_signature,
            snapshot_computed_at, capture_quality, payload_hash, updated_at
        ) VALUES ('2026-08-16', '20260816-01-02', ?, ?, ?, ?, 0, 0, 0, 1,
                  ?, ?, ?, ?, ?, ?, ?)
        """,
        common,
    )

    daily = load_roi_history_daily(
        conn,
        "2026-08-16",
        "2026-08-16",
        ("a1_ace_motor_123_corr_tri",),
    )

    assert daily == {
        "2026-08-16": {
            "a1_ace_motor_123_corr_tri": {
                "bets": 1,
                "hits": 1,
                "pay": 640,
                "stake": 100,
            }
        }
    }


def test_existing_roi_row_settles_after_result_arrives():
    conn = _conn()
    ensure_payload = {
        "date": "2026-08-11",
        "signals": {
            "20260811-13-12": {
                "race_id": "20260811-13-12",
                "l4": {"level": "amagasaki", "bet": "単勝 3"},
            }
        },
    }
    replace_roi_history_snapshot(
        conn,
        ensure_payload,
        source_cache_key="market_signals:last-good:2026-08-11",
        capture_quality="exact_last_good",
        adopted_keys=("amagasaki",),
        bet_unit_map={},
        parse_bets=_parse_market_signal_bets_for_roi,
        strategy_signature="sig",
    )
    conn.execute("INSERT INTO race_results VALUES ('20260811-13-12', 1)")
    conn.execute("INSERT INTO race_payouts VALUES ('20260811-13-12', 'win', '3', 420)")
    assert settle_roi_history_for_date(conn, "2026-08-11") == 1
    assert conn.execute(
        "SELECT payout_amount, is_hit, is_settled FROM roi_race_history"
    ).fetchone() == (420, 1, 1)


def test_reference_and_female_excluded_signals_do_not_enter_roi_history():
    conn = _conn()
    conn.execute("INSERT INTO race_results VALUES ('20260803-05-12', 1)")
    conn.execute("INSERT INTO race_payouts VALUES ('20260803-05-12', 'exacta', '1-3', 190)")
    payload = {
        "date": "2026-08-03",
        "cache_version": "v27",
        "signals": {
            "20260803-05-12": {
                "race_id": "20260803-05-12",
                "n_female": 1,
                "l4": {
                    "level": "tamagawa_13_weak_sashi2_exa",
                    "matched_levels": ["general", "tamagawa_13_weak_sashi2_exa"],
                    "is_reference": True,
                    "is_female_present": True,
                    "bet": "exacta 1-3",
                },
            }
        },
    }

    count = replace_roi_history_snapshot(
        conn,
        payload,
        source_cache_key="market_signals:last-good:2026-08-03",
        capture_quality="exact_last_good",
        adopted_keys=("tamagawa_13_weak_sashi2_exa",),
        bet_unit_map={},
        parse_bets=_parse_market_signal_bets_for_roi,
        strategy_signature="sig",
    )

    assert count == 0
    assert conn.execute("SELECT COUNT(*) FROM roi_race_history").fetchone()[0] == 0

    conn.execute("INSERT INTO stadiums VALUES (5, 'Tamagawa')")
    conn.execute(
        "INSERT INTO races VALUES ('20260803-05-12', '2026-08-03', 5, 12, '2026-08-03 17:35:00')"
    )
    conn.execute(
        """
        INSERT INTO roi_race_history (
            race_date, race_id, strategy_key, strategy_label, bet_json,
            stake_amount, payout_amount, is_hit, is_settled, is_active,
            source_cache_key, source_cache_version, strategy_signature,
            snapshot_computed_at, capture_quality, payload_hash, updated_at
        ) VALUES (
            '2026-08-03', '20260803-05-12', 'tamagawa_13_weak_sashi2_exa',
            '♀多摩川 1-3 (女性1名除外)', '[{"bet_type":"exacta","combination":"1-3"}]',
            100, 190, 1, 1, 1, 'cache', 'v27', 'sig', 'now', 'exact_last_good', 'hash', 'now'
        )
        """
    )

    daily = load_roi_history_daily(
        conn,
        "2026-08-03",
        "2026-08-03",
        ("tamagawa_13_weak_sashi2_exa",),
    )
    races = load_roi_history_races(
        conn,
        "2026-08-03",
        "2026-08-03",
        ("tamagawa_13_weak_sashi2_exa",),
    )

    assert daily == {}
    assert races == []


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
