from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from scripts.check_tomorrow_readiness import evaluate_tomorrow_readiness


JST = ZoneInfo("Asia/Tokyo")


def test_tomorrow_readiness_pending_before_nightly_window() -> None:
    result = evaluate_tomorrow_readiness(
        now_jst=datetime(2026, 8, 8, 23, 5, tzinfo=JST),
        races=0,
        entries=0,
        predictions=0,
        start_predictions=0,
        nightly_success=False,
    )
    assert result.state == "pending"


def test_tomorrow_readiness_ready_when_all_sources_exist() -> None:
    result = evaluate_tomorrow_readiness(
        now_jst=datetime(2026, 8, 8, 23, 40, tzinfo=JST),
        races=12,
        entries=72,
        predictions=12,
        start_predictions=12,
        nightly_success=True,
    )
    assert result.state == "ready"


def test_tomorrow_readiness_blocked_after_window_without_success() -> None:
    result = evaluate_tomorrow_readiness(
        now_jst=datetime(2026, 8, 8, 23, 40, tzinfo=JST),
        races=12,
        entries=72,
        predictions=0,
        start_predictions=0,
        nightly_success=False,
    )
    assert result.state == "blocked"


def test_tomorrow_readiness_warns_when_success_record_but_sources_incomplete() -> None:
    result = evaluate_tomorrow_readiness(
        now_jst=datetime(2026, 8, 8, 23, 40, tzinfo=JST),
        races=12,
        entries=72,
        predictions=0,
        start_predictions=0,
        nightly_success=True,
    )
    assert result.state == "warning"
