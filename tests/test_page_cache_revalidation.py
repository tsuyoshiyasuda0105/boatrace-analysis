import json
import os

os.environ["DATABASE_URL"] = ""

from src.web import app as web_app  # noqa: E402


class _RowResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _Connection:
    def __init__(self, row=None, error=None):
        self._row = row
        self._error = error

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, *_args, **_kwargs):
        if self._error is not None:
            raise self._error
        return _RowResult(self._row)


def _snapshot(target_date: str, *, empty: bool) -> dict:
    return {
        "version": web_app.TOP_PAGE_SNAPSHOT_VERSION,
        "date": target_date,
        "empty": empty,
        "stadium_groups": [] if empty else [{"stadium_name": "桐生"}],
    }


def _prime_memory(monkeypatch, key: str, payload: dict, *, cached_at: float) -> None:
    raw = json.dumps(payload, ensure_ascii=False)
    monkeypatch.setitem(web_app._PAGE_HTML_MEM_CACHE, key, (cached_at - 10, raw))
    cache_times = getattr(web_app, "_PAGE_HTML_MEM_CACHE_AT", None)
    if cache_times is not None:
        monkeypatch.setitem(cache_times, key, cached_at)


def _db_row(payload: dict, *, updated_at: float = 200.0):
    return (json.dumps(payload, ensure_ascii=False), updated_at)


def test_memory_entry_within_revalidation_interval_avoids_db(monkeypatch):
    key = "revalidation:within"
    now = 100.0
    monkeypatch.setattr(web_app.time, "time", lambda: now)
    web_app._mem_cache_put(key, 90.0, '{"value":"memory"}')
    monkeypatch.setattr(
        web_app,
        "_ensure_page_html_cache_table",
        lambda: (_ for _ in ()).throw(AssertionError("fresh memory must not read DB")),
    )

    assert web_app._read_json_cache_stale(key) == {"value": "memory"}


def test_expired_memory_entry_revalidates_and_returns_db_value(monkeypatch):
    key = "revalidation:expired"
    now = 100.0
    monkeypatch.setattr(web_app.time, "time", lambda: now)
    web_app._mem_cache_put(key, 90.0, '{"value":"old"}')
    now = 161.0
    monkeypatch.setattr(
        web_app,
        "db_connect",
        lambda: _Connection(_db_row({"value": "new"})),
    )

    assert web_app._read_json_cache_stale(key) == {"value": "new"}


def test_empty_snapshot_recovers_after_database_is_updated(monkeypatch):
    target_date = "2026-09-01"
    key = web_app._top_page_snapshot_cache_key(target_date)
    now = 100.0
    monkeypatch.setattr(web_app.time, "time", lambda: now)
    _prime_memory(monkeypatch, key, _snapshot(target_date, empty=True), cached_at=now)
    now = 161.0
    monkeypatch.setattr(
        web_app,
        "db_connect",
        lambda: _Connection(_db_row(_snapshot(target_date, empty=False))),
    )

    assert web_app._read_json_cache_stale(key)["empty"] is False


def test_today_empty_snapshot_bypasses_fresh_memory(monkeypatch):
    target_date = "2026-09-01"
    key = web_app._top_page_snapshot_cache_key(target_date)
    now = 100.0
    monkeypatch.setattr(web_app.time, "time", lambda: now)
    monkeypatch.setattr(web_app, "_today_jst_iso", lambda: target_date)
    _prime_memory(monkeypatch, key, _snapshot(target_date, empty=True), cached_at=now)
    db_reads = []

    def _connect():
        db_reads.append(key)
        return _Connection(_db_row(_snapshot(target_date, empty=False)))

    monkeypatch.setattr(web_app, "_ensure_page_html_cache_table", lambda: None)
    monkeypatch.setattr(web_app, "db_connect", _connect)

    assert web_app._read_top_page_snapshot(target_date)["empty"] is False
    assert db_reads == [key]


def test_revalidation_db_error_returns_stale_memory(monkeypatch):
    key = "revalidation:error"
    now = 100.0
    monkeypatch.setattr(web_app.time, "time", lambda: now)
    web_app._mem_cache_put(key, 90.0, '{"value":"old"}')
    now = 161.0
    monkeypatch.setattr(
        web_app,
        "db_connect",
        lambda: _Connection(error=RuntimeError("database unavailable")),
    )

    assert web_app._read_json_cache_stale(key) == {"value": "old"}


def test_revalidation_missing_db_row_returns_stale_memory(monkeypatch):
    key = "revalidation:missing"
    now = 100.0
    monkeypatch.setattr(web_app.time, "time", lambda: now)
    web_app._mem_cache_put(key, 90.0, '{"value":"old"}')
    now = 161.0
    monkeypatch.setattr(web_app, "db_connect", lambda: _Connection(None))

    assert web_app._read_json_cache_stale(key) == {"value": "old"}


def test_negative_entry_expires_after_ten_seconds(monkeypatch):
    key = "revalidation:negative"
    now = 100.0
    monkeypatch.setattr(web_app.time, "time", lambda: now)
    rows = {}
    monkeypatch.setattr(web_app, "_read_page_html_cache_rows", lambda *_a, **_k: rows)

    assert web_app._read_json_caches_stale([key]) == {key: {}}

    now = 111.0
    rows[key] = ('{"value":"generated"}', 110.0)

    assert web_app._read_json_caches_stale([key]) == {key: {"value": "generated"}}


def test_invalidate_cache_clears_values_and_memoized_times(monkeypatch):
    key = "revalidation:invalidate"
    monkeypatch.setattr(web_app.time, "time", lambda: 100.0)
    web_app._mem_cache_put(key, 90.0, '{"value":"cached"}')

    web_app.invalidate_cache()

    assert web_app._PAGE_HTML_MEM_CACHE == {}
    assert web_app._PAGE_HTML_MEM_CACHE.memoized_at(key) is None


def test_direct_assignment_is_revalidated_after_interval(monkeypatch):
    key = "revalidation:direct-assignment"
    now = 100.0
    monkeypatch.setattr(web_app.time, "time", lambda: now)
    web_app._PAGE_HTML_MEM_CACHE[key] = (90.0, '{"value":"old"}')
    now = 161.0
    monkeypatch.setattr(
        web_app,
        "db_connect",
        lambda: _Connection(_db_row({"value": "new"})),
    )

    assert web_app._read_json_cache_stale(key) == {"value": "new"}


def test_clear_removes_memoized_time(monkeypatch):
    key = "revalidation:clear"
    monkeypatch.setattr(web_app.time, "time", lambda: 100.0)
    web_app._PAGE_HTML_MEM_CACHE[key] = (90.0, '{"value":"cached"}')

    web_app._PAGE_HTML_MEM_CACHE.clear()

    assert web_app._PAGE_HTML_MEM_CACHE.memoized_at(key) is None


def test_update_records_time_and_entry_is_revalidated(monkeypatch):
    key = "revalidation:update"
    now = 100.0
    monkeypatch.setattr(web_app.time, "time", lambda: now)
    web_app._PAGE_HTML_MEM_CACHE.update({key: (90.0, '{"value":"old"}')})
    now = 161.0
    monkeypatch.setattr(
        web_app,
        "db_connect",
        lambda: _Connection(_db_row({"value": "new"})),
    )

    assert web_app._read_json_cache_stale(key) == {"value": "new"}


def test_corrupt_stale_json_and_db_error_returns_none(monkeypatch):
    key = "revalidation:corrupt"
    now = 100.0
    monkeypatch.setattr(web_app.time, "time", lambda: now)
    web_app._PAGE_HTML_MEM_CACHE[key] = (90.0, "{broken")
    now = 161.0
    monkeypatch.setattr(
        web_app,
        "db_connect",
        lambda: _Connection(error=RuntimeError("database unavailable")),
    )

    assert web_app._read_json_cache_stale(key) is None


def test_expired_db_row_returns_stale_memory_html(monkeypatch):
    key = "revalidation:expired-db-html"
    now = 100.0
    monkeypatch.setattr(web_app.time, "time", lambda: now)
    web_app._PAGE_HTML_MEM_CACHE[key] = (90.0, "memory html")
    now = 161.0
    monkeypatch.setattr(web_app, "_ensure_page_html_cache_table", lambda: None)
    monkeypatch.setattr(web_app, "db_connect", lambda: _Connection(("db html", 1.0)))

    assert web_app._read_page_html_cache(key, max_age_sec=100) == "memory html"
