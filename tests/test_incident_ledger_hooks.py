import logging

from scripts import render_regular_scheduler as regular
from src.notifications import cron_alerts, error_handler, incident_ledger


def _record(message="pool failed 12"):
    return logging.LogRecord(
        name="src.db.connection",
        level=logging.ERROR,
        pathname=__file__,
        lineno=10,
        msg=message,
        args=(),
        exc_info=None,
    )


def test_cron_records_even_without_mail_recipient(monkeypatch):
    calls = []
    monkeypatch.delenv("BOATRACE_ERROR_NOTIFY_TO", raising=False)
    monkeypatch.setattr(incident_ledger, "record_incident", lambda **kwargs: calls.append(kwargs) or "id")

    assert cron_alerts.notify_cron_failure("job-a", "terminal failure", detail={"attempt": 3}) is False
    assert calls[0]["category"] == "cron_failure"
    assert calls[0]["source"] == "job-a"
    assert calls[0]["notified"] is False


def test_error_handler_records_suppressed_events_and_never_propagates_ledger_failure(monkeypatch):
    calls = []
    monkeypatch.setattr(error_handler, "record_incident", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(error_handler.time, "time", lambda: 10_000.0)
    handler = error_handler.EmailErrorHandler("owner@example.com", rate_limit_sec=3600)
    monkeypatch.setattr("src.notifications.mailer._send", lambda *_a, **_k: True)

    handler.emit(_record("pool failed 12"))
    handler.emit(_record("pool failed 99"))
    assert len(calls) == 2
    assert calls[0]["category"] == "app_error"
    assert calls[0]["notified"] is True
    assert calls[1]["notified"] is False

    monkeypatch.setattr(error_handler, "record_incident", lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("down")))
    handler.emit(_record("another family"))


def test_error_handler_installs_ledger_only_without_recipient(monkeypatch):
    logger = logging.Logger("ledger-only")
    monkeypatch.delenv("BOATRACE_ERROR_NOTIFY_TO", raising=False)
    assert error_handler.install_error_notifier(logger) is False
    assert len(logger.handlers) == 1
    assert isinstance(logger.handlers[0], error_handler.EmailErrorHandler)
    assert logger.handlers[0].to_addr == ""


def test_same_log_record_is_written_to_ledger_once_across_handlers(monkeypatch):
    calls = []
    monkeypatch.setattr(error_handler, "record_incident", lambda **kwargs: calls.append(kwargs))
    first_handler = error_handler.EmailErrorHandler("")
    second_handler = error_handler.EmailErrorHandler("")
    log_record = _record("one propagated error")
    first_handler.emit(log_record)
    second_handler.emit(log_record)
    assert len(calls) == 1


def test_watchdog_routes_alert_as_watchdog_incident(monkeypatch):
    notices = []
    monkeypatch.setattr(
        regular,
        "notify_cron_failure",
        lambda job, message, **kwargs: notices.append((job, message, kwargs)),
    )
    regular._watchdog_alert("pool-exhaustion", "pool issue", {"events": 4})
    assert notices[0][2]["incident_category"] == "watchdog"
    assert notices[0][2]["cooldown_hours"] == regular.WATCHDOG_ALERT_COOLDOWN_HOURS
