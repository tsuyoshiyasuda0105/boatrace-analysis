import sqlite3
import sys
import types
from pathlib import Path


if "requests" not in sys.modules:
    requests_stub = types.ModuleType("requests")
    requests_exceptions = types.ModuleType("requests.exceptions")

    class _RequestException(Exception):
        pass

    requests_stub.RequestException = _RequestException
    requests_exceptions.ConnectionError = _RequestException
    requests_exceptions.Timeout = _RequestException
    requests_exceptions.RequestException = _RequestException
    requests_stub.exceptions = requests_exceptions
    requests_stub.get = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network not expected"))  # type: ignore[attr-defined]
    sys.modules["requests"] = requests_stub
    sys.modules["requests.exceptions"] = requests_exceptions

if "src.parsers.result_html" not in sys.modules:
    result_html_stub = types.ModuleType("src.parsers.result_html")
    result_html_stub.parse_result_html = lambda html: None  # type: ignore[attr-defined]
    sys.modules["src.parsers.result_html"] = result_html_stub


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


from scripts import poll_results


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
        CREATE TABLE races (
          race_id TEXT PRIMARY KEY,
          race_date TEXT NOT NULL,
          stadium_number INTEGER NOT NULL,
          race_number INTEGER NOT NULL,
          race_closed_at TEXT
        );
        CREATE TABLE race_entries (
          race_id TEXT NOT NULL,
          boat_number INTEGER NOT NULL
        );
        CREATE TABLE race_results (
          race_id TEXT NOT NULL,
          boat_number INTEGER NOT NULL,
          finishing_position INTEGER
        );
        """
    )
    return conn


def test_missing_closed_result_race_ids_obeys_grace_and_completion():
    conn = _conn()
    conn.executemany(
        "INSERT INTO races (race_id, race_date, stadium_number, race_number, race_closed_at) VALUES (?, ?, ?, ?, ?)",
        [
            ("20260810-01-01", "2026-08-10", 1, 1, "2026-08-10 18:00:00"),
            ("20260810-01-02", "2026-08-10", 1, 2, "2026-08-10 23:50:00"),
            ("20260810-01-03", "2026-08-10", 1, 3, "2026-08-10 18:10:00"),
        ],
    )
    conn.executemany(
        "INSERT INTO race_entries (race_id, boat_number) VALUES (?, ?)",
        [(race_id, boat) for race_id in ("20260810-01-01", "20260810-01-02", "20260810-01-03") for boat in range(1, 7)],
    )
    conn.executemany(
        "INSERT INTO race_results (race_id, boat_number, finishing_position) VALUES (?, ?, ?)",
        [("20260810-01-03", boat, boat) for boat in range(1, 7)],
    )

    missing = poll_results._missing_closed_result_race_ids(conn, poll_results.date(2026, 8, 10))

    assert "20260810-01-01" in missing
    assert "20260810-01-02" not in missing
    assert "20260810-01-03" not in missing
