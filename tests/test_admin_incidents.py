from src.notifications import incident_ledger
from src.web import app as web_app


def _admin_client():
    app = web_app.create_app()
    app.config.update(TESTING=True, SECRET_KEY="test")
    client = app.test_client()
    with client.session_transaction() as session:
        session["is_member"] = True
        session["role"] = "admin"
        session["auth_provider"] = "local"
    return client


def test_admin_incidents_is_authorized_lightweight_list(monkeypatch):
    calls = []
    monkeypatch.setattr(
        incident_ledger,
        "list_incidents",
        lambda **kwargs: calls.append(kwargs) or [{
            "incident_id": "boatrace-id",
            "app_name": "boatrace",
            "last_seen_at": "2026-08-16T10:00:00",
            "category": "watchdog",
            "source": "pool",
            "title": "pool issue",
            "severity": "error",
            "occurrence_count": 3,
            "status": "investigating",
            "response_note": "リンが調査中",
            "handled_by": "rin",
            "resolved_at": None,
        }],
    )
    response = _admin_client().get("/admin/incidents?status=investigating&limit=20")
    assert response.status_code == 200
    assert calls == [{"status": "investigating", "limit": 20}]
    html = response.get_data(as_text=True)
    assert "インシデント台帳" in html
    assert "リンが調査中" in html


def test_admin_incidents_rejects_non_admin_and_invalid_filters(monkeypatch):
    monkeypatch.setattr(incident_ledger, "list_incidents", lambda **_kwargs: [])
    app = web_app.create_app()
    app.config.update(TESTING=True, SECRET_KEY="test")
    assert app.test_client().get("/admin/incidents").status_code in {302, 403}
    assert _admin_client().get("/admin/incidents?status=unknown").status_code == 400
