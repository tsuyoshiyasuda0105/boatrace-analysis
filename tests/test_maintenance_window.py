from datetime import datetime

from src.web import app as app_module


def test_maintenance_window_boundaries():
    assert app_module._maintenance_window_active(datetime(2026, 8, 13, 4, 0, tzinfo=app_module.JST))
    assert app_module._maintenance_window_active(datetime(2026, 8, 13, 6, 59, tzinfo=app_module.JST))
    assert not app_module._maintenance_window_active(datetime(2026, 8, 13, 7, 0, tzinfo=app_module.JST))


def test_production_maintenance_page_is_static_and_health_remains_available(monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setattr(app_module, "_ensure_db_initialized", lambda: None)
    monkeypatch.setattr(app_module, "_maintenance_window_active", lambda: True)
    app = app_module.create_app()
    app.config.update(TESTING=True)
    client = app.test_client()

    response = client.get("/races?date=2026-08-13")
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "300"
    assert "データ更新中" in response.get_data(as_text=True)
    assert client.get("/healthz").status_code == 200
    assert client.get("/static/maintenance.html").status_code == 200
