import os
import runpy
from datetime import date
from pathlib import Path

from flask import Flask

os.environ["DATABASE_URL"] = ""

from scripts.prewarm_strategy_pages import (
    _hit,
    _prepare_internal_session,
    _validate_market_signal_response,
    build_targets,
)


TODAY = date(2026, 7, 18)


def test_prewarm_overrides_inherited_exhibition_trigger(monkeypatch):
    monkeypatch.setenv("BOATRACE_TASK_TRIGGER", "render-exhibition-detail-refresh")

    runpy.run_path("scripts/prewarm_strategy_pages.py")

    assert os.environ["BOATRACE_TASK_TRIGGER"] == "render-prewarm"


def test_internal_prewarm_session_has_admin_role():
    app = Flask(__name__)
    app.secret_key = "test-only-secret"
    client = app.test_client()

    _prepare_internal_session(client)

    with client.session_transaction() as sess:
        assert sess["is_member"] is True
        assert sess["role"] == "admin"
        assert sess["auth_provider"] == "internal_prewarm"


def test_signals_mode_is_the_daytime_market_signal_writer():
    assert build_targets("signals", TODAY) == [
        "/api/market-signals?date=2026-07-18&recompute=1"
    ]


def test_realtime_mode_rebuilds_today_before_reading_strategy_page():
    targets = build_targets("realtime", TODAY)

    assert targets == [
        "/api/market-signals?date=2026-07-18&recompute=1",
        "/member/strategy?from=2026-06-18&to=2026-07-18",
    ]
    assert all("/member/strategy/monthly" not in target for target in targets)


def test_morning_check_keeps_monthly_refresh_out_of_race_hours():
    targets = build_targets("morning-check", TODAY)

    assert "/member/strategy?from=2026-06-18&to=2026-07-18&recompute=1" in targets
    assert all("/member/strategy/monthly" not in target for target in targets)


def test_morning_and_nightly_refresh_market_signal_snapshot():
    morning_targets = build_targets("morning-check", TODAY)
    assert morning_targets[0] == "/api/market-signals?date=2026-07-18&recompute=1"

    nightly_targets = build_targets("nightly", TODAY)
    assert nightly_targets[0] == "/api/market-signals?date=2026-07-17&recompute=1"
    assert nightly_targets[1] == "/api/market-signals?date=2026-07-18&recompute=1"


def test_history_mode_contains_only_historical_roi_pages():
    targets = build_targets("history", TODAY)

    assert targets == [
        "/member/strategy?from=2026-06-18&to=2026-07-18&recompute=1",
        "/member/strategy?from=2026-06-18&to=2026-07-18",
    ]
    assert all("2023-07-19" not in target for target in targets)
    assert all("/member/strategy/monthly" not in target for target in targets)
    assert all("/api/market-signals" not in target for target in targets)


def test_dashboard_uses_one_read_only_refresh_clock():
    template = Path("src/web/templates/index.html").read_text(encoding="utf-8")

    assert "setInterval(loadMarketSignals" not in template
    assert "setInterval(loadOdds123Timeline" not in template
    assert "setInterval(refreshDashboard, 60000)" in template
    assert "recompute=1" not in template


def test_render_blueprint_separates_web_and_cron_services():
    blueprint = Path("render.yaml").read_text(encoding="utf-8")

    assert "type: web" in blueprint
    assert "name: boatrace-web" in blueprint
    assert "type: cron" in blueprint
    assert "name: boatrace-regular-cron" in blueprint
    assert "name: boatrace-odds-cron" in blueprint
    assert "name: boatrace-accident-external-check-cron" in blueprint
    assert "name: boatrace-roi-prewarm-cron" not in blueprint
    assert "name: boatrace-roi-history-cron" not in blueprint
    assert "name: boatrace-roi-finalize-cron" not in blueprint
    assert "startCommand: gunicorn" in blueprint
    assert "startCommand: python scripts/render_regular_scheduler.py" in blueprint
    assert "startCommand: python scripts/odds_scheduler_render.py --no-jitter" in blueprint
    assert 'schedule: "* 23,0-13 * * *"' not in blueprint
    assert blueprint.count('schedule: "*/5 23,0-13 * * *"') >= 3
    assert 'BOATRACE_RENDER_DAYTIME_LITE' in blueprint
    assert "startCommand: python scripts/prewarm_strategy_pages.py --mode signals" not in blueprint


def test_roi_refresh_ownership_is_lite_and_maintenance_only():
    regular = Path("scripts/render_regular_scheduler.py").read_text(encoding="utf-8")
    maintenance = Path("scripts/render_maintenance_scheduler.py").read_text(encoding="utf-8")
    main_block = regular.split("def main() -> int:", 1)[1]

    assert "run_lite_daytime_bootstrap(now)" in main_block
    assert "run_signal_refresh_slot(now, source_gate_verified=True)" in regular
    assert "regular.run_signal_refresh_slot(now, source_gate_verified=True)" in maintenance
    assert "regular.run_roi_daily_self_heal(now)" in maintenance
    assert "run_roi_history_slot" not in regular


def test_regular_scheduler_can_run_in_daytime_lite_mode():
    scheduler = Path("scripts/render_regular_scheduler.py").read_text(encoding="utf-8")

    assert "def render_daytime_lite_mode()" in scheduler
    assert "run_lite_daytime_bootstrap(now)" in scheduler
    assert "if lite_mode and 8 <= now.hour <= 23:" in scheduler
    assert "if not lite_mode and 6 <= now.hour <= 23:" not in scheduler


class _Response:
    def __init__(self, payload, *, status=200, cache_state="recomputed"):
        self.status_code = status
        self._payload = payload
        self.headers = {"X-Boatrace-Cache": cache_state}

    def get_json(self, silent=True):
        return self._payload

    def get_data(self):
        return b"{}"


class _Client:
    def __init__(self, response):
        self.response = response

    def get(self, path):
        return self.response


def _valid_signal_payload():
    return {
        "date": "2026-07-18",
        "computed_at": "2026-07-18T09:10:00",
        "n_races": 1,
        "signals": {"202607180101": {"race_id": "202607180101"}},
        "data_status": {
            "race_basic": {"count": 144, "total": 144},
        },
    }


def test_market_signal_prewarm_validates_completed_snapshot():
    response = _Response(_valid_signal_payload())

    valid, detail = _validate_market_signal_response(
        response,
        "/api/market-signals?date=2026-07-18&recompute=1",
    )

    assert valid is True
    assert "signals=1" in detail


def test_market_signal_prewarm_rejects_cache_placeholder_with_http_200():
    payload = _valid_signal_payload()
    payload["computed_at"] = None
    payload["data_status"]["cache_miss"] = True
    response = _Response(payload, cache_state="cache-miss")

    status, size, valid, detail = _hit(
        _Client(response),
        "/api/market-signals?date=2026-07-18&recompute=1",
    )

    assert status == 200
    assert size == 2
    assert valid is False
    assert "cache_state" in detail


def test_market_signal_prewarm_accepts_valid_zero_candidate_result():
    payload = _valid_signal_payload()
    payload["n_races"] = 0
    payload["signals"] = {}

    valid, detail = _validate_market_signal_response(
        _Response(payload),
        "/api/market-signals?date=2026-07-18&recompute=1",
    )

    assert valid is True
    assert "signals=0" in detail


def test_regular_scheduler_does_not_duplicate_signal_rebuild_each_loop():
    scheduler = Path("scripts/render_regular_scheduler.py").read_text(encoding="utf-8")
    main_block = scheduler.split("def main() -> int:", 1)[1]
    live_block = main_block.split("# Lightweight result polling", 1)[0]

    assert "run_beforeinfo(now)" not in live_block
    assert 'run_py(["scripts/prewarm_strategy_pages.py", "--mode", "signals"]' not in live_block


def test_regular_scheduler_removes_historical_roi_refresh_from_live_cron():
    scheduler = Path("scripts/render_regular_scheduler.py").read_text(encoding="utf-8")
    main_block = scheduler.split("def main() -> int:", 1)[1]

    assert "run_roi_history_slot" not in main_block
    assert "should_run_roi_history_slot" not in scheduler


def test_beforeinfo_leaves_signal_rebuild_to_dedicated_cron():
    scheduler = Path("scripts/render_regular_scheduler.py").read_text(encoding="utf-8")
    beforeinfo_block = scheduler.split("def run_beforeinfo", 1)[1].split(
        "def run_top_page_snapshot", 1
    )[0]

    assert 'if summary.get("races", 0) > 0:' in beforeinfo_block
    assert 'run_py(["scripts/prewarm_strategy_pages.py", "--mode", "signals"]' not in beforeinfo_block
