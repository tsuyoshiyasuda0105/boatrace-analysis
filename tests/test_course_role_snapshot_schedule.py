# -*- coding: utf-8 -*-
"""逃がし率 (壁) / 逃げ率タグ用スナップショットの夜間生成の回帰テスト。

2026-09-03: 逃がし率タグ本番化に伴い、進入変更スナップショットと同じ
04-07 時メンテ窓で racer_course_role_snapshots を毎晩作る配線を追加した。
これが外れるとタグ用データが更新されず、翌日から画面のタグが止まる。
"""
from datetime import datetime, timedelta, timezone

from scripts import render_regular_scheduler as regular
from scripts import render_maintenance_scheduler as maintenance


JST = timezone(timedelta(hours=9))


def _now(hour: int = 5) -> datetime:
    return datetime(2026, 8, 16, hour, 0, tzinfo=JST)


def test_course_role_snapshots_attempt_today_and_tomorrow(monkeypatch):
    calls = []
    monkeypatch.setattr(
        regular,
        "run_course_role_snapshot",
        lambda target: calls.append(target) or target.endswith("17"),
    )
    monkeypatch.setattr(regular, "_notify_failure_best_effort", lambda *_a, **_k: None)

    result = regular.run_course_role_snapshots_nonfatal(_now())

    assert calls == ["2026-08-16", "2026-08-17"], "today と tomorrow の両方を作る"
    assert result == {"today": False, "tomorrow": True}


def test_course_role_snapshot_exception_is_isolated_per_date(monkeypatch):
    calls = []
    records = []

    def run(target):
        calls.append(target)
        if target.endswith("16"):
            raise RuntimeError("builder down")
        return True

    monkeypatch.setattr(regular, "run_course_role_snapshot", run)
    monkeypatch.setattr(regular, "record_task", lambda *a, **k: records.append((a, k)))
    monkeypatch.setattr(regular, "_notify_failure_best_effort", lambda *_a, **_k: None)

    result = regular.run_course_role_snapshots_nonfatal(_now())

    assert calls == ["2026-08-16", "2026-08-17"], "1 日失敗しても翌日は続行する"
    assert result == {"today": False, "tomorrow": True}
    assert records[0][0][:3] == ("render_course_role_snapshot", "2026-08-16", "failure")


def test_course_role_snapshot_skips_days_without_races(monkeypatch):
    records = []
    ran = []
    monkeypatch.setattr(regular, "race_count_for_date", lambda d: 0)
    monkeypatch.setattr(regular, "run_py", lambda *a, **k: ran.append(a) or True)
    monkeypatch.setattr(regular, "record_task", lambda *a, **k: records.append(a))

    ok = regular.run_course_role_snapshot("2026-08-16")

    assert ok is True, "レース 0 件の日は成功扱いで抜ける"
    assert ran == [], "レースが無い日はビルダーを起動しない"
    assert records[0][:3] == ("render_course_role_snapshot", "2026-08-16", "success")


def test_course_role_snapshot_builds_when_races_exist(monkeypatch):
    ran = []
    monkeypatch.setattr(regular, "race_count_for_date", lambda d: 144)
    monkeypatch.setattr(regular, "run_py", lambda *a, **k: ran.append(a[0]) or True)
    monkeypatch.setattr(regular, "course_role_snapshot_row_count", lambda d: 527)
    monkeypatch.setattr(regular, "record_task", lambda *a, **k: None)

    ok = regular.run_course_role_snapshot("2026-08-16")

    assert ok is True
    assert ran and ran[0][0] == "scripts/build_racer_course_role_stats.py"


def test_maintenance_integrity_phase_runs_course_role_snapshots(monkeypatch):
    """メンテ窓 (integrity フェーズ) から必ず呼ばれること。"""
    called = {"entry": False, "course": False}
    monkeypatch.setattr(
        maintenance.regular, "run_entry_change_snapshots_nonfatal",
        lambda now: called.__setitem__("entry", True) or {},
    )
    monkeypatch.setattr(
        maintenance.regular, "run_course_role_snapshots_nonfatal",
        lambda now: called.__setitem__("course", True) or {},
    )
    # 後続の重い処理は本テストの対象外なので無害化する。
    monkeypatch.setattr(maintenance.regular, "run_kachisuji_delta_apply_nonfatal", lambda now: True)
    monkeypatch.setattr(maintenance, "phase_success", lambda *a, **k: True)
    monkeypatch.setattr(maintenance, "record_phase", lambda *a, **k: None)
    monkeypatch.setattr(maintenance.regular, "run_roi_daily_self_heal", lambda now: True)

    try:
        maintenance.run_integrity_phase(_now())
    except Exception:
        # 対象は「course-role が呼ばれるか」だけ。後続の未モック処理での例外は許容。
        pass

    assert called["course"] is True, "integrity フェーズが course-role 生成を呼ぶこと"
