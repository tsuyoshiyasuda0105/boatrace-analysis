from pathlib import Path

from scripts import prewarm_race_detail_tags as tag_prewarm


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "prewarm_race_detail_tags.py"
SCHEDULER = ROOT / "scripts" / "render_regular_scheduler.py"
PROGRAM_BOOTSTRAP = ROOT / "scripts" / "render_program_bootstrap_scheduler.py"
MAINTENANCE = ROOT / "scripts" / "render_maintenance_scheduler.py"


def test_daily_tag_prewarm_has_maintenance_owner_and_bounded_daytime_selfheal():
    source = SCHEDULER.read_text(encoding="utf-8")

    bootstrap = source.split("def run_lite_daytime_bootstrap", 1)[1].split("def tide_refresh_needed", 1)[0]
    maintenance = MAINTENANCE.read_text(encoding="utf-8")
    assert "def run_detail_pages_selfheal" in bootstrap
    assert '"scripts/prewarm_race_detail_tags.py"' in bootstrap
    assert '"--budget-sec", str(DETAIL_SELFHEAL_TAG_BUDGET_SEC)' in bootstrap
    assert '"render_detail_pages_selfheal"' in bootstrap
    assert '"--budget-sec", str(DETAIL_TAG_BUDGET_SEC)' in maintenance
    assert "def run_nightly(" not in source
    assert '"--missing-only"' in maintenance
    assert maintenance.index('"scripts/prewarm_race_detail_tags.py"') < maintenance.index('"scripts/prewarm_race_detail_pages.py"')
    assert "_at_or_after(now, 6, 30)" in PROGRAM_BOOTSTRAP.read_text(encoding="utf-8")


def test_entry_change_snapshot_records_task_runs():
    source = SCHEDULER.read_text(encoding="utf-8")
    block = source.split("def run_entry_change_snapshot", 1)[1].split("def task_success_exists", 1)[0]

    assert 'record_task("render_entry_change_snapshot", target_date, "success", detail="skip:no-races")' in block
    assert '"render_entry_change_snapshot"' in block
    assert '"success" if verified else "failure"' in block


def test_tag_prewarm_covers_every_race_and_forces_snapshot_refresh():
    source = SCRIPT.read_text(encoding="utf-8")

    assert "WHERE race_date = ?" in source
    assert "_race_detail_tag_snapshot(str(race_id), recompute=True)" in source


class _RaceRowsConnection:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, *_args, **_kwargs):
        return self

    def fetchall(self):
        return [("race-1",), ("race-2",), ("race-3",)]


def test_tag_prewarm_budget_keeps_progress_and_next_run_resumes(monkeypatch):
    cached = set()
    clock = {"value": 0.0}

    monkeypatch.setattr(tag_prewarm, "db_connect", _RaceRowsConnection)
    monkeypatch.setattr(
        tag_prewarm,
        "_missing_cached_race_ids",
        lambda race_ids: [race_id for race_id in race_ids if race_id not in cached],
    )
    monkeypatch.setattr(tag_prewarm.time, "perf_counter", lambda: clock["value"])

    def build(race_id, *, recompute):
        assert recompute is True
        cached.add(race_id)
        clock["value"] += 2.0
        return {"boats": {"1": {}}}

    monkeypatch.setattr(tag_prewarm, "_race_detail_tag_snapshot", build)

    first = tag_prewarm.prewarm("2026-08-16", budget_sec=1)
    assert first["cached"] == 1
    assert first["remaining"] == 2
    assert first["budget_exhausted"] is True
    assert cached == {"race-1"}

    second = tag_prewarm.prewarm("2026-08-16")
    assert second["skipped_existing"] == 1
    assert second["cached"] == 2
    assert second["remaining"] == 0
    assert second["budget_exhausted"] is False
    assert cached == {"race-1", "race-2", "race-3"}


def test_tag_prewarm_main_treats_budget_remaining_as_success(monkeypatch):
    monkeypatch.setattr(
        tag_prewarm,
        "prewarm",
        lambda *_args, **_kwargs: {"races": 3, "failed": 0, "remaining": 2},
    )
    monkeypatch.setattr(tag_prewarm.sys, "argv", ["prewarm", "--budget-sec", "1"])

    assert tag_prewarm.main() == 0


def test_escape_tag_uses_monthly_frozen_boat1_profile():
    source = (ROOT / "src" / "web" / "app.py").read_text(encoding="utf-8")

    assert 'RACE_DETAIL_TAG_CACHE_VERSION = "v6"' in source
    assert "def _boat1_monthly_escape_profile" in source
    assert "def _monthly_snapshot_window" in source
    assert "WHERE race_id = ? AND boat_number = 1" in source
    assert "COALESCE(NULLIF(rr1.course_number, 0), e1.boat_number) = 1" in source
    assert "escape_rate >= 70.0" in source
    assert '"snapshot_month": str(boat1_escape.get("snapshot_month") or "")' in source
    assert "escape_context_tag" in source
    assert "preferred_course" in source
    assert "entry_change_tag" in source
