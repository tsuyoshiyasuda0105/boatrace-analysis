from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3

import pytest

from scripts.export_kachisuji_slim_db import export_slim_db
from src.features.asof_builder import create_output_schema
from src.web import app as web_app


def _create_search_db(path: Path) -> Path:
    with sqlite3.connect(path) as connection:
        create_output_schema(connection)
        connection.execute(
            "CREATE TABLE racers ("
            "racer_number INTEGER PRIMARY KEY, name TEXT, name_kana TEXT)"
        )
        connection.execute(
            "CREATE INDEX idx_racers_name ON racers(name)"
        )
        connection.execute(
            "INSERT INTO racers VALUES (?, ?, ?)",
            (4320, "峰竜太", "ﾐﾈ ﾘｭｳﾀ"),
        )
        connection.execute(
            """
            INSERT INTO asof_race_features (
                race_id, race_date, asof_date, built_at, schema_version,
                jcd, race_no,
                result_sanrentan, payout_sanrentan,
                result_sanrentan_json, payout_sanrentan_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "20250101-01-01",
                "2025-01-01",
                "2024-12-31",
                "2025-01-02T00:00:00+00:00",
                10,
                1,
                1,
                "1-2-3",
                1230,
                '["1-2-3"]',
                '{"1-2-3":1230}',
            ),
        )
        connection.execute("CREATE TABLE unused_bulk_table (value TEXT)")
        connection.executemany(
            "INSERT INTO unused_bulk_table VALUES (?)",
            [("unused",), ("still unused",)],
        )
    return path


def _create_production_app(monkeypatch):
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("BOATRACE_TASK_TRIGGER", raising=False)
    monkeypatch.setattr(web_app, "_ensure_db_initialized", lambda: None)
    web_app.invalidate_cache()
    app = web_app.create_app(cached_predictions_only=True)
    app.config.update(TESTING=True, SECRET_KEY="kachisuji-step22-test")
    return app


def _set_role(client, role: str) -> None:
    with client.session_transaction() as session:
        session["is_member"] = role in {"free_member", "paid_member", "admin"}
        session["role"] = role
        session["auth_provider"] = "test"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/kachisuji/api/search"),
        ("get", "/kachisuji/api/racers?q=峰"),
        ("get", "/kachisuji/api/strategies"),
        ("post", "/kachisuji/api/strategies"),
        ("get", "/kachisuji/api/strategies/performance"),
        ("get", "/kachisuji/api/strategies/1/performance"),
        ("delete", "/kachisuji/api/strategies/1"),
        ("get", "/kachisuji/api/strategies/1/matches?date=2025-01-01"),
        ("get", "/kachisuji/api/matches?date=2025-01-01"),
    ],
)
def test_kachisuji_apis_reuse_member_only_api(monkeypatch, method: str, path: str):
    app = _create_production_app(monkeypatch)
    response = getattr(app.test_client(), method)(path)

    assert response.status_code == 401
    assert response.get_json()["error"] == "unauthorized"


def test_kachisuji_page_reuses_login_and_paid_member_checks(monkeypatch, tmp_path: Path):
    search_db = _create_search_db(tmp_path / "search.db")
    monkeypatch.setenv("KACHISUJI_DB", str(search_db))
    monkeypatch.setenv("KACHISUJI_STRATEGY_DB", str(tmp_path / "strategies.db"))
    app = _create_production_app(monkeypatch)

    anonymous = app.test_client().get("/kachisuji")
    assert anonymous.status_code == 302
    assert anonymous.headers["Location"].endswith("/login?next=/kachisuji")

    free_client = app.test_client()
    _set_role(free_client, "free_member")
    assert free_client.get("/kachisuji").status_code == 403
    free_api = free_client.post("/kachisuji/api/search", json={"fast": True})
    assert free_api.status_code == 403
    assert free_api.get_json()["error"] == "forbidden"

    paid_client = app.test_client()
    _set_role(paid_client, "paid_member")
    paid = paid_client.get("/kachisuji")
    html = paid.get_data(as_text=True)
    base_source = (
        Path(web_app.__file__).resolve().parent / "templates" / "base.html"
    ).read_text(encoding="utf-8")
    assert paid.status_code == 200
    assert "<title>バックテスト</title>" in html
    assert "{% block title %}競艇｜バックテストLAB{% endblock %}" in base_source
    assert "バックテスト" in html
    assert "競艇｜バックテストLAB" in html
    assert html.index("本日のレース</span>") < html.index("バックテスト</span>")
    assert html.index("バックテスト</span>") < html.index("プラン申込</span>")
    assert '/member/today-races' in html
    assert "公開ROI</span>" not in html
    assert 'href="/kachisuji/"' in html or 'href="/kachisuji"' in html
    kachisuji_rules = {
        rule.rule
        for rule in app.url_map.iter_rules()
        if rule.endpoint == "kachisuji.index"
    }
    assert kachisuji_rules == {"/kachisuji", "/kachisuji/"}
    assert 'id="venue" class="venue-grid"' in html
    assert html.count('<input type="checkbox" name="venue" value=') == 24
    assert '<select id="venue"' not in html
    assert "#venue input[name=\"venue\"]:checked" in html
    assert 'id="windDirection"' in html
    # Wind direction is enabled (course-relative frame); no longer disabled.
    assert 'aria-disabled="true"' not in html
    assert "準備中：会場ごとの水面向きを整備中" not in html
    assert html.count('<input type="checkbox" value="追い風">') == 1
    assert "#windDirection input:checked:not(:disabled)" in html
    assert html.index('class="lab-ad"') < html.index("★ マイ手法")
    assert "勝ち筋サーチ" not in html
    assert "/kachisuji/api/search" in html
    assert "/static/kachisuji.css" in html


def test_blueprint_search_strategy_match_and_racer_flow(monkeypatch, tmp_path: Path):
    search_db = _create_search_db(tmp_path / "search.db")
    strategy_db = tmp_path / "strategies.db"
    monkeypatch.setenv("KACHISUJI_DB", str(search_db))
    monkeypatch.setenv("KACHISUJI_STRATEGY_DB", str(strategy_db))
    app = _create_production_app(monkeypatch)
    client = app.test_client()
    _set_role(client, "paid_member")

    search = client.post("/kachisuji/api/search", json={"fast": True})
    assert search.status_code == 200
    assert search.get_json()["n"] == 1

    racers = client.get("/kachisuji/api/racers?q=峰")
    assert racers.status_code == 200
    assert racers.get_json() == [
        {"name": "峰竜太", "name_kana": "ﾐﾈ ﾘｭｳﾀ", "racer_number": 4320}
    ]

    saved = client.post(
        "/kachisuji/api/strategies",
        json={
            "name": "本番統合テスト",
            "conditions": {},
            "backtest": {"roi": 123.0, "n": 1},
        },
    )
    assert saved.status_code == 200
    strategy_id = saved.get_json()["id"]
    assert client.get("/kachisuji/api/strategies").get_json()[0]["id"] == strategy_id

    performance = client.get(
        f"/kachisuji/api/strategies/{strategy_id}/performance"
    )
    assert performance.status_code == 200
    assert performance.get_json()["strategy_id"] == strategy_id

    matched = client.get(
        f"/kachisuji/api/strategies/{strategy_id}/matches?date=2025-01-01"
    )
    assert matched.status_code == 200
    assert matched.get_json()["counts"]["matched"] == 1

    all_matches = client.get("/kachisuji/api/matches?date=2025-01-01")
    assert all_matches.status_code == 200
    assert all_matches.get_json()[0]["strategy_id"] == strategy_id

    deleted = client.delete(f"/kachisuji/api/strategies/{strategy_id}")
    assert deleted.status_code == 200
    assert deleted.get_json() == {"deactivated": True}


def test_missing_search_db_does_not_break_app_or_existing_routes(monkeypatch, tmp_path: Path):
    missing_db = tmp_path / "not-deployed.db"
    monkeypatch.setenv("KACHISUJI_DB", str(missing_db))
    monkeypatch.setenv("KACHISUJI_STRATEGY_DB", str(tmp_path / "strategies.db"))
    app = _create_production_app(monkeypatch)
    snapshot = {
        "version": web_app.TOP_PAGE_SNAPSHOT_VERSION,
        "date": "2026-08-17",
        "generated_at": "2026-08-17T12:00:00+09:00",
        "stadium_groups": [],
        "initial_market_signals": {
            "date": "2026-08-17",
            "signals": {},
            "race_badges": {},
            "accident_watch": {},
        },
        "empty": True,
    }
    monkeypatch.setattr(web_app, "_read_top_page_snapshot", lambda _date: snapshot)
    client = app.test_client()
    _set_role(client, "paid_member")

    assert client.get("/healthz").status_code == 200
    assert client.get("/races?date=2026-08-17").status_code == 200
    preparing = client.get("/kachisuji")
    assert preparing.status_code == 200
    assert "バックテストは準備中です" in preparing.get_data(as_text=True)
    unavailable = client.post("/kachisuji/api/search", json={"fast": True})
    assert unavailable.status_code == 503
    assert unavailable.get_json()["error"] == "kachisuji_unavailable"
    assert unavailable.get_json()["message"] == "バックテストは準備中です"
    assert not missing_db.exists()


def test_slim_export_copies_only_required_tables_indexes_and_verifies(tmp_path: Path):
    source = _create_search_db(tmp_path / "source.db")
    output = tmp_path / "slim.db"
    source_before = hashlib.sha256(source.read_bytes()).hexdigest()

    counts = export_slim_db(source, output, verify=True)

    assert counts == {"asof_race_features": 1, "racers": 1}
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_before
    with sqlite3.connect(output) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND sql IS NOT NULL"
            )
        }
        assert tables == {"asof_race_features", "racers"}
        assert indexes == {"idx_asof_race_features_date", "idx_racers_name"}
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"


def test_render_disk_definition_is_comment_only():
    source = Path("render.yaml").read_text(encoding="utf-8")

    assert "# disk:" in source
    assert "#   mountPath: /data" in source
    assert "#   sizeGB: 1" in source
    assert "# KACHISUJI_DB=/data/kachisuji_slim.db" in source
