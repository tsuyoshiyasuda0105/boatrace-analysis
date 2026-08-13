from datetime import datetime


def test_task_log_today_uses_jst_date(monkeypatch):
    from src.db import task_log

    fake_now = datetime(2026, 8, 8, 0, 20, tzinfo=task_log.JST)

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == task_log.JST
            return fake_now

    monkeypatch.setattr(task_log, "datetime", _FakeDatetime)

    assert task_log._today() == "2026-08-08"


def test_task_log_record_serializes_jst_clock_without_offset(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "task-log.db"))
    from src.db import task_log

    fake_now = datetime(2026, 8, 8, 6, 30, 15, tzinfo=task_log.JST)

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == task_log.JST
            return fake_now

    monkeypatch.setattr(task_log, "datetime", _FakeDatetime)

    task_log.record("morning", "success")
    row = task_log.get_today("morning")

    assert row is not None
    assert row["run_date"] == "2026-08-08"
    assert row["finished_at"] == "2026-08-08T06:30:15"
    assert row["success_at"] == "2026-08-08T06:30:15"
