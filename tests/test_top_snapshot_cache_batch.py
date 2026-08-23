import inspect

import pytest

from src.web import app as web_app


class _Rows:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _PageCacheConnection:
    _kind = "postgres"

    def __init__(self, rows, calls):
        self._rows = rows
        self._calls = calls

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        keys = tuple(params or ())
        self._calls.append((normalized, keys))
        if "WHERE cache_key IN (" in normalized:
            return _Rows(
                (key, *self._rows[key]) for key in keys if key in self._rows
            )
        key = keys[0]
        row = self._rows.get(key)
        return _Rows([row] if row is not None else [])

    def close(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


@pytest.fixture(autouse=True)
def _clear_page_cache_context():
    web_app._PAGE_HTML_MEM_CACHE.clear()
    token = web_app._RACE_DETAIL_PREWARM_CONTEXT.set(None)
    yield
    web_app._RACE_DETAIL_PREWARM_CONTEXT.reset(token)
    web_app._PAGE_HTML_MEM_CACHE.clear()


@pytest.mark.parametrize("max_age_sec", [100, None])
def test_bulk_page_cache_matches_single_key_fresh_and_stale_reads(
    monkeypatch,
    max_age_sec,
):
    rows = {
        "db-fresh": ("db-fresh-html", 950.0),
        "db-stale": ("db-stale-html", 800.0),
        "mem-stale-db-fresh": ("replacement-html", 980.0),
        "empty": ("", 990.0),
    }
    initial_memory = {
        "mem-fresh": (960.0, "mem-fresh-html"),
        "mem-stale-db-fresh": (800.0, "old-html"),
    }
    keys = [
        "mem-fresh",
        "mem-stale-db-fresh",
        "db-fresh",
        "db-stale",
        "empty",
        "missing",
    ]
    calls = []
    monkeypatch.setattr(web_app.time, "time", lambda: 1000.0)
    monkeypatch.setattr(web_app, "_ensure_page_html_cache_table", lambda: None)
    monkeypatch.setattr(
        web_app,
        "db_connect",
        lambda: _PageCacheConnection(rows, calls),
    )

    web_app._PAGE_HTML_MEM_CACHE.update(initial_memory)
    if max_age_sec is None:
        expected = {
            key: value
            for key in keys
            if (value := web_app._read_page_html_cache_stale(key)) is not None
        }
    else:
        expected = {
            key: value
            for key in keys
            if (value := web_app._read_page_html_cache(key, max_age_sec)) is not None
        }
    expected_memory = dict(web_app._PAGE_HTML_MEM_CACHE)

    web_app._PAGE_HTML_MEM_CACHE.clear()
    web_app._PAGE_HTML_MEM_CACHE.update(initial_memory)
    actual = web_app._read_page_html_caches(keys, max_age_sec)

    assert actual == expected
    assert web_app._PAGE_HTML_MEM_CACHE == expected_memory


def test_bulk_page_cache_splits_more_than_900_keys(monkeypatch):
    calls = []
    rows = {}
    keys = [f"cache-{index}" for index in range(901)]
    monkeypatch.setattr(web_app, "_ensure_page_html_cache_table", lambda: None)
    monkeypatch.setattr(
        web_app,
        "db_connect",
        lambda: _PageCacheConnection(rows, calls),
    )

    assert web_app._read_page_html_caches(keys) == {}

    in_calls = [params for sql, params in calls if "WHERE cache_key IN (" in sql]
    assert [len(params) for params in in_calls] == [900, 1]


def test_top_snapshot_cache_reads_are_set_based():
    builder_source = inspect.getsource(web_app._build_top_page_snapshot_payload)
    badges_source = inspect.getsource(web_app._race_grid_badges_payload)
    hydration_source = inspect.getsource(web_app._hydrate_market_race_badges)

    assert "_read_json_caches_stale(" in builder_source
    assert "_read_json_cache_stale(" not in badges_source
    assert "_race_detail_tag_snapshot(" not in hydration_source
    assert "_read_json_caches_stale(" in hydration_source
