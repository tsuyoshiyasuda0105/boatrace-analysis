# -*- coding: utf-8 -*-
"""/healthz が「DB が死に続けている」ことを申告する回帰テスト。

2026-08-24 実障害: Web プロセスが DB 接続を掴めなくなり、レース詳細が数時間
「準備しています」に落ち続けた。それでも /healthz は DB を見ずに 200 を返し
続けたので Render は健康と判断し、再起動しなかった。人間が気づいて手で再
デプロイするまで復旧しなかった。

通常の probe は DB を触らないまま (毎分の負荷を増やさない)、失敗が続いた
ときだけ実際に ping して裏を取り、駄目なら 503 を返す — この二段構えを固定する。
"""
import pytest

from src.web import app as app_module


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(app_module.config, "WEB_SESSION_SECRET", "healthz-test-secret")
    monkeypatch.setattr(app_module.config, "WEB_MEMBER_PASSWORD", "healthz-test-password")
    monkeypatch.setattr(app_module, "_ensure_db_initialized", lambda: None)
    app = app_module.create_app()
    app.config.update(TESTING=True)
    app_module._note_db_recovered()
    yield app.test_client()
    app_module._note_db_recovered()


def _forbid_db(monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("通常の probe で DB を触ってはいけない")

    monkeypatch.setattr(app_module, "db_connect", _boom)


def test_healthy_probe_does_not_touch_the_database(client, monkeypatch):
    _forbid_db(monkeypatch)
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.get_json()["checks"]["db"] == "skipped"


def test_brief_failure_does_not_make_the_service_unhealthy(client, monkeypatch):
    _forbid_db(monkeypatch)
    app_module._note_db_failure_started()
    monkeypatch.setattr(app_module, "_db_failing_for_seconds", lambda: 5.0)
    response = client.get("/healthz")
    assert response.status_code == 200, "一瞬の失敗で再起動させない"
    assert response.get_json()["checks"]["db"] == "skipped"


def test_sustained_failure_confirmed_by_ping_reports_unhealthy(client, monkeypatch):
    monkeypatch.setattr(app_module, "_db_failing_for_seconds", lambda: 999.0)

    def _boom(*_a, **_k):
        raise RuntimeError("pool timeout")

    monkeypatch.setattr(app_module, "db_connect", _boom)
    response = client.get("/healthz")
    assert response.status_code == 503, "DB が死に続けているなら Render に再起動させる"
    body = response.get_json()
    assert body["status"] == "error"
    assert "pool timeout" in body["checks"]["db"]


def test_sustained_failure_that_actually_recovered_stays_healthy(client, monkeypatch):
    """記録が残っていても、実際に ping が通るなら 200 に戻す (誤検知で殺さない)。"""
    monkeypatch.setattr(app_module, "_db_failing_for_seconds", lambda: 999.0)
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.get_json()["checks"]["db"] == "ok"


def test_guard_can_be_disabled_by_env(client, monkeypatch):
    monkeypatch.setenv("BOATRACE_HEALTHZ_DB_GUARD", "0")
    monkeypatch.setattr(app_module, "_db_failing_for_seconds", lambda: 999.0)
    _forbid_db(monkeypatch)
    assert client.get("/healthz").status_code == 200
