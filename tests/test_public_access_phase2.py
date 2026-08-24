# -*- coding: utf-8 -*-
"""公開範囲 第2段階の回帰テスト。

方針 (発注者): 公開 = レース一覧 / レース詳細 (予測含む) / ROI公開ページ。
会員 = 本日のレース / バックテストLAB / Value Bet。管理者ページは現状維持。
"""
import pytest

from src.web import app as app_module


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(app_module.config, "WEB_SESSION_SECRET", "phase2-test-secret")
    monkeypatch.setattr(app_module.config, "WEB_MEMBER_PASSWORD", "phase2-test-password")
    monkeypatch.setattr(app_module, "_ensure_db_initialized", lambda: None)
    monkeypatch.delenv("BOATRACE_GUEST_ACCESS", raising=False)
    app = app_module.create_app()
    app.config.update(TESTING=True)
    return app.test_client()


PUBLIC_DISPLAY_APIS = (
    "/api/race/20260824-20-11/signals",
    "/api/race/20260824-20-11/motor-history/1",
    "/api/race/20260824-20-11/racer-detail/1",
)


@pytest.mark.parametrize("path", PUBLIC_DISPLAY_APIS)
def test_display_apis_are_not_locked_behind_login(client, path):
    """詳細ページが公開なら、その描画に使う API も公開でなければ画面が欠ける。"""
    response = client.get(path)
    assert response.status_code != 401, f"{path} が未ログインを弾いている"


def test_value_bets_stays_member_only(client):
    response = client.get("/api/race/20260824-20-11/value-bets")
    assert response.status_code == 401, "EV/Value Bet は会員価値の中核なので公開しない"


def test_member_pages_still_require_login(client):
    for path in ("/member/today-races", "/member/today-races/history", "/kachisuji/"):
        response = client.get(path)
        assert response.status_code in (301, 302), f"{path} が未ログインに開いている"
        assert "/login" in response.headers.get("Location", ""), path


def test_member_apis_still_require_login(client):
    for path in ("/api/market-signals", "/api/odds-123-timeline", "/api/member/l4-stats"):
        assert client.get(path).status_code == 401, path


def test_guest_kill_switch_closes_the_display_apis(monkeypatch):
    """公開をやめたい時は 1 つのスイッチで閉じられること。"""
    monkeypatch.setattr(app_module.config, "WEB_SESSION_SECRET", "phase2-test-secret")
    monkeypatch.setattr(app_module.config, "WEB_MEMBER_PASSWORD", "phase2-test-password")
    monkeypatch.setattr(app_module, "_ensure_db_initialized", lambda: None)
    monkeypatch.setenv("BOATRACE_GUEST_ACCESS", "0")
    app = app_module.create_app()
    app.config.update(TESTING=True)
    client = app.test_client()

    for path in PUBLIC_DISPLAY_APIS:
        assert client.get(path).status_code == 401, path
