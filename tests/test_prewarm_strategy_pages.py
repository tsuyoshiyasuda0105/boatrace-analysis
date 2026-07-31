import os
from datetime import date
from pathlib import Path

os.environ["DATABASE_URL"] = ""

from scripts.prewarm_strategy_pages import (
    _hit,
    _validate_market_signal_response,
    build_targets,
)


TODAY = date(2026, 7, 18)


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

    assert targets[0] == "/member/strategy?from=2023-07-19&to=2026-07-18&recompute=1"
    assert "/member/strategy/monthly?recompute=1" in targets
    assert all("/api/market-signals" not in target for target in targets)


def test_dashboard_uses_one_read_only_refresh_clock():
    template = Path("src/web/templates/index.html").read_text(encoding="utf-8")

    assert "setInterval(loadMarketSignals" not in template
    assert "setInterval(loadOdds123Timeline" not in template
    assert "setInterval(refreshDashboard, 30000)" in template
    assert "recompute=1" not in template


def test_render_blueprint_separates_web_and_cron_services():
    blueprint = Path("render.yaml").read_text(encoding="utf-8")

    assert "type: web" in blueprint
    assert "name: boatrace-web" in blueprint
    assert "type: cron" in blueprint
    assert "name: boatrace-regular-cron" in blueprint
    assert "name: boatrace-odds-cron" in blueprint
    assert "name: boatrace-roi-prewarm-cron" in blueprint
    assert "name: boatrace-roi-history-cron" in blueprint
    assert "name: boatrace-roi-finalize-cron" in blueprint
    assert "startCommand: gunicorn" in blueprint
    assert "startCommand: python scripts/render_regular_scheduler.py" in blueprint
    assert "startCommand: python scripts/odds_scheduler.py --no-jitter" in blueprint
    assert "startCommand: python scripts/prewarm_strategy_pages.py --mode signals" in blueprint
    assert 'schedule: "0 */12 * * *"' in blueprint
    assert "startCommand: python scripts/prewarm_strategy_pages.py --mode history" in blueprint
    assert 'schedule: "30 14 * * *"' in blueprint
    assert "startCommand: python scripts/prewarm_strategy_pages.py --mode daily-reconcile" in blueprint


def test_regular_scheduler_leaves_roi_refresh_to_dedicated_crons():
    scheduler = Path("scripts/render_regular_scheduler.py").read_text(encoding="utf-8")
    main_block = scheduler.split("def main() -> int:", 1)[1]
    hourly_block = scheduler.split("def run_hourly", 1)[1].split(
        "def run_roi_daily_self_heal", 1
    )[0]
    nightly_block = scheduler.split("def run_nightly", 1)[1].split(
        "def main() -> int:", 1
    )[0]

    assert "run_signal_refresh_slot(now)" not in main_block
    assert "run_roi_daily_self_heal(now)" not in main_block
    assert "prewarm_strategy_pages.py" not in hourly_block
    assert '"--mode", "nightly"' not in nightly_block
    assert '"--mode", "signals", "--date", tomorrow' in nightly_block


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

    assert "run_beforeinfo(now)" in live_block
    assert 'run_py(["scripts/prewarm_strategy_pages.py", "--mode", "signals"]' not in live_block


def test_beforeinfo_leaves_signal_rebuild_to_dedicated_cron():
    scheduler = Path("scripts/render_regular_scheduler.py").read_text(encoding="utf-8")
    beforeinfo_block = scheduler.split("def run_beforeinfo", 1)[1].split("def run_morning", 1)[0]

    assert 'if summary.get("races", 0) > 0:' in beforeinfo_block
    assert 'run_py(["scripts/prewarm_strategy_pages.py", "--mode", "signals"]' not in beforeinfo_block
