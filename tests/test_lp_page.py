"""はじめての人向けの案内ページ（/start）の回帰テスト。

SNS から来た人が最初に着地するページ。登録前の人が開けること、
登録とレース一覧への導線が切れていないこと、有料化の予告と年齢の注意が
消えていないことを固定する (2026-09-17)。
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from src.web import app as web_app

STATIC = Path(web_app.__file__).parent / "static"


@pytest.fixture
def app(monkeypatch):
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("BOATRACE_TASK_TRIGGER", raising=False)
    monkeypatch.setattr(web_app, "_ensure_db_initialized", lambda: None)
    web_app.invalidate_cache()
    application = web_app.create_app(cached_predictions_only=True)
    application.config.update(TESTING=True, SECRET_KEY="lp-test")
    application._system_status_cache = {"ts": time.time(), "warnings": []}
    return application


@pytest.fixture
def html(app):
    res = app.test_client().get("/start")
    assert res.status_code == 200
    return res.get_data(as_text=True)


def test_opens_without_logging_in(app):
    res = app.test_client().get("/start")
    assert res.status_code == 200
    assert "location" not in {k.lower() for k in res.headers.keys()}


def test_links_to_signup_and_to_races_without_signup(html):
    assert 'href="/signup-supabase"' in html
    assert 'href="/races"' in html
    assert 'href="/guide"' in html


def test_states_beta_terms_before_people_sign_up(html):
    """後から有料化しても筋が通るよう、募集の時点で明示しておく約束。"""
    assert "期間限定" in html
    assert "1,380円" in html
    assert "自動で" in html and "ことはありません" in html
    assert "20歳以上" in html


def test_links_legal_pages(html):
    for path in ("/legal/terms", "/legal/privacy", "/legal/tokushoho"):
        assert f'href="{path}"' in html


def test_screenshots_exist_and_are_served_from_self(html):
    """CSP の img-src は 'self' と data: だけ。外部画像を書くと本番で消える。"""
    for name in ("result.webp", "races.webp"):
        assert (STATIC / "lp" / name).is_file()
        assert f"/static/lp/{name}" in html
    assert "<img src=\"http" not in html


def test_does_not_load_outside_fonts_or_styles(html):
    """CSP の style-src/font-src は自サイトのみ。Google Fonts 等は無言で落ちる。"""
    assert "fonts.googleapis.com" not in html
    assert "<link rel=\"stylesheet\" href=\"http" not in html


def test_invites_people_to_check_a_tip_before_riding_it(html):
    """リッキーさんの依頼 (2026-09-17): 予想に乗っている人・疑っている人の両方に向け、
    予想する側を敵に回さない言い方で「確かめてから乗る」を伝える段。"""
    assert "根拠を確かめてから" in html
    assert "良い予想は、確かめるともっと強くなります" in html
    assert "予想屋" not in html and "騙" not in html
