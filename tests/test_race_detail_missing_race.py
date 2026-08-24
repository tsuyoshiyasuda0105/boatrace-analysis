# -*- coding: utf-8 -*-
"""存在しないレースを仮ページで隠さないことの回帰テスト。

2026-08-24: 実在しない race_id でも「レース詳細を準備しています」が返るため、
「まだ作っている途中」と「そんなレースは無い」が外から見分けられなかった。
実障害の調査中に、存在しないレースで再現を試みて誤った結論を出しかけた。
"""
import pytest

from src.web import app as app_module


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(app_module.config, "WEB_SESSION_SECRET", "missing-race-secret")
    monkeypatch.setattr(app_module.config, "WEB_MEMBER_PASSWORD", "missing-race-password")
    monkeypatch.setattr(app_module, "_ensure_db_initialized", lambda: None)
    app = app_module.create_app()
    app.config.update(TESTING=True)
    return app.test_client()


def _guest_with_empty_cache(monkeypatch):
    monkeypatch.setattr(app_module, "is_member", lambda: False)
    monkeypatch.setattr(app_module, "_read_page_html_cache", lambda *_a, **_k: None)
    monkeypatch.setattr(app_module, "_read_page_html_cache_stale", lambda *_a: None)


def test_unknown_race_is_not_hidden_behind_the_preparing_page(client, monkeypatch):
    _guest_with_empty_cache(monkeypatch)
    monkeypatch.setattr(app_module, "_race_basic_info", lambda _rid: None)

    assert client.get("/race/20260824-02-05").status_code == 404


def test_real_race_without_a_cached_page_still_shows_preparing(client, monkeypatch):
    _guest_with_empty_cache(monkeypatch)
    monkeypatch.setattr(
        app_module, "_race_basic_info", lambda _rid: {"race_date": "2026-08-24"}
    )
    monkeypatch.setattr(
        app_module, "_start_race_detail_background_refresh", lambda *_a, **_k: True
    )

    response = client.get("/race/20260824-20-11")
    assert response.status_code == 200
    assert "準備しています" in response.get_data(as_text=True)


def test_unreadable_database_falls_back_to_preparing_not_404(client, monkeypatch):
    """DB を引けない時に 404 を出すと、実在するレースを消してしまう。"""
    _guest_with_empty_cache(monkeypatch)

    def _boom(_rid):
        raise RuntimeError("pool timeout")

    monkeypatch.setattr(app_module, "_race_basic_info", _boom)
    monkeypatch.setattr(
        app_module, "_start_race_detail_background_refresh", lambda *_a, **_k: True
    )

    response = client.get("/race/20260824-20-11")
    assert response.status_code == 200
    assert "準備しています" in response.get_data(as_text=True)
