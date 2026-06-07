from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_l4_recent_daily_stats_require_odds_source():
    """Recent live-operation ROI must not use final payout as the selector."""
    src = (ROOT / "src/web/app.py").read_text(encoding="utf-8")
    assert 'STRICT_ODDS_DAILY_START = "2026-05-30"' in src
    assert 'not day_d.get("_strict_odds_only")' in src
    assert "if sdate >= STRICT_ODDS_DAILY_START:" in src
    assert "fav_pay is not None and rdate < STRICT_ODDS_DAILY_START" in src
    assert "fav_pay is not None and not strict_odds_only" in src
