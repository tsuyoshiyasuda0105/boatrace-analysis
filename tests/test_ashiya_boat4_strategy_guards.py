from pathlib import Path


def test_ashiya_boat4_lift_requires_a1_and_motor40_at_context_entry():
    app_source = Path("src/web/app.py").read_text(encoding="utf-8")

    assert 'if info.get("stadium") == 21\n                           and info.get("boat4_class") == 1' in app_source
    assert 'and (info.get("boat4_motor_top2") or 0) >= 40' in app_source


def test_ashiya_win4_requires_a1_boat4():
    app_source = Path("src/web/app.py").read_text(encoding="utf-8")

    assert '"key": "ashiya_win4_ace_kimarite_no_rain"' in app_source
    assert '"target_class": 1, "target_motor_min": 35.0' in app_source
    assert 'target_class_required = strategy.get("target_class")' in app_source
    assert 'if target_class_required is not None and target_class != int(target_class_required):' in app_source


def test_ashiya_boat4_lift_evaluators_reject_non_a1_or_weak_motor():
    app_source = Path("src/web/app.py").read_text(encoding="utf-8")

    assert "and boat4_class == 1\n                and boat4_motor_top2 >= 40.0\n                and ex_course == 4" in app_source
    assert "if stadium != 21 or boat4_class != 1 or boat4_motor_top2 < 40.0:" in app_source


def test_ashiya_boat4_roi_history_query_requires_a1_and_motor40():
    app_source = Path("src/web/app.py").read_text(encoding="utf-8")

    assert "AND e4.class_number = 1" in app_source
    assert "AND e4.assigned_motor_top_2_percent >= 40.0" in app_source
    assert "if int(boat4_class_no or 0) != 1 or float(boat4_motor_top2 or 0.0) < 40.0:" in app_source


def test_roi_display_and_history_use_display_confirmed_flag():
    app_source = Path("src/web/app.py").read_text(encoding="utf-8")

    assert 'is_display_confirmed = bool(l4.get("is_display_confirmed"))' in app_source
    assert 'status = "closed" if is_closed else ("confirmed" if is_display_confirmed else "waiting")' in app_source
    assert 'if not l4.get("is_display_confirmed"):\n                        continue' in app_source
