import sqlite3

from scripts.check_post_run_integrity import RULE_VERSION, check_accident_integrity


def test_integrity_uses_fresh_internal_snapshot_over_stale_official():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE racer_accident_period_stats (
            period_start TEXT, period_end TEXT, source_kind TEXT,
            rule_version TEXT, updated_at TEXT, accident_rate REAL
        );
        CREATE TABLE racer_accident_rank_snapshots (
            period_start TEXT, period_end TEXT, snapshot_date TEXT,
            source_kind TEXT, source_rule_version TEXT, updated_at TEXT
        );
        """
    )
    conn.executemany(
        "INSERT INTO racer_accident_period_stats VALUES (?, ?, ?, ?, '', 1.0)",
        [
            ("2026-05-01", "2026-08-11", "official_external", RULE_VERSION),
            ("2026-05-01", "2026-08-12", "internal_rebuild", RULE_VERSION),
        ],
    )
    conn.execute(
        "INSERT INTO racer_accident_rank_snapshots VALUES (?, ?, ?, ?, ?, '')",
        ("2026-05-01", "2026-08-12", "2026-08-12", "internal_rebuild", RULE_VERSION),
    )

    status, _message, detail = check_accident_integrity(conn, "2026-08-12")

    assert status == "ok"
    assert detail["source_kind"] == "internal_rebuild"
    assert detail["snapshot_rows"] == 1
