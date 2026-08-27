# -*- coding: utf-8 -*-
"""空のレース一覧を保存しないことの回帰テスト。

2026-08-28 リッキーさん報告: 管理者で /races?date=2026-08-28 を開くと
「この日のデータはありません」と出るのに、未ログインでは 144 レースが出た。
一覧のキャッシュ鍵は閲覧者の役割を含むため、番組表が届く前や DB が一瞬
答えられなかった瞬間に焼かれた空の画面が、その役割にだけ出続ける。
空の結果は答えではなく、保存してはいけない。
"""
import pytest

from src.web import app as app_module


@pytest.fixture
def member_client(monkeypatch):
    monkeypatch.setattr(app_module.config, "WEB_SESSION_SECRET", "empty-races-secret")
    monkeypatch.setattr(app_module.config, "WEB_MEMBER_PASSWORD", "empty-races-password")
    monkeypatch.setattr(app_module, "_ensure_db_initialized", lambda: None)
    app = app_module.create_app()
    app.config.update(TESTING=True)
    import src.web.auth as auth
    monkeypatch.setattr(app_module, "is_member", lambda: True)
    monkeypatch.setattr(auth, "is_member", lambda: True)
    return app.test_client()


def test_empty_listing_is_marked_no_store(member_client, monkeypatch):
    monkeypatch.setattr(app_module, "_read_top_page_snapshot", lambda _d: None)
    monkeypatch.setattr(app_module, "_races_for_date", lambda *a, **k: [])
    monkeypatch.setattr(
        app_module, "_venue_environment_summaries_for_date", lambda *a, **k: {}
    )

    response = member_client.get("/races?date=2026-08-28")

    assert response.status_code == 200
    assert "データはありません" in response.get_data(as_text=True)
    assert response.cache_control.no_store, (
        "空の一覧を保存すると、その役割の閲覧者にだけ空の画面が出続ける"
    )


def test_a_later_request_can_recover(member_client, monkeypatch):
    """空を返した直後にデータが揃えば、次の閲覧でちゃんと出ること。"""
    monkeypatch.setattr(app_module, "_read_top_page_snapshot", lambda _d: None)
    monkeypatch.setattr(
        app_module, "_venue_environment_summaries_for_date", lambda *a, **k: {}
    )
    monkeypatch.setattr(app_module, "_races_for_date", lambda *a, **k: [])
    first = member_client.get("/races?date=2026-08-28")
    assert "データはありません" in first.get_data(as_text=True)

    rows = [
        {"race_id": "20260828-01-01", "stadium_number": 1, "stadium_name": "桐生",
         "race_number": 1, "race_closed_at": None, "results_count": 0}
    ]
    monkeypatch.setattr(app_module, "_races_for_date", lambda *a, **k: rows)

    second = member_client.get("/races?date=2026-08-28")

    assert "データはありません" not in second.get_data(as_text=True), (
        "空が保存されていると、データが揃っても空のまま出続ける"
    )
