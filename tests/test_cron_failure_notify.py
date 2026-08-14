"""P0-2 タスク3: notify_cron_failure のメール通知とクールダウンのテスト。

送信関数 (src.notifications.mailer._send) は mock し、実メールは送らない。
"""
import sqlite3
from datetime import datetime, timedelta

import pytest

from src.notifications import cron_alerts


JOB = "boatrace-race-detail-cron"


@pytest.fixture
def alert_env(tmp_path, monkeypatch):
    db_path = tmp_path / "alerts.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE system_status (
              check_name TEXT NOT NULL,
              check_date TEXT NOT NULL,
              status TEXT NOT NULL,
              message TEXT,
              detail_json TEXT,
              checked_at TEXT NOT NULL,
              PRIMARY KEY (check_name, check_date)
            )
            """
        )
    monkeypatch.setattr(
        cron_alerts, "db_connect", lambda *a, **k: sqlite3.connect(db_path)
    )
    monkeypatch.setenv("BOATRACE_ERROR_NOTIFY_TO", "admin@example.com")

    sends = []

    def fake_send(to, subject, body_text, body_html=None):
        sends.append({"to": to, "subject": subject, "body": body_text})
        return True

    # notify_cron_failure は遅延 import で mailer._send を呼ぶ
    monkeypatch.setattr("src.notifications.mailer._send", fake_send)
    return db_path, sends


def test_final_failure_sends_one_mail(alert_env):
    db_path, sends = alert_env

    assert cron_alerts.notify_cron_failure(JOB, "maintenance window ended degraded: detail") is True
    assert len(sends) == 1
    assert sends[0]["to"] == "admin@example.com"
    assert JOB in sends[0]["subject"]
    assert "degraded" in sends[0]["subject"]

    # クールダウン状態が system_status の既存行パターンで記録される
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT check_name, status FROM system_status WHERE check_name=?",
            (cron_alerts._CHECK_PREFIX + JOB,),
        ).fetchone()
    assert row is not None
    assert row[1] == "error"


def test_cooldown_suppresses_second_mail(alert_env):
    _db_path, sends = alert_env
    assert cron_alerts.notify_cron_failure(JOB, "first failure") is True
    assert cron_alerts.notify_cron_failure(JOB, "second failure") is False
    assert len(sends) == 1


def test_cooldown_is_per_job(alert_env):
    _db_path, sends = alert_env
    assert cron_alerts.notify_cron_failure(JOB, "failure A") is True
    assert cron_alerts.notify_cron_failure("boatrace-program-bootstrap-cron", "failure B") is True
    assert len(sends) == 2


def test_cooldown_expires_after_window(alert_env):
    db_path, sends = alert_env
    assert cron_alerts.notify_cron_failure(JOB, "first failure") is True

    # クールダウン行を 7 時間前に巻き戻すと再送される
    old = (datetime.now(cron_alerts.JST).replace(tzinfo=None) - timedelta(hours=7)).isoformat(
        timespec="seconds"
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE system_status SET checked_at=? WHERE check_name=?",
            (old, cron_alerts._CHECK_PREFIX + JOB),
        )
    assert cron_alerts.notify_cron_failure(JOB, "next failure") is True
    assert len(sends) == 2


def test_missing_recipient_skips_send(alert_env, monkeypatch):
    _db_path, sends = alert_env
    monkeypatch.delenv("BOATRACE_ERROR_NOTIFY_TO")
    assert cron_alerts.notify_cron_failure(JOB, "failure") is False
    assert sends == []


def test_send_failure_still_starts_cooldown(alert_env, monkeypatch):
    _db_path, sends = alert_env

    def broken_send(to, subject, body_text, body_html=None):
        sends.append("attempt")
        return False

    monkeypatch.setattr("src.notifications.mailer._send", broken_send)
    assert cron_alerts.notify_cron_failure(JOB, "failure") is True
    # 送信失敗でもクールダウンは始まる (壊れたメール経路への連投防止)
    assert cron_alerts.notify_cron_failure(JOB, "failure again") is False
    assert len(sends) == 1


def test_notify_never_raises(monkeypatch):
    monkeypatch.setenv("BOATRACE_ERROR_NOTIFY_TO", "admin@example.com")

    def broken_connect(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr(cron_alerts, "db_connect", broken_connect)
    sends = []
    monkeypatch.setattr(
        "src.notifications.mailer._send",
        lambda to, subject, body, html=None: sends.append(subject) or True,
    )
    # クールダウン状態が読めなくても可視性優先で送信し、例外は出さない
    assert cron_alerts.notify_cron_failure(JOB, "failure") is True
    assert len(sends) == 1
