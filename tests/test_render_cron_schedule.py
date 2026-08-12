from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_blueprint_has_separate_overnight_program_bootstrap():
    source = (ROOT / "render.yaml").read_text(encoding="utf-8")
    start = source.index("\n    name: boatrace-program-bootstrap-cron")
    end = source.index("\n    name: boatrace-odds-cron", start)
    block = source[start:end]
    assert 'schedule: "*/10 0,14-23 * * *"' in block
    assert "python scripts/render_program_bootstrap_scheduler.py" in block


def test_daytime_crons_are_limited_to_0800_2259_jst():
    source = (ROOT / "render.yaml").read_text(encoding="utf-8")
    for name in (
        "boatrace-odds-cron",
        "boatrace-regular-cron",
        "boatrace-exhibition-detail-cron",
    ):
        start = source.index(f"\n    name: {name}")
        next_service = source.find("\n  - type:", start + 1)
        block = source[start : next_service if next_service >= 0 else None]
        assert 'schedule: "*/5 23,0-13 * * *"' in block


def test_race_detail_runs_as_serial_overnight_maintenance():
    source = (ROOT / "render.yaml").read_text(encoding="utf-8")
    start = source.index("\n    name: boatrace-race-detail-cron")
    end = source.index("\n    name: boatrace-exhibition-detail-cron", start)
    block = source[start:end]
    assert 'schedule: "*/10 19-21 * * *"' in block
    assert "python scripts/render_maintenance_scheduler.py" in block


def test_accident_external_check_runs_after_maintenance_snapshot():
    source = (ROOT / "render.yaml").read_text(encoding="utf-8")
    start = source.index("\n    name: boatrace-accident-external-check-cron")
    assert 'schedule: "50 21 * * *"' in source[start:]


def test_odds_uses_two_snapshot_render_scheduler():
    source = (ROOT / "render.yaml").read_text(encoding="utf-8")
    start = source.index("\n    name: boatrace-odds-cron")
    end = source.index("\n    name: boatrace-regular-cron", start)
    assert "python scripts/odds_scheduler_render.py --no-jitter" in source[start:end]
