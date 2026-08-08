from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_SOURCE = ROOT / "src" / "web" / "app.py"


def _read_app_source() -> str:
    return APP_SOURCE.read_text(encoding="utf-8")


def test_jst_today_helpers_exist():
    source = _read_app_source()

    assert "def _now_jst() -> datetime:" in source
    assert "def _today_jst_date() -> date:" in source
    assert "def _today_jst_iso() -> str:" in source
    assert "return _today_jst_date().isoformat()" in source


def test_cached_date_logic_uses_jst_today_helper():
    source = _read_app_source()
    cached_block = source.split("def cached(", 1)[1].split("return decorator", 1)[0]
    after_request_block = source.split("def add_security_headers(response):", 1)[1].split(
        '@app.route("/robots.txt")', 1
    )[0]

    assert "today_iso = _today_jst_iso()" in cached_block
    assert "date.today().isoformat()" not in cached_block
    assert "_today_jst_iso()" in after_request_block
    assert "date.today().isoformat()" not in after_request_block


def test_today_facing_routes_default_to_jst_today():
    source = _read_app_source()

    for route_name in (
        "public_roi",
        "races",
        "member_today_races",
        "market_signals_for_date",
        "member_accidents",
    ):
        route_block = source.split(f"def {route_name}(", 1)[1].split("@app.route", 1)[0]
        assert '_today_jst_iso()' in route_block
        assert 'date.today().isoformat()' not in route_block
