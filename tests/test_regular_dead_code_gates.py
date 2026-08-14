from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DISABLED_PATHS = (
    "run_morning",
    "run_morning_catchup_if_needed",
    "run_tide_self_heal",
    "run_hourly",
    "run_accident_self_heal",
    "run_nightly",
    "run_roi_history_slot",
)


def test_render_regular_service_enables_daytime_lite():
    blueprint = (ROOT / "render.yaml").read_text(encoding="utf-8")
    regular_service = blueprint.split("\n    name: boatrace-regular-cron\n", 1)[1].split(
        "- type:", 1
    )[0]

    assert "BOATRACE_RENDER_DAYTIME_LITE" in regular_service
    assert 'value: "1"' in regular_service


def test_production_disabled_legacy_regular_paths_are_removed():
    source = (ROOT / "scripts" / "render_regular_scheduler.py").read_text(encoding="utf-8")

    for name in DISABLED_PATHS:
        assert f"def {name}(" not in source
        assert f"{name}(now)" not in source


def test_regular_main_keeps_only_the_lightweight_daytime_path():
    source = (ROOT / "scripts" / "render_regular_scheduler.py").read_text(encoding="utf-8")
    main = source.split("def main() -> int:", 1)[1]

    assert "run_lite_daytime_bootstrap(now)" in main
    assert '["scripts/poll_results.py", "--no-jitter"]' in main
    assert "run_top_page_snapshot(now, lightweight=True, environment_only=True)" in main
