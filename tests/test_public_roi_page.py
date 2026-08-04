from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_public_roi_page_is_public_and_uses_leak_safe_rows():
    source = (ROOT / "src" / "web" / "app.py").read_text(encoding="utf-8")
    route_block = source.split('@app.route("/public/roi")', 1)[1].split(
        '@app.route("/")',
        1,
    )[0]

    assert "def public_roi()" in route_block
    assert "@login_required" not in route_block
    assert "def _public_roi_rows_for_display" in source
    assert "The public page intentionally hides exact conditions and buy tickets." in source
    assert '"category": _public_roi_category(row)' in source
    assert '"condition"' not in route_block
    assert '"bet"' not in route_block


def test_public_roi_button_and_template_exist():
    base = (ROOT / "src" / "web" / "templates" / "base.html").read_text(encoding="utf-8")
    template = (ROOT / "src" / "web" / "templates" / "public_roi.html").read_text(
        encoding="utf-8"
    )
    css = (ROOT / "src" / "web" / "static" / "style.css").read_text(encoding="utf-8")

    assert "url_for('public_roi')" in base
    assert "公開用データ" in base
    assert "公開用データ" in template
    assert "条件詳細と具体的な閾値" in template
    assert "public-roi-page" in css
    assert "account-btn-public-roi" in css
