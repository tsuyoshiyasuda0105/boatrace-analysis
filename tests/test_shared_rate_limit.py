# -*- coding: utf-8 -*-
"""P0-3 タスク2: プロセス横断 共有レートリミッタのテスト。

- SQLite / DATABASE_URL 未設定環境では従来のプロセス内リミッタへフォールバック
- 共有スロット取得ロジック (fake connection) の単体テスト
- 複数プロセス相当が同一 host スロットを奪い合っても、許可される
  リクエストの合算間隔が REQUEST_INTERVAL_SECONDS を下回らないこと
"""
import time

import config
from src.collectors import _http


class _Cursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeClock:
    """_http.time を差し替えるための fake (monotonic + sleep)。"""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class _FakeSlotDB:
    """scrape_rate_slots の UPDATE ... WHERE last_request_at <= now() - interval
    と同じ判定を fake クロック上で再現する接続。"""

    def __init__(self, clock: _FakeClock):
        self.clock = clock
        self.last: dict[str, float] = {}
        self.granted_at: list[float] = []

    def execute(self, sql, params=()):
        sql_flat = " ".join(sql.split())
        if sql_flat.startswith("INSERT INTO scrape_rate_slots"):
            self.last.setdefault(params[0], float("-inf"))
            return _Cursor(None)
        if sql_flat.startswith("UPDATE scrape_rate_slots"):
            host, interval = params[0], float(params[1])
            now = self.clock.monotonic()
            if self.last.get(host, float("-inf")) <= now - interval:
                self.last[host] = now
                self.granted_at.append(now)
                return _Cursor((1,))
            return _Cursor(None)
        raise AssertionError(f"unexpected sql: {sql_flat}")


def _reset_http_state(monkeypatch):
    monkeypatch.setattr(_http, "_last_request_at", 0.0)
    monkeypatch.setattr(_http, "_shared_disabled_until", 0.0)
    monkeypatch.setattr(_http, "_shared_conn", None)


# ------------------------------------------------------------
# 有効/無効の判定
# ------------------------------------------------------------

def test_disabled_without_postgres_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("BOATRACE_SHARED_RATE_LIMIT", raising=False)

    assert _http._shared_rate_limit_enabled() is False


def test_enabled_by_default_with_postgres_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/db")
    monkeypatch.delenv("BOATRACE_SHARED_RATE_LIMIT", raising=False)

    assert _http._shared_rate_limit_enabled() is True


def test_env_flag_disables_shared_limiter(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/db")
    monkeypatch.setenv("BOATRACE_SHARED_RATE_LIMIT", "0")

    assert _http._shared_rate_limit_enabled() is False


def test_rate_limit_host_mapping():
    assert _http._rate_limit_host(
        "https://www.boatrace.jp/owpc/pc/race/raceresult?rno=1"
    ) == "boatrace.jp"
    assert _http._rate_limit_host(
        "https://www1.mbrace.or.jp/od2/B/202608/b260812.lzh"
    ) == "mbrace.or.jp"


# ------------------------------------------------------------
# フォールバック (止血が本体を殺さないこと)
# ------------------------------------------------------------

def test_wait_interval_falls_back_to_local_limiter_on_sqlite(monkeypatch):
    """DATABASE_URL 無し (ローカル SQLite) では DB に触らず従来動作。"""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    _reset_http_state(monkeypatch)

    def _must_not_connect():
        raise AssertionError("shared conn must not be opened without postgres")

    monkeypatch.setattr(_http, "_get_shared_conn", _must_not_connect)

    start = time.monotonic()
    _http._wait_interval("boatrace.jp")  # _last_request_at=0 → sleep 無しで即返る
    assert time.monotonic() - start < 1.0
    assert _http._last_request_at > 0.0


def test_wait_interval_survives_db_failure(monkeypatch):
    """共有リミッタの DB 障害時はプロセス内リミッタで続行し、例外を出さない。"""
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/db")
    _reset_http_state(monkeypatch)

    def _broken_conn():
        raise RuntimeError("db unreachable")

    monkeypatch.setattr(_http, "_get_shared_conn", _broken_conn)

    _http._wait_interval("boatrace.jp")  # raise しないこと

    # 障害後はクールダウンが入り、以降しばらく共有リミッタを再試行しない
    assert _http._shared_disabled_until > time.monotonic()
    assert _http._try_shared_slot("boatrace.jp") is False


# ------------------------------------------------------------
# 共有スロット取得ロジック
# ------------------------------------------------------------

def test_acquire_slot_immediately_when_interval_elapsed(monkeypatch):
    clock = _FakeClock()
    clock.now = 100.0
    monkeypatch.setattr(_http, "time", clock)
    db = _FakeSlotDB(clock)

    assert _http._acquire_shared_slot(db, "boatrace.jp", 2.0) is True


def test_acquire_slot_waits_until_interval_then_succeeds(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(_http, "time", clock)
    db = _FakeSlotDB(clock)

    # 直前 (t=0) にリクエスト済み → t>=2.0 まで poll しながら待つ
    assert _http._acquire_shared_slot(db, "boatrace.jp", 2.0) is True
    assert _http._acquire_shared_slot(db, "boatrace.jp", 2.0) is True
    assert clock.now >= 2.0


def test_acquire_slot_times_out_and_returns_false(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(_http, "time", clock)

    class _NeverGrants:
        def execute(self, sql, params=()):
            sql_flat = " ".join(sql.split())
            if sql_flat.startswith("INSERT"):
                return _Cursor(None)
            return _Cursor(None)  # UPDATE は常に行なし

    got = _http._acquire_shared_slot(
        _NeverGrants(), "boatrace.jp", 2.0, max_wait_seconds=3.0, poll_seconds=0.4
    )

    assert got is False
    assert clock.now >= 3.0  # 上限まで待った後に諦める


def test_combined_request_interval_never_below_limit(monkeypatch):
    """受け入れ条件: 複数プロセス合算でも許可間隔が 2.0 秒を下回らない。

    2 ワーカーが同一 host のスロットを交互に奪い合うのを fake クロックで
    シミュレートし、許可タイムスタンプの隣接差分が全て interval 以上で
    あることを確認する。
    """
    clock = _FakeClock()
    monkeypatch.setattr(_http, "time", clock)
    db = _FakeSlotDB(clock)
    interval = config.REQUEST_INTERVAL_SECONDS  # 2.0 (変更禁止の規約値)

    for _ in range(5):  # worker A / worker B が交互にリクエスト
        assert _http._acquire_shared_slot(db, "boatrace.jp", interval) is True
        assert _http._acquire_shared_slot(db, "boatrace.jp", interval) is True

    grants = db.granted_at
    assert len(grants) == 10
    gaps = [b - a for a, b in zip(grants, grants[1:])]
    assert all(gap >= interval - 1e-9 for gap in gaps), gaps


def test_hosts_have_independent_slots(monkeypatch):
    clock = _FakeClock()
    clock.now = 50.0
    monkeypatch.setattr(_http, "time", clock)
    db = _FakeSlotDB(clock)

    assert _http._acquire_shared_slot(db, "boatrace.jp", 2.0) is True
    # 別 host は直後でも取得できる (host 単位で枠が分かれている)
    before = clock.now
    assert _http._acquire_shared_slot(db, "mbrace.or.jp", 2.0) is True
    assert clock.now == before
