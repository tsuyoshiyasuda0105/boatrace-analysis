from pathlib import Path

from src.web import app as web_app


def test_exacta_niche_adopted_rows_backfill_display_confirmed():
    source = Path("src/web/app.py").read_text(encoding="utf-8")

    assert "def _ensure_exacta_niche_display_confirmed(signal: dict | None) -> dict | None:" in source
    assert 'if not signal.get("is_exacta_niche"):' in source
    assert 'if signal.get("is_reference"):' in source
    assert 'if "is_display_confirmed" in signal:' in source
    assert 'merged["is_display_confirmed"] = True' in source


def test_exacta_niche_main_paths_apply_display_confirmed_backfill():
    source = Path("src/web/app.py").read_text(encoding="utf-8")

    assert 'exacta_niche = _ensure_exacta_niche_display_confirmed(exacta_niche)' in source
    assert source.count('exacta_niche = _ensure_exacta_niche_display_confirmed(exacta_niche)') == 2


def test_cached_exacta_niche_rows_get_display_confirmed_backfill():
    payload = {
        "signals": {
            "20260809-24-06": {
                "race_id": "20260809-24-06",
                "l4": {
                    "level": "omura_14_exa",
                    "is_exacta_niche": True,
                    "is_reference": False,
                },
            },
            "20260809-24-07": {
                "race_id": "20260809-24-07",
                "l4": {
                    "level": "morning_watch_demo",
                    "is_exacta_niche": True,
                    "is_reference": True,
                },
            },
        }
    }

    got = web_app._backfill_market_signal_display_flags(payload)

    assert got["signals"]["20260809-24-06"]["l4"]["is_display_confirmed"] is True
    assert "is_display_confirmed" not in got["signals"]["20260809-24-07"]["l4"]
