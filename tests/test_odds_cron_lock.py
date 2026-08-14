# -*- coding: utf-8 -*-
"""P0-3 タスク5: odds cron の多重実行ガード (pg_try_advisory_lock) のテスト。

- ロック取得失敗 = 前回 tick 実行中 → base.main() を呼ばずスキップ
  (成功も記録しない)
- ロック取得成功 → 従来どおり実行し、終了時に unlock
- SQLite (ローカル) では常に実行 (ロック不要)
"""
import scripts.odds_scheduler_render as render_mod


class _FakePgConn:
    def __init__(self, lock_granted: bool):
        self._kind = "postgres"
        self.lock_granted = lock_granted
        self.executed: list[str] = []
        self.closed = False

    def execute(self, sql, params=()):
        self.executed.append(sql)
        return self

    def fetchone(self):
        return (self.lock_granted,)

    def close(self):
        self.closed = True


class _FakeSqliteConn:
    # _kind 属性なし = sqlite 扱い
    def __init__(self):
        self.executed: list[str] = []
        self.closed = False

    def execute(self, sql, params=()):  # pragma: no cover - 呼ばれないはず
        self.executed.append(sql)
        return self

    def close(self):
        self.closed = True


def test_lock_not_acquired_skips_base_main(monkeypatch):
    conn = _FakePgConn(lock_granted=False)
    monkeypatch.setattr(render_mod, "db_connect", lambda: conn)
    monkeypatch.setattr(render_mod, "log_deploy_revision", lambda *_: None)

    called = []
    monkeypatch.setattr(render_mod.base, "main", lambda: called.append(True))

    rc = render_mod.main()

    assert rc == 0  # Render は healthy のまま (次の tick が拾う)
    assert called == []  # base.main は実行されない = 偽装成功なし
    assert any("pg_try_advisory_lock" in sql for sql in conn.executed)
    # 取得できなかったロックを unlock してはならない
    assert not any("pg_advisory_unlock" in sql for sql in conn.executed)
    assert conn.closed


def test_lock_acquired_runs_and_unlocks(monkeypatch):
    conn = _FakePgConn(lock_granted=True)
    monkeypatch.setattr(render_mod, "db_connect", lambda: conn)
    monkeypatch.setattr(render_mod, "log_deploy_revision", lambda *_: None)

    called = []
    monkeypatch.setattr(render_mod.base, "main", lambda: called.append(True))

    rc = render_mod.main()

    assert rc == 0
    assert called == [True]
    assert any("pg_try_advisory_lock" in sql for sql in conn.executed)
    assert any("pg_advisory_unlock" in sql for sql in conn.executed)
    assert conn.closed
    # Render 用スナップショット規則が適用されていること (既存挙動の維持)
    assert render_mod.base.SNAPSHOT_RULES == render_mod.RENDER_SNAPSHOT_RULES


def test_sqlite_local_runs_without_advisory_lock(monkeypatch):
    conn = _FakeSqliteConn()
    monkeypatch.setattr(render_mod, "db_connect", lambda: conn)
    monkeypatch.setattr(render_mod, "log_deploy_revision", lambda *_: None)

    called = []
    monkeypatch.setattr(render_mod.base, "main", lambda: called.append(True))

    rc = render_mod.main()

    assert rc == 0
    assert called == [True]
    assert conn.executed == []  # advisory lock SQL は発行されない
    assert conn.closed
