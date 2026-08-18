from datetime import datetime
import json

from src.web import app as app_module


def test_maintenance_window_boundaries():
    assert app_module._maintenance_window_active(datetime(2026, 8, 13, 4, 0, tzinfo=app_module.JST))
    assert app_module._maintenance_window_active(datetime(2026, 8, 13, 6, 59, tzinfo=app_module.JST))
    assert not app_module._maintenance_window_active(datetime(2026, 8, 13, 7, 0, tzinfo=app_module.JST))


def test_production_maintenance_page_is_static_and_health_remains_available(monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setattr(app_module.config, "WEB_SESSION_SECRET", "maintenance-test-secret")
    monkeypatch.setattr(app_module.config, "WEB_MEMBER_PASSWORD", "maintenance-test-password")
    monkeypatch.setattr(app_module, "_ensure_db_initialized", lambda: None)
    monkeypatch.setattr(app_module, "_maintenance_window_active", lambda: True)
    app = app_module.create_app()
    app.config.update(TESTING=True)
    client = app.test_client()

    monkeypatch.setattr(app_module, "_read_top_page_snapshot", lambda _d: None)
    response = client.get("/races?date=2026-08-13")
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "300"
    assert "データ更新中" in response.get_data(as_text=True)
    assert client.get("/healthz").status_code == 200
    assert client.get("/static/maintenance.html").status_code == 200


def test_maintenance_window_keeps_login_routes_available(monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setattr(app_module.config, "WEB_SESSION_SECRET", "maintenance-test-secret")
    monkeypatch.setattr(app_module.config, "WEB_MEMBER_PASSWORD", "maintenance-test-password")
    monkeypatch.setattr(app_module, "_ensure_db_initialized", lambda: None)
    monkeypatch.setattr(app_module, "_maintenance_window_active", lambda: True)
    app = app_module.create_app()
    app.config.update(TESTING=True)
    client = app.test_client()

    # 認証系はメンテ時間帯でも締め出さない (「早朝ログインできない」対策)
    for path in ("/login", "/login-supabase", "/signup-supabase",
                 "/reset-password", "/logout"):
        status = client.get(path).status_code
        assert status != 503, f"{path} must stay reachable during maintenance"


def test_maintenance_window_serves_top_from_snapshot(monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setattr(app_module.config, "WEB_SESSION_SECRET", "maintenance-test-secret")
    monkeypatch.setattr(app_module.config, "WEB_MEMBER_PASSWORD", "maintenance-test-password")
    monkeypatch.setattr(app_module, "_ensure_db_initialized", lambda: None)
    monkeypatch.setattr(app_module, "_maintenance_window_active", lambda: True)
    app = app_module.create_app()
    app.config.update(TESTING=True)
    client = app.test_client()

    # スナップショットがあれば TOP は 503 にしない (ルート側が snapshot 優先描画)
    monkeypatch.setattr(
        app_module,
        "_read_top_page_snapshot",
        lambda _d: {"stadium_groups": [], "empty": True},
    )
    assert client.get("/races?date=2026-08-13").status_code != 503
    assert client.get("/?date=2026-08-13").status_code != 503

    # スナップショットが無ければ従来どおりメンテページで DB を守る
    monkeypatch.setattr(app_module, "_read_top_page_snapshot", lambda _d: None)
    assert client.get("/races?date=2026-08-13").status_code == 503


def test_preflight_gate_extension_is_hard_capped_at_0730(monkeypatch):
    class _Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, *_args):
            return self

        def fetchone(self):
            return (json.dumps({"gate": {"extend_maintenance": True}}),)

    monkeypatch.setattr(app_module, "_raw_db_connect", _Connection)
    app_module._PREFLIGHT_GATE_CACHE.update(
        {"date": None, "checked_monotonic": 0.0, "active": False}
    )

    assert app_module._maintenance_window_active(
        datetime(2026, 8, 13, 7, 0, tzinfo=app_module.JST)
    )
    assert app_module._maintenance_window_active(
        datetime(2026, 8, 13, 7, 29, tzinfo=app_module.JST)
    )
    assert not app_module._maintenance_window_active(
        datetime(2026, 8, 13, 7, 30, tzinfo=app_module.JST)
    )
    assert not app_module._maintenance_window_active(
        datetime(2026, 8, 13, 8, 0, tzinfo=app_module.JST)
    )


def test_preflight_gate_read_failure_publishes_fail_open(monkeypatch):
    def failed_connection():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(app_module, "_raw_db_connect", failed_connection)
    app_module._PREFLIGHT_GATE_CACHE.update(
        {"date": None, "checked_monotonic": 0.0, "active": False}
    )

    assert not app_module._maintenance_window_active(
        datetime(2026, 8, 13, 7, 10, tzinfo=app_module.JST)
    )


def test_preflight_extension_blocks_top_even_when_snapshot_exists(monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setattr(app_module.config, "WEB_SESSION_SECRET", "maintenance-test-secret")
    monkeypatch.setattr(app_module.config, "WEB_MEMBER_PASSWORD", "maintenance-test-password")
    monkeypatch.setattr(app_module, "_ensure_db_initialized", lambda: None)
    monkeypatch.setattr(app_module, "_maintenance_window_active", lambda: True)
    monkeypatch.setattr(app_module, "_preflight_gate_extension_active", lambda: True)
    monkeypatch.setattr(
        app_module,
        "_read_top_page_snapshot",
        lambda _date: {"stadium_groups": [], "empty": True},
    )
    app = app_module.create_app()
    app.config.update(TESTING=True)

    assert app.test_client().get("/?date=2026-08-13").status_code == 503
