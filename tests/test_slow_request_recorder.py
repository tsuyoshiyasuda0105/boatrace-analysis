"""遅いリクエストの現行犯記録の回帰テスト。

- 閾値超過リクエストがバッファされ、フラッシュで system_status に merge される
- 上位 _SLOW_REQUEST_KEEP 件のみ保持・件数は累積
- フラッシュ間隔内は DB へ書かない (渋滞への加担防止)
"""
import json
import sqlite3

import pytest

from src.web import app as app_module


@pytest.fixture
def status_db(tmp_path, monkeypatch):
    db_path = tmp_path / "status.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE system_status (
              check_name TEXT NOT NULL,
              check_date TEXT NOT NULL,
              status TEXT NOT NULL,
              message TEXT,
              detail_json TEXT,
              checked_at TEXT NOT NULL,
              PRIMARY KEY (check_name, check_date)
            )
            """
        )
    monkeypatch.setattr(app_module, "db_connect", lambda *a, **k: sqlite3.connect(db_path))
    app_module._SLOW_REQUESTS.clear()
    monkeypatch.setattr(app_module, "_slow_requests_last_flush", 0.0)
    yield db_path
    app_module._SLOW_REQUESTS.clear()


def _entry(path="/races", elapsed=4200.0, date="2026-08-14"):
    return {
        "at": f"{date}T12:00:00",
        "date": date,
        "method": "GET",
        "path": path,
        "query": "",
        "status": 200,
        "elapsed_ms": elapsed,
        "db_queries": 3,
        "db_time_ms": 3900.0,
    }


def _load_row(db_path, date="2026-08-14"):
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            "SELECT status, message, detail_json FROM system_status "
            "WHERE check_name=? AND check_date=?",
            (app_module.SLOW_REQUEST_CHECK, date),
        ).fetchone()


def test_flush_writes_merged_row(status_db):
    app_module._record_slow_request(_entry(elapsed=4200.0))
    row = _load_row(status_db)
    assert row is not None
    status, message, detail_json = row
    assert status == "warning"
    assert "遅いリクエスト 1件" in message
    detail = json.loads(detail_json)
    assert detail["count"] == 1
    assert detail["requests"][0]["path"] == "/races"


def test_flush_interval_batches_writes(status_db, monkeypatch):
    # 1回目のフラッシュ後、間隔内の記録は DB に書かれずバッファされる
    app_module._record_slow_request(_entry(elapsed=4000.0))
    app_module._record_slow_request(_entry(path="/race/x", elapsed=9000.0))
    detail = json.loads(_load_row(status_db)[2])
    assert detail["count"] == 1  # 2件目は間隔内なので未書込
    assert len(app_module._SLOW_REQUESTS) == 1

    # 間隔経過後の3件目で、2件目・3件目がまとめて merge される
    monkeypatch.setattr(app_module, "_slow_requests_last_flush", 0.0)
    app_module._record_slow_request(_entry(path="/member/strategy", elapsed=6000.0))
    detail = json.loads(_load_row(status_db)[2])
    assert detail["count"] == 3
    paths = [r["path"] for r in detail["requests"]]
    assert paths[0] == "/race/x"  # 最遅が先頭
    assert set(paths) == {"/races", "/race/x", "/member/strategy"}


def test_keeps_only_top_slowest(status_db, monkeypatch):
    for i in range(app_module._SLOW_REQUEST_KEEP + 10):
        monkeypatch.setattr(app_module, "_slow_requests_last_flush", 0.0)
        app_module._record_slow_request(_entry(path=f"/p{i}", elapsed=3000.0 + i))
    detail = json.loads(_load_row(status_db)[2])
    assert detail["count"] == app_module._SLOW_REQUEST_KEEP + 10
    assert len(detail["requests"]) == app_module._SLOW_REQUEST_KEEP
    # 最遅 (elapsed が最大 = 最後に入れたもの) が保持されている
    assert detail["requests"][0]["path"] == f"/p{app_module._SLOW_REQUEST_KEEP + 9}"


def test_threshold_env_parse(monkeypatch):
    monkeypatch.setenv("BOATRACE_SLOW_REQUEST_MS", "5000")
    assert app_module._slow_request_threshold_ms() == 5000.0
    monkeypatch.setenv("BOATRACE_SLOW_REQUEST_MS", "bogus")
    assert app_module._slow_request_threshold_ms() == 3000.0


def test_slow_request_rows_merges_buffer(status_db, monkeypatch):
    app_module._record_slow_request(_entry(elapsed=4000.0))          # flushed
    app_module._record_slow_request(_entry(path="/race/y", elapsed=8000.0))  # buffered
    check = {"detail_json": json.loads(_load_row(status_db)[2])}
    rows = app_module._slow_request_rows(check)
    assert [r["path"] for r in rows][:2] == ["/race/y", "/races"]


def test_recorder_never_raises_on_db_failure(monkeypatch):
    def _broken(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(app_module, "db_connect", _broken)
    app_module._SLOW_REQUESTS.clear()
    monkeypatch.setattr(app_module, "_slow_requests_last_flush", 0.0)
    # 例外を外へ出さない
    app_module._record_slow_request(_entry())
    app_module._SLOW_REQUESTS.clear()
