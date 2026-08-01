from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "prewarm_race_detail_tags.py"
SCHEDULER = ROOT / "scripts" / "render_regular_scheduler.py"


def test_daily_tag_prewarm_is_wired_into_morning_and_nightly_jobs():
    source = SCHEDULER.read_text(encoding="utf-8")

    morning = source.split("def run_morning", 1)[1].split("def tide_refresh_needed", 1)[0]
    nightly = source.split("def run_nightly", 1)[1].split("def main", 1)[0]
    assert '"scripts/prewarm_race_detail_tags.py", "--date", today' in morning
    assert '"scripts/prewarm_race_detail_tags.py", "--date", tomorrow' in nightly
    assert "hour=6, minute=0" in source


def test_tag_prewarm_covers_every_race_and_forces_snapshot_refresh():
    source = SCRIPT.read_text(encoding="utf-8")

    assert "WHERE race_date = ?" in source
    assert "_race_detail_tag_snapshot(str(race_id), recompute=True)" in source


def test_escape_tag_uses_boat1_national_course1_win_rate():
    source = (ROOT / "src" / "web" / "app.py").read_text(encoding="utf-8")

    assert "RACE_DETAIL_TAG_CACHE_VERSION = \"v3\"" in source
    assert "def _boat1_national_course1_win_stats" in source
    assert "WHERE race_id = ? AND boat_number = 1" in source
    assert "COALESCE(NULLIF(rr.course_number, 0), e.boat_number) = 1" in source
    assert "escape_rate >= 70.0" in source
    assert '"label": "逃げ"' in source
    assert "rate is not None and rate >= 0.5" in source
