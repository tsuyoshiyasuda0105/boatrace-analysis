"""はじめての人向けの案内ページ（/start）の回帰テスト。

2026-09-19 に v2 デザイン（別制作の静的HTML）へ移植。SNS から来た人が最初に
着地するページで、登録前にページ内で検証例を体験してから登録に誘う。
守りたい約束（登録前の明示・法務リンク・金額は出さない・外部読込なし・正直な
言い回し）をここで固定する。
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


def test_links_to_signup_and_to_races_and_guide(html):
    assert 'href="/signup-supabase"' in html
    assert 'href="/races"' in html
    assert 'href="/guide"' in html


def test_experience_lets_people_try_before_signing_up(html):
    """登録前にページ内で検証例を試せる（5つの説）。"""
    assert 'id="try"' in html
    for key in ("nige", "omura", "makuri", "entry", "kanchou"):
        assert f'data-key="{key}"' in html, f"説 {key} のボタンが無い"
    # 具体的な数字は歩き方（walkthrough）の実画面説明にも出ている
    assert "553,814" in html and "81.4" in html


def test_states_beta_terms_but_no_price(html):
    """募集の時点で明示する約束。金額は検討中なので出さない（9/18 指示）。"""
    assert "期間限定" in html
    assert "1,380" not in html and "1380" not in html
    assert "自動で" in html and "ことはありません" in html
    assert "20歳以上" in html


def test_links_legal_pages(html):
    for path in ("/legal/terms", "/legal/privacy", "/legal/tokushoho"):
        assert f'href="{path}"' in html


def test_welcomes_tip_followers_without_blaming_tipsters(html):
    """予想に乗っている人・疑い始めた人の両方に向け、予想する側を敵に回さない。"""
    assert "その予想に乗る前に" in html
    assert "予想家の実績や配信内容を自動で判定する機能ではありません" in html
    assert "予想屋" not in html and "騙" not in html


def test_shows_the_manga_and_serves_assets_from_self(html):
    """マンガでアプリを説明。画像・CSS・JS はすべて自サイト配信（CSP）。"""
    for name in ("manga_p1.webp", "manga_p2.webp", "v2.css", "v2.js",
                 "hero-desktop.webp", "hero-mobile.webp", "logo-white.webp", "water-hero.webp"):
        assert (STATIC / "lp" / name).is_file(), f"{name} が static/lp に無い"
    assert 'id="manga-image"' in html
    assert "/static/lp/manga_p1.webp" in html


def test_does_not_load_outside_fonts_scripts_or_styles(html):
    """CSP は自サイト＋Cloudflareビーコンのみ。外部フォント/CSS/JSは無言で落ちる。"""
    assert "fonts.googleapis.com" not in html
    assert '<link rel="stylesheet" href="http' not in html
    # 外部スクリプトは Cloudflare ビーコン以外に無いこと
    import re
    ext = re.findall(r'<script[^>]+src="https?://[^"]+', html)
    assert all("static.cloudflareinsights.com" in tag for tag in ext), ext
