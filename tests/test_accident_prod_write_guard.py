import pytest

from src.db.connection import assert_safe_production_write


def test_local_process_cannot_write_to_prod_postgres(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.com/db")
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("BOATRACE_ALLOW_ACCIDENT_PROD_WRITE", raising=False)

    with pytest.raises(RuntimeError, match="local process would write to production Postgres"):
        assert_safe_production_write(
            action="rebuild_racer_accident_stats",
            allow_env_var="BOATRACE_ALLOW_ACCIDENT_PROD_WRITE",
        )


def test_render_process_can_write_to_prod_postgres(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.com/db")
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.delenv("BOATRACE_ALLOW_ACCIDENT_PROD_WRITE", raising=False)

    assert_safe_production_write(
        action="rebuild_racer_accident_stats",
        allow_env_var="BOATRACE_ALLOW_ACCIDENT_PROD_WRITE",
    )


def test_explicit_sqlite_target_is_allowed(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.com/db")
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("BOATRACE_ALLOW_ACCIDENT_PROD_WRITE", raising=False)

    assert_safe_production_write(
        action="cache_racer_accident_rank_snapshot",
        db_path="C:/tmp/local.sqlite3",
        allow_env_var="BOATRACE_ALLOW_ACCIDENT_PROD_WRITE",
    )
