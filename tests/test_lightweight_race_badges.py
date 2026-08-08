from pathlib import Path


TEMPLATE = (
    Path(__file__).resolve().parents[1] / "src" / "web" / "templates" / "index.html"
)


def _source() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def test_strategy_cleanup_keeps_lightweight_data_badges() -> None:
    source = _source()

    strategy_selector = source.split(
        "const STRATEGY_BADGE_SELECTOR = ", 1
    )[1].split(";", 1)[0]
    assert "accident-watch-badge" not in strategy_selector
    assert "ace-motor-watch-badge" not in strategy_selector
    assert "kimarite-watch-badge" not in strategy_selector
    assert "escape-watch-badge" not in strategy_selector
    assert "entry-change-watch-badge" not in strategy_selector
    assert "item.querySelectorAll(STRATEGY_BADGE_SELECTOR)" in source


def test_lightweight_badges_render_before_unchanged_digest_return() -> None:
    source = _source()
    render_position = source.index(
        "renderLightweightRaceBadges(raceBadges, accidentWatch);"
    )
    unchanged_return_position = source.index(
        "if (nextDigest === lastRenderedSignalsDigest)"
    )

    assert render_position < unchanged_return_position


def test_lightweight_badges_include_escape_and_render_on_top_page() -> None:
    source = _source()

    assert ".escape-watch-badge" in source
    assert ".entry-change-watch-badge" in source
    assert "const escape = badges?.escape;" in source
    assert "const entryChange = badges?.entry_change;" in source
    assert "badge.textContent = '!';" in source
    assert "badge.textContent = '逃げ';" in source
    assert "badge.textContent = '事故';" in source
    assert "badge.textContent = 'M';" in source
    assert "race-badge-row" in source
    assert "initial lightweight badges render failed" in source

    render_position = source.index("initial lightweight badges render failed")
    disabled_return_position = source.index("if (!marketSignalsEnabled) return;")
    assert render_position < disabled_return_position
