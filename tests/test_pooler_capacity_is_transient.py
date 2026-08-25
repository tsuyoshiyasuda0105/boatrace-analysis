# -*- coding: utf-8 -*-
"""接続枠の枯渇を一時障害として扱うことの回帰テスト。

2026-08-25 リッキーさん報告「会員ログイン頻繁にアクセスできなくなる」。
Supabase の pooler が session mode の枠 (15) を使い切ると
"FATAL: (EMAXCONNSESSION) max clients reached in session mode" を返すが、
これが一時障害と判定されていなかった。会員は 60 秒ごとに権限を再確認する
(_refresh_supabase_membership_session) 造りで、非一時障害は例外を送出する
ため、枠が一瞬埋まっただけでログイン中の会員に 500 が返っていた。

同時に、認証・設定の誤りまで一時障害に化けないことも固定する。
"""
import psycopg
import pytest

from src.db.connection import is_transient_db_error


POOLER_FULL = (
    'connection failed: connection to server at "13.114.6.6", port 5432 failed: '
    "FATAL:  (EMAXCONNSESSION) max clients reached in session mode - "
    "max clients are limited to pool_size: 15"
)
AUTH_FAILED = (
    'connection failed: connection to server at "10.0.0.1", port 5432 failed: '
    'FATAL:  password authentication failed for user "postgres"'
)


@pytest.mark.parametrize(
    "message",
    [POOLER_FULL, "FATAL: sorry, too many clients already"],
)
def test_running_out_of_connection_slots_is_transient(message):
    assert is_transient_db_error(psycopg.OperationalError(message)), (
        "枠の枯渇は待てば通る。ここを非一時障害にすると会員に 500 が返る"
    )


def test_too_many_connections_sqlstate_is_transient():
    exc = psycopg.OperationalError("too many connections")
    exc.sqlstate = "53300"
    assert is_transient_db_error(exc)


@pytest.mark.parametrize("message", [AUTH_FAILED, "database \"nope\" does not exist"])
def test_credential_and_configuration_errors_stay_fatal(message):
    assert not is_transient_db_error(psycopg.OperationalError(message)), (
        "資格情報や設定の誤りを一時障害に化けさせると、原因が隠れて再試行し続ける"
    )


def test_plain_connection_failed_prefix_is_not_enough():
    """"connection failed" だけを目印にしない (認証失敗も同じ前置きで始まる)。"""
    from pathlib import Path

    source = Path("src/db/connection.py").read_text(encoding="utf-8")
    markers = source[source.index("for marker in ("):source.index("):\n            return True")]
    assert '"connection failed"' not in markers
