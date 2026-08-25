# -*- coding: utf-8 -*-
"""戦略ページの構築がプロセスを窒息させないことの回帰テスト。

2026-08-25 実障害 (日中 8 回の全断)。管理者用 ROI ページのキャッシュキーは
末尾に台帳リビジョンを含み、結果が 1 件入るたびに変わる。同キー検索では新旧
どちらも外れ、25MB 級のページ構築がリクエストスレッド上で数十秒走った。
複数タブ/再読込で全スレッドが塞がり、/healthz が 5 秒応答できず Render に
処刑されていた。「アクセスしただけで落ちる」の正体。
"""
import threading

import pytest

from src.web import app as app_module


@pytest.fixture
def member_client(monkeypatch):
    monkeypatch.setattr(app_module.config, "WEB_SESSION_SECRET", "sfg-secret")
    monkeypatch.setattr(app_module.config, "WEB_MEMBER_PASSWORD", "sfg-password")
    monkeypatch.setattr(app_module, "_ensure_db_initialized", lambda: None)
    app = app_module.create_app()
    app.config.update(TESTING=True)
    client = app.test_client()
    import src.web.auth as auth
    monkeypatch.setattr(app_module, "is_member", lambda: True)
    monkeypatch.setattr(auth, "is_member", lambda: True)
    monkeypatch.setattr(auth, "is_admin", lambda: True)
    return client


def _miss_all_same_key_caches(monkeypatch):
    monkeypatch.setattr(app_module, "_read_page_html_cache", lambda *_a, **_k: None)
    monkeypatch.setattr(app_module, "_read_page_html_cache_stale", lambda *_a: None)


def test_revision_bump_serves_the_previous_generation(member_client, monkeypatch):
    """リビジョンが変わった直後は 1 世代前を出す (作り直しに落とさない)。"""
    _miss_all_same_key_caches(monkeypatch)
    monkeypatch.setattr(
        app_module,
        "_read_stale_strategy_page_any_revision",
        lambda page, f, t: "<html>previous generation</html>",
    )

    response = member_client.get("/member/strategy")

    assert response.status_code == 200
    assert b"previous generation" in response.data


def test_cold_build_is_limited_to_one_at_a_time(member_client, monkeypatch):
    """完全な初回構築は同時 1 件。2 件目は案内ページで即返す。"""
    _miss_all_same_key_caches(monkeypatch)
    monkeypatch.setattr(
        app_module, "_read_stale_strategy_page_any_revision", lambda *_a: None
    )

    acquired = app_module._STRATEGY_BUILD_GATE.acquire(blocking=False)
    assert acquired, "テスト開始時点でゲートが空いていない"
    try:
        response = member_client.get("/member/strategy")
        assert response.status_code == 200
        assert "準備中" in response.get_data(as_text=True), (
            "ゲートが埋まっている間に重い構築へ進むと、全スレッドが塞がって"
            "healthz が窒息する"
        )
    finally:
        app_module._STRATEGY_BUILD_GATE.release()


def test_gate_is_released_even_when_the_build_dies(member_client, monkeypatch):
    """構築が例外で死んでもゲートは必ず返る (返らないと永久に案内ページ)。"""
    _miss_all_same_key_caches(monkeypatch)
    monkeypatch.setattr(
        app_module, "_read_stale_strategy_page_any_revision", lambda *_a: None
    )

    # 構築本体は create_app 内のクロージャで直接は差し替えられないため、
    # 構築の最後で必ず呼ばれる render_template を爆弾にする
    def _boom(*_a, **_k):
        raise RuntimeError("build failed")

    monkeypatch.setattr(app_module, "render_template", _boom)

    with pytest.raises(RuntimeError):
        member_client.get("/member/strategy")

    assert app_module._STRATEGY_BUILD_GATE.acquire(blocking=False), (
        "例外経路でゲートが返っていない"
    )
    app_module._STRATEGY_BUILD_GATE.release()
