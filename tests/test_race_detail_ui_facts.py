from pathlib import Path

from jinja2 import Environment


ROOT = Path(__file__).resolve().parents[1]
APP_SOURCE = ROOT / "src" / "web" / "app.py"
RACE_TEMPLATE = ROOT / "src" / "web" / "templates" / "race.html"
RACE_DETAIL_JS = ROOT / "src" / "web" / "static" / "race_detail.js"
STYLE_CSS = ROOT / "src" / "web" / "static" / "style.css"


def test_race_template_parses_and_contains_requested_fact_columns():
    source = RACE_TEMPLATE.read_text(encoding="utf-8")
    Environment().parse(source)

    assert "\u9078\u624b\u6210\u7e3e" in source
    assert "\u5e73\u5747ST" in source
    assert "racer-class-badge--name" in source
    assert "racer-rate-grid" in source
    national_win = source.index("<small>\u5168\u56fd\u52dd\u7387</small>")
    national_top2 = source.index("<small>\u5168\u56fd2\u9023\u7387</small>")
    local_top2 = source.index("<small>\u5f53\u57302\u9023\u7387</small>")
    assert national_win < national_top2 < local_top2

    assert "p.national_top_1_percent" in source
    assert "p.accident_display_level is defined" in source
    assert "事故" in source
    assert "p.escape_tag is defined" in source
    assert "逃げ" in source
    assert "p.kimarite_skill.label" not in source
    assert "p.tilt_adjustment" in source
    assert "tilt-chip" in source
    assert "racer-age-chip" in source
    assert "racer-branch-weight" in source
    assert "racer-risk-grid" in source
    assert "racer-course-win-sample" in source
    assert "p.branch_label" in source
    assert "p.weight" in source
    assert "p.flying_count" in source
    assert "p.late_count" in source
    assert "p.venue_recent10_course_win_rate" in source
    assert "p.national_course_win_rate" in source
    assert "p.national_course_top3_rate" in source
    assert "当地10R" in source
    assert "preds | sort(attribute='boat_number')" in source
    assert "p.avg_start_timing" in source
    assert "p.dash_time" in source
    assert "p.turn_time" in source
    assert "p.straight_time" in source


def test_start_comparison_is_rendered_after_six_boat_details():
    source = RACE_TEMPLATE.read_text(encoding="utf-8")

    details_heading = source.index("🚤 6艇詳細")
    details_close = source.index("</details>", details_heading)
    start_comparison = source.index('class="start-prediction"')

    assert details_heading < details_close < start_comparison
    detail_open = source.rfind('<details class="collapsible-section" open>', 0, details_heading)
    assert detail_open != -1


def test_race_detail_removes_top_candidate_and_top_pick_cards():
    source = RACE_TEMPLATE.read_text(encoding="utf-8")
    script = RACE_DETAIL_JS.read_text(encoding="utf-8")

    assert "TOP PICK" not in source
    assert 'class="top-pick"' not in source
    assert "race-signal-shell" not in source
    assert "data-race-signals-loading" not in source
    assert "market-signal-container" not in source
    assert "renderMarketSignal" not in script
    assert 'document.querySelectorAll(".top-pick, .market-signal")' in script
    assert 'document.querySelectorAll("[data-race-signals-loading]")' in script
    assert 'summaryText.includes("6艇詳細")' in script


def test_motor_inspector_is_moved_above_start_comparison():
    script = RACE_DETAIL_JS.read_text(encoding="utf-8")

    assert 'document.querySelector("[data-start-prediction]")' in script
    assert "insertBefore(inspectorShell, startPredictionShell)" in script


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
    assert "venue_recent10_course_win_rate" in source
    assert "national_course_win_rate" in source
    assert "national_course_top3_rate" in source
    assert "e.branch_number" in source
    assert "e.age" in source
    assert "e.weight" in source
    assert "e.flying_count" in source
    assert "e.late_count" in source
    assert "WHERE rn <= 10" in source
    assert "e.avg_start_timing" in source
    assert "original.dash_time" in source
    assert "original.turn_time" in source
    assert "original.straight_time" in source
    assert 'RACE_DETAIL_PAGE_CACHE_VERSION = "v7"' in source


def test_cached_predictions_include_same_display_facts_as_fallback_rows():
    source = APP_SOURCE.read_text(encoding="utf-8")
    start = source.index("def _race_predictions_from_cache")
    end = source.index("def _race_entry_fallback_rows", start)
    cached_source = source[start:end]

    assert "e.national_top_1_percent" in cached_source
    assert "e.branch_number" in cached_source
    assert "e.age" in cached_source
    assert "e.weight" in cached_source
    assert "e.flying_count" in cached_source
    assert "e.late_count" in cached_source
    assert "pv.tilt_adjustment" in cached_source
    assert '"national_top_1_percent"' in cached_source
    assert '"branch_number"' in cached_source
    assert '"age"' in cached_source
    assert '"weight"' in cached_source
    assert '"flying_count"' in cached_source
    assert '"late_count"' in cached_source
    assert '"tilt_adjustment"' in cached_source


def test_race_detail_page_cache_version_bumped_for_template_changes():
    source = APP_SOURCE.read_text(encoding="utf-8")

    assert 'RACE_DETAIL_PAGE_CACHE_VERSION = "v7"' in source


def test_mobile_racer_course_stats_do_not_overlap_identity_line():
    css = STYLE_CSS.read_text(encoding="utf-8")
    template = RACE_TEMPLATE.read_text(encoding="utf-8")

    assert ".racer-name-line > .racer-course-win-sample" in css
    assert "display: none;" in css[css.index(".racer-name-line > .racer-course-win-sample"):css.index(".racer-identity > .racer-course-win-sample")]
    assert ".racer-identity > .racer-course-win-sample" in css
    assert 'grid-template-columns: 44px minmax(0, 1fr);' in css
    assert "C3着内" in template or "C3逹蜀" in template


def test_motor_marks_use_same_original_exhibition_source_in_table_and_history():
    source = APP_SOURCE.read_text(encoding="utf-8")

    assert "def _motor_fact_grade_from_original_mark" in source
    assert '_motor_fact_grade_from_original_mark(p.get("dash_mark"), p.get("dash_rank"))' in source
    assert '_motor_fact_grade_from_original_mark(p.get("turn_mark"), p.get("turn_rank"))' in source
    assert '_motor_fact_grade_from_original_mark(p.get("straight_mark"), p.get("straight_rank"))' in source

    start = source.index("def _attach_race_detail_display_facts")
    end = source.index("def _motor_history_payload", start)
    display_facts = source[start:end]
    assert "original_marks = _original_exhibition_quality_marks([race_id]).get(race_id, {})" in display_facts
    assert "p.update(original_marks.get(boat_number, {}))" in display_facts


def test_motor_history_current_row_includes_result_and_new_cache_version():
    source = APP_SOURCE.read_text(encoding="utf-8")
    start = source.index("def _motor_history_payload")
    end = source.index("def _build_race_compat_analysis", start)
    payload_source = source[start:end]

    assert "LEFT JOIN race_results rr" in payload_source
    assert "rr.finishing_position" in payload_source
    assert '"finishing_position": current_finishing_position' in payload_source
    assert '"course_number": current_course_number' in payload_source
    assert 'cache_key = f"motor_history_v9:{race_id}:{boat_number}"' in source


def test_race_detail_request_uses_only_precomputed_display_tags():
    source = APP_SOURCE.read_text(encoding="utf-8")
    start = source.index("def race_detail(race_id: str):")
    end = source.index("@app.route", start)
    route_source = source[start:end]

    assert "_attach_kimarite_skill_tags" not in route_source
    assert "_attach_accident_watch_tags" not in route_source
    assert route_source.count("_attach_precomputed_race_detail_tags") == 2
    assert route_source.count("allow_ace_recompute=False") == 2


def test_race_detail_tag_snapshot_contains_all_three_tag_families():
    source = APP_SOURCE.read_text(encoding="utf-8")
    start = source.index("def _build_race_detail_tag_snapshot")
    end = source.index("def _race_detail_tag_snapshot", start)
    function_source = source[start:end]

    assert "escape_tag" in function_source
    assert "escape_rate >= 70.0" in function_source
    assert "kimarite_skill" not in function_source
    assert "accident_display_level" in function_source
    assert "is_ace_motor" in function_source


def test_motor_position_rows_finish_before_display_fact_helper():
    source = APP_SOURCE.read_text(encoding="utf-8")
    start = source.index("def _current_race_position_rows")
    end = source.index("RACE_DETAIL_TAG_CACHE_VERSION", start)
    function_source = source[start:end]

    assert '_apply_motor_position_ranks(out, "_dash_metric"' in function_source
    assert '_apply_motor_position_ranks(out, "_turn_metric"' in function_source
    assert '_apply_motor_position_ranks(out, "_straight_metric"' in function_source
    assert function_source.rstrip().endswith("return out")


def test_only_motor_number_click_opens_detail_panel():
    template = RACE_TEMPLATE.read_text(encoding="utf-8")
    script = RACE_DETAIL_JS.read_text(encoding="utf-8")

    assert 'class="racer-detail-btn"' not in template
    assert 'class="racer-name-static"' in template
    assert 'class="motor-history-btn"' in template
    assert "data-motor-position-boat" in script
    assert "openMotorHistory" in script
    assert "openRacerDetail" in script
    assert 'document.querySelectorAll(".motor-history-btn")' in script
    assert 'document.querySelectorAll(".racer-detail-btn")' not in script
    assert 'activeMotorBoatNumber === requestedBoatNumber' in script
    assert 'keepCurrentHistoryVisible' in script
    assert 'event.preventDefault()' in script
    assert 'event.stopPropagation()' in script
    assert 'document.querySelector("[data-start-prediction]")' in script
    assert "insertBefore(inspectorShell, startPredictionShell)" in script
