"""検索エンジン向けの robots.txt と sitemap.xml の回帰テスト。

既定（BOATRACE_ALLOW_INDEXING 未設定）は全面「検索お断り」。
索引ON のときだけ公開ページを許可し、会員・API・認証系は必ず塞ぐ (2026-09-19)。
"""
from __future__ import annotations

import time

import pytest

from src.web import app as web_app


@pytest.fixture
def app(monkeypatch):
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("BOATRACE_TASK_TRIGGER", raising=False)
    monkeypatch.setattr(web_app, "_ensure_db_initialized", lambda: None)
    web_app.invalidate_cache()
    application = web_app.create_app(cached_predictions_only=True)
    application.config.update(TESTING=True, SECRET_KEY="seo-test")
    application._system_status_cache = {"ts": time.time(), "warnings": []}
    return application


def test_robots_blocks_everything_by_default(app, monkeypatch):
    monkeypatch.delenv("BOATRACE_ALLOW_INDEXING", raising=False)
    body = app.test_client().get("/robots.txt").get_data(as_text=True)
    assert "Disallow: /" in body
    assert "Sitemap:" not in body  # 未公開の間はサイトマップも案内しない


def test_robots_allows_public_but_blocks_sensitive_when_enabled(app, monkeypatch):
    monkeypatch.setenv("BOATRACE_ALLOW_INDEXING", "1")
    body = app.test_client().get("/robots.txt").get_data(as_text=True)
    # 会員・API・認証系は索引ONでも塞ぐ
    for p in ("/api/", "/member/", "/admin/", "/login", "/signup-supabase",
              "/billing/", "/internal/"):
        assert f"Disallow: {p}" in body, f"{p} を塞いでいない"
    # 公開ページはブロックされていない（全面 Disallow: / が無い）
    assert "Disallow: /\n" not in body
    assert "Allow: /" in body
    assert "Sitemap:" in body and "/sitemap.xml" in body


def test_sitemap_lists_the_landing_page(app):
    res = app.test_client().get("/sitemap.xml")
    assert res.status_code == 200
    assert "xml" in res.headers.get("Content-Type", "")
    body = res.get_data(as_text=True)
    assert "<urlset" in body
    for path in ("/start", "/guide", "/legal/tokushoho"):
        assert f"{path}</loc>" in body
    # 会員・APIはサイトマップに載せない
    assert "/member/" not in body and "/api/" not in body
