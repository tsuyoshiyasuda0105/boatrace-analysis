"""使い方ページ（/guide）の回帰テスト。

解説動画は「もう触る気がある人」向けの教材なので、YouTube 検索に晒すのではなく
アプリの中で見せる (2026-09-09)。ページは会員でなくても開ける必要がある。
"""
from __future__ import annotations

import time

import pytest

from src.web import app as web_app
from src.web.guide_bp import GUIDE_VIDEOS


@pytest.fixture
def app(monkeypatch):
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("BOATRACE_TASK_TRIGGER", raising=False)
    monkeypatch.setattr(web_app, "_ensure_db_initialized", lambda: None)
    web_app.invalidate_cache()
    application = web_app.create_app(cached_predictions_only=True)
    application.config.update(TESTING=True, SECRET_KEY="guide-test")
    application._system_status_cache = {"ts": time.time(), "warnings": []}
    return application


def test_guide_page_opens_without_logging_in(app):
    """登録前の人に「何ができるか」を見せる導線なので、認証を挟まないこと。"""
    res = app.test_client().get("/guide")
    assert res.status_code == 200
    assert "使い方" in res.get_data(as_text=True)


def test_every_video_is_embedded(app):
    html = app.test_client().get("/guide").get_data(as_text=True)
    for video in GUIDE_VIDEOS:
        assert video["id"] in html, f"{video['title']} が埋め込まれていない"
        assert video["title"] in html


def test_videos_use_the_no_cookie_domain(app):
    """CSP が許可しているのは youtube-nocookie のみ。素の youtube.com を書くと
    本番で無言でブロックされ、動画が真っ黒になる。"""
    html = app.test_client().get("/guide").get_data(as_text=True)
    assert "youtube-nocookie.com/embed/" in html
    assert "//www.youtube.com/embed" not in html


def test_security_policy_allows_the_embedded_player(app):
    """frame-src が無いと default-src 'self' が効いて iframe が落ちる。"""
    csp = app.test_client().get("/guide").headers.get("Content-Security-Policy", "")
    assert "frame-src https://www.youtube-nocookie.com" in csp


def test_guide_link_is_reachable_from_a_page_seen_while_logged_out(app):
    """ヘッダーの導線が会員判定の内側に入ると、未登録の人から永久に見えなくなる。"""
    html = app.test_client().get("/guide").get_data(as_text=True)
    assert 'href="/guide"' in html


def test_video_ids_look_like_youtube_ids():
    """ID を書き間違えると再生できない枠だけが並ぶので、形だけ検査する。"""
    import re

    for video in GUIDE_VIDEOS:
        assert re.fullmatch(r"[A-Za-z0-9_-]{11}", video["id"]), video
        assert video["title"] and video["lead"] and video["length"]


def test_page_states_it_does_not_recommend_bets(app):
    """規約と同じ趣旨を、教材の入口にも置いておく。"""
    html = app.test_client().get("/guide").get_data(as_text=True)
    assert "推奨するものではありません" in html
