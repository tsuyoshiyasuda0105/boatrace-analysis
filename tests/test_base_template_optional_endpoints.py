from __future__ import annotations

from flask import render_template, session

from src.web.app import create_app


def test_base_template_skips_optional_start_prediction_link_when_endpoint_missing(monkeypatch):
    app = create_app()
    app.view_functions.pop("start_prediction.prediction_metrics_page", None)

    with app.test_request_context("/member/today-races?date=2026-08-09"):
        session["is_member"] = True
        session["role"] = "admin"
        session["auth_provider"] = "legacy_password"
        html = render_template(
            "base.html",
            target_date="2026-08-09",
            date_form_action="/member/today-races",
        )

    assert "member_health" not in html  # sanity: urls are rendered, not endpoint names
    assert "ST展開精度" not in html
    assert "/admin/memberships" in html
