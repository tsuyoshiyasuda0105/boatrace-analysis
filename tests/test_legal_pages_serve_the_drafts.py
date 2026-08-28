# -*- coding: utf-8 -*-
"""4本の法定ページが草案の全文を配信することの回帰テスト。

2026-08-28: 事業者本人と専門家に確認していただいた草案 (利用規約 39 条 /
プライバシーポリシー 23 条 / Discord 規約 22 条 / 特商法) を、環境変数で
未確定項目を埋めながら配信する。プレースホルダーが残ったまま公開される
事故を防ぐため、未確定項目は目立つ黄色 (mark) で表示する。
"""
import pytest

from src.web import app as app_module
from src.web import legal_markdown


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(app_module.config, "WEB_SESSION_SECRET", "legal-test-secret")
    monkeypatch.setattr(app_module.config, "WEB_MEMBER_PASSWORD", "legal-test-password")
    monkeypatch.setattr(app_module, "_ensure_db_initialized", lambda: None)
    app = app_module.create_app()
    app.config.update(TESTING=True)
    return app.test_client()


@pytest.mark.parametrize(
    ("url", "must_contain"),
    [
        ("/legal/terms",     "第1条（適用）"),
        ("/legal/privacy",   "第1条（適用範囲）"),
        ("/legal/tokushoho", "特定商取引法に基づく表記"),
        ("/legal/discord",   "第1条（目的）"),
    ],
)
def test_each_legal_page_serves_the_full_draft(client, url, must_contain):
    response = client.get(url)
    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert must_contain in text


def test_terms_page_contains_all_39_articles(client):
    text = client.get("/legal/terms").get_data(as_text=True)
    for n in range(1, 40):
        assert f"第{n}条" in text, f"第{n}条が抜けている"


def test_privacy_page_contains_all_23_articles(client):
    text = client.get("/legal/privacy").get_data(as_text=True)
    for n in range(1, 24):
        assert f"第{n}条" in text


def test_discord_rules_page_is_reachable_from_the_footer(client):
    text = client.get("/legal/terms").get_data(as_text=True)
    assert "/legal/discord" in text, "Discord 規約への導線が無い"


def test_placeholders_are_highlighted_not_hidden(client, monkeypatch):
    # プレースホルダーは "未入力ですよ" と分かる形で残す。空欄で公開しない。
    monkeypatch.delenv("LEGAL_OPERATOR_NAME", raising=False)
    text = client.get("/legal/tokushoho").get_data(as_text=True)
    assert 'class="legal-placeholder"' in text
    assert "この文書には未確定の項目が" in text


def test_environment_variables_replace_placeholders(client, monkeypatch):
    monkeypatch.setenv("LEGAL_OPERATOR_NAME", "テスト事業者")
    monkeypatch.setenv("LEGAL_EMAIL", "support@example.jp")
    text = client.get("/legal/tokushoho").get_data(as_text=True)
    assert "テスト事業者" in text
    assert "support@example.jp" in text


def test_the_converter_escapes_html_from_the_markdown():
    """草案内に生 HTML が入っても、そのまま出さない。"""
    body, _ = legal_markdown.markdown_to_html("段落内の <script>alert(1)</script> は無害化する。")
    assert "<script>" not in body
    assert "&lt;script&gt;" in body


def test_the_converter_supports_the_shapes_the_drafts_use():
    md = "# 見出し1\n\n段落。\n\n- 箇条書き1\n- 箇条書き2\n\n1. 番号 1\n2. 番号 2\n\n| A | B |\n| --- | --- |\n| a1 | b1 |\n"
    body, _ = legal_markdown.markdown_to_html(md)
    assert "<h1>見出し1</h1>" in body
    assert "<ul>" in body and "<li>箇条書き1</li>" in body
    assert "<ol>" in body and "<li>番号 1</li>" in body
    assert '<table class="legal-table">' in body
    assert "<td>a1</td>" in body
