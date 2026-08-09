from __future__ import annotations

from src.web import app as web_app


def test_race_detail_redirects_legacy_race_id_to_canonical():
    app = web_app.create_app()
    client = app.test_client()
    with client.session_transaction() as session:
        session["is_member"] = True

    response = client.get("/race/202608091412")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/race/20260809-14-12")


def test_motor_history_api_accepts_legacy_race_id(monkeypatch):
    web_app.invalidate_cache()

    seen: list[str] = []

    def fake_race_basic_info(race_id: str):
        seen.append(race_id)
        if race_id != "20260809-14-12":
            return None
        return {
            "race_id": race_id,
            "race_date": "2026-08-09",
            "stadium_number": 14,
            "race_number": 12,
        }

    monkeypatch.setattr(web_app, "_race_basic_info", fake_race_basic_info)
    monkeypatch.setattr(web_app, "_read_json_cache_stale", lambda _key: None)
    monkeypatch.setattr(web_app, "_write_json_cache", lambda _key, _payload: None)
    monkeypatch.setattr(
        web_app,
        "_motor_history_payload",
        lambda race_id, boat_number, info=None: {
            "race_id": race_id,
            "boat_number": boat_number,
            "info_race_id": info["race_id"],
            "history": [],
        },
    )

    app = web_app.create_app()
    client = app.test_client()
    with client.session_transaction() as session:
        session["is_member"] = True

    response = client.get("/api/race/202608091412/motor-history/1")

    assert response.status_code == 200
    assert response.get_json()["race_id"] == "20260809-14-12"
    assert response.get_json()["info_race_id"] == "20260809-14-12"
    assert seen == ["20260809-14-12"]
