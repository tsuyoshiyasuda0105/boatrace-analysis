import importlib.util
from datetime import date, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_poll_results():
    spec = importlib.util.spec_from_file_location(
        "poll_results",
        ROOT / "scripts" / "poll_results.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _RowsResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, sql, params=()):
        assert params == ("2026-08-08",)
        return _RowsResult(self._rows)


def test_parse_closed_at_normalizes_naive_and_utc_values_to_jst():
    mod = _load_poll_results()

    naive = mod._parse_closed_at("2026-08-08 16:30:00")
    aware = mod._parse_closed_at("2026-08-08T07:30:00+00:00")

    assert naive is not None
    assert aware is not None
    assert naive.tzinfo is not None
    assert aware.tzinfo is not None
    assert naive.utcoffset() == aware.utcoffset()
    assert naive.hour == 16
    assert aware.hour == 16


def test_count_openapi_shell_races_handles_mixed_timezone_values(monkeypatch):
    mod = _load_poll_results()
    conn = _Conn(
        [
            ("20260808-01-01", "2026-08-08 16:30:00"),
            ("20260808-01-02", "2026-08-08T07:31:00+00:00"),
            ("20260808-01-03", "2026-08-08T16:38:00+09:00"),
        ]
    )
    monkeypatch.setattr(
        mod,
        "_now_jst",
        lambda: datetime(2026, 8, 8, 16, 40, tzinfo=mod.JST),
    )

    pending = mod._count_openapi_shell_races(conn, date(2026, 8, 8))

    assert pending == 2
