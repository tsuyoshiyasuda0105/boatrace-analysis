from pathlib import Path

from jinja2 import Environment


ROOT = Path(__file__).resolve().parents[1]
APP_SOURCE = ROOT / "src" / "web" / "app.py"
RACE_TEMPLATE = ROOT / "src" / "web" / "templates" / "race.html"


def test_race_template_parses_and_contains_requested_fact_columns():
    source = RACE_TEMPLATE.read_text(encoding="utf-8")
    Environment().parse(source)

    assert "\u7d1a / \u9078\u624b\u6210\u7e3e" in source
    assert "racer-rate-grid" in source
    national_win = source.index("<small>\u5168\u56fd\u52dd\u7387</small>")
    national_top2 = source.index("<small>\u5168\u56fd2\u9023\u7387</small>")
    local_top2 = source.index("<small>\u5f53\u57302\u9023\u7387</small>")
    assert national_win < national_top2 < local_top2

    assert "p.national_top_1_percent" in source
    assert "p.accident_display_level == 'high'" in source
    assert "\u4e8b\u65450.7+" in source
    assert "\u4e8b\u65450.5+" in source
    assert "p.kimarite_skill.label" in source
    assert "p.tilt_adjustment" in source
    assert "tilt-chip" in source


def test_race_detail_facts_and_course_skill_are_pre_result_only():
    source = APP_SOURCE.read_text(encoding="utf-8")

    assert "e.national_top_1_percent" in source
    assert "pv.tilt_adjustment" in source
    assert "r.race_date < ?" in source
    assert "rr.course_number = c.boat_number" in source
    assert "1: ((\"\u9003\u3052\", 2),)" in source
    assert "2: ((\"\u5dee\u3057\", 3),)" in source
    assert "3: ((\"\u307e\u304f\u308a\", 4), (\"\u307e\u304f\u308a\u5dee\u3057\", 5))" in source
    assert "4: ((\"\u307e\u304f\u308a\", 4),)" in source


def test_motor_position_rows_finish_before_display_fact_helper():
    source = APP_SOURCE.read_text(encoding="utf-8")
    start = source.index("def _current_race_position_rows")
    end = source.index("def _attach_race_detail_display_facts", start)
    function_source = source[start:end]

    assert '_apply_motor_position_ranks(out, "_dash_metric"' in function_source
    assert '_apply_motor_position_ranks(out, "_turn_metric"' in function_source
    assert '_apply_motor_position_ranks(out, "_straight_metric"' in function_source
    assert function_source.rstrip().endswith("return out")
