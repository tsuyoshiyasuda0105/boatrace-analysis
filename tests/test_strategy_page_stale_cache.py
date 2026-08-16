import os

os.environ["DATABASE_URL"] = ""

from src.web import app as web_app


def _member_client():
    app = web_app.create_app()
    client = app.test_client()
    with client.session_transaction() as session:
        session["is_member"] = True
        session["role"] = "admin"  # /member/strategy(/monthly) は @admin_required に格上げ済み
    return client


def test_strategy_page_uses_stale_html_without_running_aggregation(monkeypatch):
    web_app.invalidate_cache()
    monkeypatch.setattr(web_app, "_roi_history_page_revision", lambda *_args: "ledger-fixed")
    monkeypatch.setattr(web_app, "_read_page_html_cache", lambda *_args: None)
    monkeypatch.setattr(
        web_app,
        "_read_page_html_cache_stale",
        lambda _key: "stale-strategy-page",
    )
    response = _member_client().get("/member/strategy")

    assert response.status_code == 200
    assert response.get_data(as_text=True) == "stale-strategy-page"


def test_monthly_page_uses_stale_html_without_running_aggregation(monkeypatch):
    web_app.invalidate_cache()
    monkeypatch.setattr(web_app, "_roi_history_page_revision", lambda *_args: "ledger-fixed")
    monkeypatch.setattr(web_app, "_read_page_html_cache", lambda *_args: None)
    monkeypatch.setattr(
        web_app,
        "_read_page_html_cache_stale",
        lambda _key: "stale-monthly-page",
    )
    response = _member_client().get("/member/strategy/monthly")

    assert response.status_code == 200
    assert response.get_data(as_text=True) == "stale-monthly-page"


def test_strategy_page_cache_changes_immediately_with_ledger_revision(monkeypatch):
    web_app.invalidate_cache()
    revision = {"value": "ledger-before"}
    seen_keys = []

    monkeypatch.setattr(
        web_app,
        "_roi_history_page_revision",
        lambda *_args: revision["value"],
    )

    def _read(cache_key, _max_age):
        seen_keys.append(cache_key)
        return f"page-for-{revision['value']}"

    monkeypatch.setattr(web_app, "_read_page_html_cache", _read)
    client = _member_client()

    first = client.get("/member/strategy?from=2026-04-01&to=2026-08-16")
    revision["value"] = "ledger-after"
    second = client.get("/member/strategy?from=2026-04-01&to=2026-08-16")

    assert first.get_data(as_text=True) == "page-for-ledger-before"
    assert second.get_data(as_text=True) == "page-for-ledger-after"
    assert seen_keys[0] != seen_keys[1]
    assert "ledger-before" in seen_keys[0]
    assert "ledger-after" in seen_keys[1]


def test_monthly_page_cache_changes_immediately_with_ledger_revision(monkeypatch):
    web_app.invalidate_cache()
    revision = {"value": "ledger-before"}
    seen_keys = []
    monkeypatch.setattr(
        web_app,
        "_roi_history_page_revision",
        lambda *_args: revision["value"],
    )

    def _read(cache_key, _max_age):
        seen_keys.append(cache_key)
        return f"monthly-for-{revision['value']}"

    monkeypatch.setattr(web_app, "_read_page_html_cache", _read)
    client = _member_client()

    first = client.get("/member/strategy/monthly")
    revision["value"] = "ledger-after"
    second = client.get("/member/strategy/monthly")

    assert first.get_data(as_text=True) == "monthly-for-ledger-before"
    assert second.get_data(as_text=True) == "monthly-for-ledger-after"
    assert seen_keys[0] != seen_keys[1]


def test_roi_history_page_revision_is_lightweight_and_settled_only(monkeypatch):
    executed = []

    class _Cursor:
        def fetchone(self):
            return ("2026-08-16T19:58:38", 174)

    class _Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql, params):
            executed.append((sql, params))
            return _Cursor()

    monkeypatch.setattr(web_app, "db_connect", lambda: _Connection())

    revision = web_app._roi_history_page_revision("2026-04-01", "2026-08-16")

    assert revision == "ledger-2026-08-16T19:58:38-174"
    assert executed[0][1] == ("2026-04-01", "2026-08-16")
    assert "MAX(updated_at), COUNT(*)" in executed[0][0]
    assert "is_settled = 1" in executed[0][0]
    assert "is_active = 1" in executed[0][0]


def test_operational_totals_match_ledger_rows_and_exclude_reconstruction():
    keys = ("a1_ace_motor_123_corr_tri", "g23_optb_tri")
    rows = [
        {
            "date": "2026-07-02",
            "_adopted_from_market_signals_cache": True,
            "a1_ace_motor_123_corr_tri_bets": 2,
            "a1_ace_motor_123_corr_tri_hits": 1,
            "a1_ace_motor_123_corr_tri_pay": 530,
            "g23_optb_tri_bets": 1,
            "g23_optb_tri_hits": 0,
            "g23_optb_tri_pay": 0,
        },
        {
            "date": "2026-07-03",
            "_adopted_from_market_signals_cache": True,
            "a1_ace_motor_123_corr_tri_bets": 0,
            "a1_ace_motor_123_corr_tri_hits": 0,
            "a1_ace_motor_123_corr_tri_pay": 0,
            "g23_optb_tri_bets": 2,
            "g23_optb_tri_hits": 1,
            "g23_optb_tri_pay": 780,
        },
        {
            "date": "2026-07-04",
            "_adopted_from_market_signals_cache": False,
            "a1_ace_motor_123_corr_tri_bets": 99,
            "a1_ace_motor_123_corr_tri_hits": 99,
            "a1_ace_motor_123_corr_tri_pay": 99999,
            "g23_optb_tri_bets": 99,
            "g23_optb_tri_hits": 99,
            "g23_optb_tri_pay": 99999,
        },
    ]

    operational, reconstructed, totals = web_app._operational_roi_totals(
        rows,
        keys,
        {"a1_ace_motor_123_corr_tri": 100, "g23_optb_tri": 200},
    )

    assert [row["date"] for row in operational] == ["2026-07-02", "2026-07-03"]
    assert [row["date"] for row in reconstructed] == ["2026-07-04"]
    assert totals == {
        "adopted_total_bets": 5,
        "adopted_total_hits": 2,
        "adopted_total_pay": 1310,
        "adopted_total_cost": 800,
        "operational_day_count": 2,
        "reconstructed_day_count": 1,
    }
