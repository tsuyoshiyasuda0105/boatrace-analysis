import os
from datetime import date
from pathlib import Path

os.environ["DATABASE_URL"] = ""

from scripts.prewarm_strategy_pages import build_targets


TODAY = date(2026, 7, 18)


def test_signals_mode_is_the_daytime_market_signal_writer():
    assert build_targets("signals", TODAY) == [
        "/api/market-signals?date=2026-07-18&recompute=1"
    ]


def test_realtime_mode_never_forces_expensive_recompute():
    targets = build_targets("realtime", TODAY)

    assert "/api/market-signals?date=2026-07-18" in targets
    assert all("recompute=1" not in target for target in targets)
    assert all("/member/strategy/monthly" not in target for target in targets)


def test_morning_check_keeps_monthly_refresh_out_of_race_hours():
    targets = build_targets("morning-check", TODAY)

    assert "/member/strategy?from=2026-06-18&to=2026-07-18&recompute=1" in targets
    assert all("/member/strategy/monthly" not in target for target in targets)


def test_morning_and_nightly_refresh_market_signal_snapshot():
    for mode in ("morning-check", "nightly"):
        targets = build_targets(mode, TODAY)
        assert targets[0] == "/api/market-signals?date=2026-07-18&recompute=1"


def test_dashboard_uses_one_read_only_refresh_clock():
    template = Path("src/web/templates/index.html").read_text(encoding="utf-8")

    assert "setInterval(loadMarketSignals" not in template
    assert "setInterval(loadOdds123Timeline" not in template
    assert "setInterval(refreshDashboard, 30000)" in template
    assert "recompute=1" not in template
