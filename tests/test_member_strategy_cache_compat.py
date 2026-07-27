import json
import os
import sqlite3
import sys


def test_member_strategy_uses_stale_daily_cache_metrics(tmp_path, monkeypatch):
    db_path = tmp_path / "boatrace.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE l4_daily_stats_cache "
            "(race_date TEXT PRIMARY KEY, stats_json TEXT NOT NULL)"
        )
        conn.execute("CREATE TABLE races (race_id TEXT PRIMARY KEY, race_date TEXT)")
        conn.execute(
            "CREATE TABLE race_results "
            "(race_id TEXT, boat_number INTEGER, finishing_position INTEGER)"
        )
        conn.execute("CREATE TABLE race_payouts (race_id TEXT, bet_type TEXT)")
        conn.execute(
            "INSERT INTO races (race_id, race_date) VALUES (?, ?)",
            ("202607160101", "2026-07-16"),
        )
        conn.execute(
            "INSERT INTO race_results (race_id, boat_number, finishing_position) "
            "VALUES (?, ?, ?)",
            ("202607160101", 1, 1),
        )
        stats = {
            "date": "2026-07-16",
            "n_total": 1,
            "n_l4": 0,
            "_adopted_daily_select_version": "old-version",
            "_strict_odds_only": True,
            "tri134_acc2_ex3_tri_bets": 1,
            "tri134_acc2_ex3_tri_hits": 1,
            "tri134_acc2_ex3_tri_pay": 780,
        }
        conn.execute(
            "INSERT INTO l4_daily_stats_cache (race_date, stats_json) VALUES (?, ?)",
            ("2026-07-16", json.dumps(stats)),
        )

    monkeypatch.setenv("DATABASE_URL", "local-sqlite")
    monkeypatch.setenv("BOATRACE_DB_PATH", str(db_path))

    sys.path.insert(0, os.fspath(tmp_path.parent))
    sys.path.insert(0, "C:/boat_project/boatrace-analysis")
    from src.web.app import create_app

    app = create_app()
    app.config.update(TESTING=True, SECRET_KEY="test")
    client = app.test_client()
    with client.session_transaction() as session:
        session["is_member"] = True

    resp = client.get("/member/strategy?from=2026-07-16&to=2026-07-16")
    html = resp.get_data(as_text=True)

    assert resp.status_code == 200
    assert "1-3-4" in html
    assert "n=1" in html
    assert "HIT 1/1" in html
