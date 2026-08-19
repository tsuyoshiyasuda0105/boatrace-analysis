import argparse
import json

from scripts import prewarm_strategy_pages as prewarm
from scripts import refresh_race_detail_after_exhibition as exhibition
from src.db.cron_runtime import try_cron_advisory_lock


def test_signal_refresh_skips_when_exhibition_fingerprints_are_unchanged(monkeypatch, capsys):
    snapshot = {"_exhibition_fingerprints": {"race-1": "same"}}
    monkeypatch.setattr(
        prewarm,
        "parse_args",
        lambda: argparse.Namespace(mode="signals", date="2026-08-19", full=False),
    )
    monkeypatch.setattr(prewarm, "_read_incremental_market_snapshot", lambda _date: snapshot)
    monkeypatch.setattr(
        prewarm,
        "_market_signal_exhibition_fingerprints",
        lambda _date: {"race-1": "same"},
    )
    monkeypatch.setattr(prewarm, "get_sql_count", lambda: 2)
    monkeypatch.setattr(
        prewarm,
        "_create_prewarm_app",
        lambda: (_ for _ in ()).throw(AssertionError("full reconstruction must be skipped")),
    )

    assert prewarm.main() == 0
    metrics_line = next(
        line for line in capsys.readouterr().out.splitlines()
        if line.startswith("SIGNAL_REFRESH_METRICS=")
    )
    metrics = json.loads(metrics_line.split("=", 1)[1])
    assert metrics == {
        "changed_races": 0,
        "duration_seconds": metrics["duration_seconds"],
        "reason": "exhibition-unchanged",
        "scope": "skipped",
        "sql_count": 2,
    }


class _Cursor:
    def __init__(self, value):
        self._value = value

    def fetchone(self):
        return (self._value,)


class _PgConnection:
    _kind = "postgres"

    def __init__(self, lock_result):
        self.lock_result = lock_result
        self.calls = []
        self.closed = False

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        return _Cursor(self.lock_result)

    def close(self):
        self.closed = True


def test_cron_advisory_lock_releases_the_same_key():
    conn = _PgConnection(True)
    with try_cron_advisory_lock(connect=lambda: conn) as locked:
        assert locked is True
    assert "pg_try_advisory_lock" in conn.calls[0][0]
    assert "pg_advisory_unlock" in conn.calls[1][0]
    assert conn.calls[0][1] == conn.calls[1][1]
    assert conn.closed is True


def test_exhibition_signal_refresh_does_not_run_when_shared_lock_is_busy(monkeypatch):
    class _BusyLock:
        def __enter__(self):
            return False

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(exhibition, "try_cron_advisory_lock", lambda **_kwargs: _BusyLock())
    monkeypatch.setattr(
        exhibition,
        "_refresh_market_signals_if_needed_locked",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not overlap")),
    )

    result = exhibition.refresh_market_signals_if_needed(
        "2026-08-19",
        {"beforeinfo_rows": 6},
        {"refreshed": 1},
    )
    assert result["reason"] == "exhibition-signal-lock-busy"
    assert result["refresh_scope"] == "skipped"
