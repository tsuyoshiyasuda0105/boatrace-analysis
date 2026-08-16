import logging

from src.notifications.error_handler import EmailErrorHandler


def _record(logger_name: str, message: str) -> logging.LogRecord:
    return logging.LogRecord(
        name=logger_name,
        level=logging.ERROR,
        pathname=__file__,
        lineno=10,
        msg=message,
        args=(),
        exc_info=None,
    )


def test_variable_pool_stats_share_one_cooldown_key(monkeypatch):
    sent = []
    monkeypatch.setattr("src.notifications.error_handler.record_incident", lambda **_kwargs: None)
    monkeypatch.setattr("src.notifications.mailer._send", lambda *args, **kwargs: sent.append(args))
    monkeypatch.setattr("src.notifications.error_handler.time.time", lambda: 10_000.0)
    handler = EmailErrorHandler("owner@example.com", rate_limit_sec=3600)

    first = _record(
        "src.db.connection",
        "postgres pool checkout failed stats={'pool_size': 4, 'pool_available': 0, 'requests_waiting': 12}",
    )
    second = _record(
        "src.db.connection",
        "postgres pool checkout failed stats={'pool_size': 3, 'pool_available': 0, 'requests_waiting': 27}",
    )

    assert handler._key(first) == handler._key(second)
    handler.emit(first)
    handler.emit(second)

    assert len(sent) == 1


def test_distinct_error_families_and_loggers_keep_separate_notifications(monkeypatch):
    sent = []
    monkeypatch.setattr("src.notifications.error_handler.record_incident", lambda **_kwargs: None)
    monkeypatch.setattr("src.notifications.mailer._send", lambda *args, **kwargs: sent.append(args))
    monkeypatch.setattr("src.notifications.error_handler.time.time", lambda: 10_000.0)
    handler = EmailErrorHandler("owner@example.com", rate_limit_sec=3600)

    handler.emit(_record("src.db.connection", "postgres pool checkout failed stats={'pool_size': 4}"))
    handler.emit(_record("src.db.connection", "postgres statement timeout after 8 seconds"))
    handler.emit(_record("src.web.app", "postgres pool checkout failed stats={'pool_size': 4}"))

    assert len(sent) == 3


def test_normalized_error_family_sends_again_at_one_hour_boundary(monkeypatch):
    sent = []
    monkeypatch.setattr("src.notifications.error_handler.record_incident", lambda **_kwargs: None)
    times = iter((10_000.0, 13_599.0, 13_600.0))
    monkeypatch.setattr("src.notifications.mailer._send", lambda *args, **kwargs: sent.append(args))
    monkeypatch.setattr("src.notifications.error_handler.time.time", lambda: next(times))
    handler = EmailErrorHandler("owner@example.com", rate_limit_sec=3600)

    for waiting in (2, 19, 7):
        handler.emit(
            _record(
                "src.db.connection",
                f"postgres pool checkout failed stats={{'requests_waiting': {waiting}}}",
            )
        )

    assert len(sent) == 2
