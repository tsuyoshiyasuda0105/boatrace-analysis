from pathlib import Path


def test_render_odds_scheduler_limits_snapshot_labels():
    source = (Path("scripts") / "odds_scheduler_render.py").read_text(encoding="utf-8")

    assert '("T-5min", 5, 0.5)' in source
    assert '("T-1d", 24 * 60, 5)' in source
    assert "T-120min" not in source
    assert "T-4min" not in source
    assert "T-3min" not in source
    assert "T-2min" not in source
    assert '("T-1min", 1, 0.5)' not in source


def test_render_blueprint_uses_render_specific_odds_scheduler():
    source = Path("render.yaml").read_text(encoding="utf-8")

    assert "startCommand: python scripts/odds_scheduler_render.py --no-jitter" in source
    assert 'schedule: "*/5 23,0-13 * * *"' in source
