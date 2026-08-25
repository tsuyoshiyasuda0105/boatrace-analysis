# -*- coding: utf-8 -*-
"""本番構成 (cached_predictions_only) での Value Bet API の回帰テスト。

2026-08-25 の会員経路耐久テストで発覚: 本番 web は軽量モードで起動しており
三連単予測器が無いため、この API は会員に常時 500 を返していた (呼ぶ画面が
無かったので気づかれなかった)。「壊れている」ではなく「未提供」と返すこと。
"""
import pytest

from src.web import app as app_module


@pytest.fixture
def member_client(monkeypatch):
    monkeypatch.setattr(app_module.config, "WEB_SESSION_SECRET", "vb-test-secret")
    monkeypatch.setattr(app_module.config, "WEB_MEMBER_PASSWORD", "vb-test-password")
    monkeypatch.setattr(app_module, "_ensure_db_initialized", lambda: None)
    app = app_module.create_app(cached_predictions_only=True)
    app.config.update(TESTING=True)
    client = app.test_client()
    monkeypatch.setattr(app_module, "is_member", lambda: True)
    import src.web.auth as auth
    monkeypatch.setattr(auth, "is_member", lambda: True)
    return client


def test_value_bets_declares_unavailable_instead_of_500(member_client, monkeypatch):
    monkeypatch.setattr(
        app_module, "_race_basic_info", lambda _rid: {"race_date": "2026-08-25"}
    )

    response = member_client.get("/api/race/20260825-17-04/value-bets")

    assert response.status_code == 200, "会員の中核機能が常時 500 は許されない"
    body = response.get_json()
    assert body["n_value_bets"] == 0
    assert "not available" in body["warning"]
