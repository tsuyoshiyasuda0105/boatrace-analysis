from pathlib import Path

from src.evaluation.course_fit_strategy import (
    ADOPTED_CODES,
    COURSE_FIT_STRATEGIES,
    evaluate_race,
    representative_match,
    score_race,
)


def _race_rows(stadium=8, race_number=5, grade=5, target=2):
    rows = []
    for boat in range(1, 7):
        rank_offset = 0 if boat == target else boat + 1
        rows.append({
            "race_id": "sample",
            "stadium_number": stadium,
            "race_number": race_number,
            "race_grade_number": grade,
            "boat_number": boat,
            "course_number": boat,
            "racer_number": 4000 + boat,
            "exhibition_time": 6.60 + rank_offset * 0.03,
            "exhibition_st": 0.02 + rank_offset * 0.03,
            "motor_top2": 45.0 if boat == target else 30.0,
            "finishing_position": boat,
            "kimarite": "\u9003\u3052",
        })
    return rows


def test_only_requested_codes_are_adopted():
    assert ADOPTED_CODES == ("C1", "C3", "C4", "C5", "C6", "C7", "C8")
    assert not {"C2", "C9", "C10"}.intersection(ADOPTED_CODES)


def test_tokoname_c1_matches():
    matches = evaluate_race(_race_rows(), {})
    assert [match["strategy"].code for match in matches] == ["C1"]


def test_biwako_overlaps_keep_strict_c4_as_representative():
    history = {(4004, 4): (100, 100, 100)}
    matches = evaluate_race(_race_rows(stadium=11, target=4), history)
    assert {match["strategy"].code for match in matches} == {"C4", "C6", "C7", "C8"}
    assert representative_match(matches)["strategy"].code == "C4"


def test_selection_does_not_use_current_result():
    rows = _race_rows(stadium=19, race_number=3, target=2)
    before = score_race(rows, {})
    for row in rows:
        row["finishing_position"] = 1 if row["boat_number"] == 6 else 6
        row["kimarite"] = "\u307e\u304f\u308a"
    after = score_race(rows, {})
    assert before == after
    assert [match["strategy"].code for match in evaluate_race(rows, {})] == ["C5"]


def test_app_registers_every_course_fit_key_and_venue():
    app_source = Path("src/web/app.py").read_text(encoding="utf-8")
    for strategy in COURSE_FIT_STRATEGIES:
        assert app_source.count(f'"{strategy.key}"') >= 3
    assert '"tokoname_coursefit_boat2_win": "tokoname"' in app_source
    assert '"biwako_coursefit_boat4_gap10_general_win": "biwako"' in app_source
    assert '"shimonoseki_coursefit_boat2_win": "shimonoseki"' in app_source
