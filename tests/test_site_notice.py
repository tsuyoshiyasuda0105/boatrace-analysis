"""サイト上部のお知らせ（最小構成・2026-10-07）。"""
from pathlib import Path


def _app(monkeypatch, notice=None):
    from src.web import app as web_app
    if notice is None:
        monkeypatch.delenv("BOATRACE_SITE_NOTICE", raising=False)
    else:
        monkeypatch.setenv("BOATRACE_SITE_NOTICE", notice)
    application = web_app.create_app(cached_predictions_only=True)
    application.config.update(TESTING=True)
    return application


def _render(app):
    with app.test_request_context("/"):
        return app.jinja_env.get_template("base.html").render()


def test_no_notice_by_default(monkeypatch):
    app = _app(monkeypatch)
    assert app.jinja_env.globals["site_notice"] == ""
    html = _render(app)
    assert "site-notice" not in html


def test_the_notice_is_shown_on_every_page_when_set(monkeypatch):
    text = "当日の直前情報の自動更新を、一時お休みしています。"
    app = _app(monkeypatch, "  " + text + "  ")
    assert app.jinja_env.globals["site_notice"] == text
    html = _render(app)
    assert 'class="site-notice"' in html and text in html


def test_the_notice_is_escaped(monkeypatch):
    app = _app(monkeypatch, "<script>x</script>")
    html = _render(app)
    assert "<script>x</script>" not in html


def test_the_style_exists():
    assert ".site-notice" in Path("src/web/static/style.css").read_text(encoding="utf-8")
