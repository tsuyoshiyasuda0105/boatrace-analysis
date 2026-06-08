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


def test_l4_mid_is_not_listed_in_high_roi_candidates():
    """L4-Mid remains an observation badge, not a today's high-ROI pick."""
    src = (ROOT / "src/web/templates/index.html").read_text(encoding="utf-8")
    assert "T-A L4-Mid Tier A" not in src
    block_start = src.index("function renderTodaysPicks()")
    block_end = src.index("const closedAt = parseClosedAt", block_start)
    block = src[block_start:block_end]
    assert "const isMid132" in block
    assert "if (isMid132) return;" in block
