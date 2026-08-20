import pytest

from src.db.connection import _placeholder_pg, _rewrite_sqlite_specific


def test_replace_unknown_table_fails_loudly():
    sql = "INSERT OR REPLACE INTO unknown_cache (id, value) VALUES (?, ?)"

    with pytest.raises(ValueError, match="unknown_cache.*_TABLE_PRIMARY_KEYS"):
        _rewrite_sqlite_specific(sql)


def test_ignore_unknown_table_keeps_do_nothing_semantics():
    sql = "INSERT OR IGNORE INTO unknown_cache (id, value) VALUES (?, ?)"

    rewritten = _rewrite_sqlite_specific(sql)

    assert rewritten.endswith("ON CONFLICT DO NOTHING")


def test_replace_appends_upsert_before_trailing_line_comment():
    sql = (
        "INSERT OR REPLACE INTO races (race_id, race_date) "
        "VALUES (?, ?); -- keep this from swallowing the upsert"
    )

    rewritten = _rewrite_sqlite_specific(sql)

    assert "--" not in rewritten
    assert rewritten.endswith(
        "ON CONFLICT (race_id) DO UPDATE SET race_date=EXCLUDED.race_date;"
    )


def test_replace_preserves_non_terminal_line_comment():
    sql = """INSERT OR REPLACE INTO races (race_id, race_date)
-- values are supplied by the collector
VALUES (?, ?)
"""

    rewritten = _rewrite_sqlite_specific(sql)

    assert "-- values are supplied by the collector" in rewritten
    assert rewritten.endswith(
        "ON CONFLICT (race_id) DO UPDATE SET race_date=EXCLUDED.race_date"
    )


def test_placeholder_escapes_literal_percent_only_for_parameterized_sql():
    sql = "SELECT * FROM races WHERE name LIKE ? AND note LIKE '%done%' AND score % 2 = 0"

    rewritten = _placeholder_pg(sql, escape_percent=True)

    assert "name LIKE %s" in rewritten
    assert "note LIKE '%%done%%'" in rewritten
    assert "score %% 2" in rewritten
    assert _placeholder_pg(sql) == (
        "SELECT * FROM races WHERE name LIKE %s "
        "AND note LIKE '%done%' AND score % 2 = 0"
    )


def test_placeholder_preserves_existing_markers_and_escaped_percent():
    sql = "SELECT %s, 'already %% escaped', 'it''s 50%'"

    assert _placeholder_pg(sql, escape_percent=True) == (
        "SELECT %s, 'already %% escaped', 'it''s 50%%'"
    )


def test_rewrite_handles_double_quoted_identifiers():
    """sync_to_supabase の識別子クォート化 (b49ca49) で翻訳が壊れた回帰の防止。

    2026-08-20 実障害: INSERT OR REPLACE INTO "stadiums" (...) が翻訳されず
    生のまま Postgres に届き syntax error → 夜間デルタ取込 (step27) が全滅した。
    """
    from src.db.connection import _rewrite_sqlite_specific

    quoted = (
        'INSERT OR REPLACE INTO "stadiums" '
        '("stadium_number", "name", "water") VALUES (?, ?, ?)'
    )
    rewritten = _rewrite_sqlite_specific(quoted)
    assert "INSERT OR REPLACE" not in rewritten
    assert "ON CONFLICT (stadium_number) DO UPDATE SET" in rewritten
    assert "name=EXCLUDED.name" in rewritten
    assert "water=EXCLUDED.water" in rewritten

    ignore = (
        'INSERT OR IGNORE INTO "applied_deltas" '
        '("name", "applied_at") VALUES (?, ?)'
    )
    assert "ON CONFLICT DO NOTHING" in _rewrite_sqlite_specific(ignore)
