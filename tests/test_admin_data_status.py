import os
import sqlite3
from pathlib import Path

os.environ["DATABASE_URL"] = ""

from src.web import app as web_app


def test_admin_data_status_labels_match_render_schedules():
    source = Path(web_app.__file__).read_text(encoding="utf-8")

    assert '"毎日 04:00 JST"' in source
    assert '"08:00〜22:59 JST・5分ごと"' in source


class _ConnCtx:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self._conn

    def __exit__(self, exc_type, exc, tb):
        return False


def _prepare_db():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE races (
          race_id TEXT PRIMARY KEY,
          race_date TEXT,
          stadium_number INTEGER,
          race_number INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE page_html_cache (
          cache_key TEXT PRIMARY KEY,
          html TEXT,
          updated_at REAL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE task_runs (
          task_name TEXT NOT NULL,
          run_date TEXT NOT NULL,
          status TEXT NOT NULL,
          run_count INTEGER NOT NULL DEFAULT 0,
          started_at TEXT,
          finished_at TEXT,
          success_at TEXT,
          trigger TEXT,
          detail TEXT,
          PRIMARY KEY (task_name, run_date)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE system_status (
          check_name TEXT NOT NULL,
          check_date TEXT NOT NULL,
          status TEXT NOT NULL,
          message TEXT,
          detail_json TEXT,
          checked_at TEXT,
          PRIMARY KEY (check_name, check_date)
        )
        """
    )
    return conn


def test_admin_data_status_snapshot_counts_expected_caches(monkeypatch):
    conn = _prepare_db()
    target_date = "2026-08-04"
    races = [("202608041001", target_date, 10, 1), ("202608041002", target_date, 10, 2)]
    conn.executemany("INSERT INTO races VALUES (?, ?, ?, ?)", races)

    page_keys = [
        web_app._race_detail_page_cache_key("202608041001"),
        web_app._race_detail_page_cache_key("202608041002"),
    ]
    motor_keys = [
        f"motor_history_v9:202608041001:{boat}" for boat in range(1, 7)
    ] + [
        f"motor_history_v9:202608041002:{boat}" for boat in range(1, 6)
    ]
    racer_keys = [
        f"racer_detail:202608041001:{boat}" for boat in range(1, 7)
    ] + [
        f"racer_detail:202608041002:{boat}" for boat in range(1, 7)
    ]
    for key in page_keys + motor_keys + racer_keys:
        conn.execute(
            "INSERT INTO page_html_cache(cache_key, html, updated_at) VALUES (?, 'x', 1.0)",
            (key,),
        )
    conn.execute(
        """
        INSERT INTO task_runs(task_name, run_date, status, run_count, started_at, finished_at, success_at, trigger, detail)
        VALUES (?, ?, 'success', 1, '2026-08-04T07:00:00', '2026-08-04T07:05:00', '2026-08-04T07:05:00', 'render-detail-prewarm', ?)
        """,
        ("render_race_detail_all", target_date, '{"races":2,"racer":12,"motor":11,"pages":2,"failed":0}'),
    )
    conn.execute(
        """
        INSERT INTO system_status(check_name, check_date, status, message, detail_json, checked_at)
        VALUES (?, ?, 'ok', 'detail cache ok', '{}', '2026-08-04T07:06:00')
        """,
        ("post_run_detail_cache", target_date),
    )
    conn.execute(
        """
        INSERT INTO system_status(check_name, check_date, status, message, detail_json, checked_at)
        VALUES (?, ?, 'error', 'motor cache missing 1', '{}', '2026-08-04T07:06:00')
        """,
        ("post_run_motor_cache", target_date),
    )
    conn.commit()

    monkeypatch.setattr(web_app, "db_connect", lambda: _ConnCtx(conn))

    snapshot = web_app._admin_data_status_snapshot(target_date)
    items = {item["slug"]: item for item in snapshot["items"]}

    assert snapshot["race_count"] == 2
    assert items["race_detail"]["present_count"] == 2
    assert items["race_detail"]["missing_count"] == 0
    assert items["race_detail"]["status"] == "healthy"
    assert items["motor_history"]["present_count"] == 11
    assert items["motor_history"]["missing_count"] == 1
    assert items["motor_history"]["status"] == "error"
    assert items["racer_detail"]["present_count"] == 12
    assert items["racer_detail"]["status"] == "healthy"


def test_admin_data_status_race_detail_partial_html_cache_is_warning_when_rows_are_ok(monkeypatch):
    conn = _prepare_db()
    target_date = "2026-08-04"
    races = [("202608041001", target_date, 10, 1), ("202608041002", target_date, 10, 2)]
    conn.executemany("INSERT INTO races VALUES (?, ?, ?, ?)", races)

    conn.execute(
        "INSERT INTO page_html_cache(cache_key, html, updated_at) VALUES (?, 'x', 1.0)",
        (web_app._race_detail_page_cache_key("202608041001"),),
    )
    for key in [f"motor_history_v9:202608041001:{boat}" for boat in range(1, 7)]:
        conn.execute(
            "INSERT INTO page_html_cache(cache_key, html, updated_at) VALUES (?, 'x', 1.0)",
            (key,),
        )
    for key in [f"racer_detail:202608041001:{boat}" for boat in range(1, 7)]:
        conn.execute(
            "INSERT INTO page_html_cache(cache_key, html, updated_at) VALUES (?, 'x', 1.0)",
            (key,),
        )
    conn.execute(
        """
        INSERT INTO task_runs(task_name, run_date, status, run_count, started_at, finished_at, success_at, trigger, detail)
        VALUES (?, ?, 'success', 1, '2026-08-04T07:00:00', '2026-08-04T07:05:00', '2026-08-04T07:05:00', 'render-detail-prewarm', ?)
        """,
        ("render_race_detail_all", target_date, '{"races":2,"failed":0}'),
    )
    conn.execute(
        """
        INSERT INTO system_status(check_name, check_date, status, message, detail_json, checked_at)
        VALUES (?, ?, 'ok', 'detail cache ok', '{}', '2026-08-04T07:06:00')
        """,
        ("post_run_detail_cache", target_date),
    )
    conn.execute(
        """
        INSERT INTO system_status(check_name, check_date, status, message, detail_json, checked_at)
        VALUES (?, ?, 'ok', 'detail rows ok', '{}', '2026-08-04T07:06:00')
        """,
        ("post_run_detail_rows", target_date),
    )
    conn.commit()

    monkeypatch.setattr(web_app, "db_connect", lambda: _ConnCtx(conn))

    snapshot = web_app._admin_data_status_snapshot(target_date)
    items = {item["slug"]: item for item in snapshot["items"]}

    assert items["race_detail"]["present_count"] == 1
    assert items["race_detail"]["missing_count"] == 1
    assert items["race_detail"]["status"] == "warning"
    assert "HTML" in items["race_detail"]["status_hint"]


def test_admin_data_status_partial_counts_are_warning_while_morning_build_is_running(monkeypatch):
    conn = _prepare_db()
    target_date = "2026-08-11"
    races = [("202608111001", target_date, 10, 1), ("202608111002", target_date, 10, 2)]
    conn.executemany("INSERT INTO races VALUES (?, ?, ?, ?)", races)

    conn.execute(
        "INSERT INTO page_html_cache(cache_key, html, updated_at) VALUES (?, 'x', 1.0)",
        (web_app._race_detail_page_cache_key("202608111001"),),
    )
    for key in [f"motor_history_v9:202608111001:{boat}" for boat in range(1, 4)]:
        conn.execute(
            "INSERT INTO page_html_cache(cache_key, html, updated_at) VALUES (?, 'x', 1.0)",
            (key,),
        )
    for key in [f"racer_detail:202608111001:{boat}" for boat in range(1, 4)]:
        conn.execute(
            "INSERT INTO page_html_cache(cache_key, html, updated_at) VALUES (?, 'x', 1.0)",
            (key,),
        )
    conn.execute(
        """
        INSERT INTO task_runs(task_name, run_date, status, run_count, started_at, finished_at, success_at, trigger, detail)
        VALUES (?, ?, 'running', 1, '2026-08-11T07:00:00', NULL, NULL, 'render-detail-prewarm', ?)
        """,
        ("render_race_detail_all", target_date, '{"races":2,"pages":1,"motor":3,"racer":3,"failed":0}'),
    )
    conn.commit()

    monkeypatch.setattr(web_app, "db_connect", lambda: _ConnCtx(conn))

    snapshot = web_app._admin_data_status_snapshot(target_date)
    items = {item["slug"]: item for item in snapshot["items"]}

    assert items["race_detail"]["status"] == "warning"
    assert items["race_detail"]["status_hint"]
    assert items["motor_history"]["status"] == "warning"
    assert items["racer_detail"]["status"] == "warning"


def test_admin_data_status_page_renders_for_admin(monkeypatch):
    conn = _prepare_db()
    monkeypatch.setattr(web_app, "db_connect", lambda: _ConnCtx(conn))
    web_app.invalidate_cache()

    app = web_app.create_app()
    app.config.update(TESTING=True, SECRET_KEY="test")
    client = app.test_client()
    with client.session_transaction() as session:
        session["is_member"] = True
        session["role"] = "admin"
        session["auth_provider"] = "local"

    response = client.get("/admin/data-status?date=2026-08-04")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "cron / データ取得状況" in html
    assert "レース詳細HTML" in html
    assert "取得状況を見る" not in html
