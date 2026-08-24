# -*- coding: utf-8 -*-
"""Web の共有プールを温存しておくことを固定する回帰テスト。

2026-08-22 実障害: レース詳細が「準備中」に落ちた。プール計測は
failures=0 / peak_concurrent=1 / max_hold_ms=296 と健全なのに
max_wait_ms=2571 で、混雑ではなく「新規接続そのものが遅い」ことを示していた。
Render(シンガポール) から Supabase(東京) への接続は往復 + TLS で実測 2.5 秒。
min_size=1 かつ max_idle=120 だと 2 本目以降を毎回張り直し、その待ちが
リクエスト予算 (10秒) を食い潰していた。
"""
from pathlib import Path

SOURCE = Path("src/db/connection.py").read_text(encoding="utf-8")


def test_web_pool_preheats_its_connections():
    """Web は常用分を温めておく。ただし全 worker の合計が枠に収まる範囲で。

    2026-08-24: min_size を 4 -> 8 に上げたら、2 worker x 8 = 16 本が Supabase
    (Supavisor) のクライアント枠を超え、先に温まった worker が枠を占有した。
    もう一方は pool_available=0 のまま復帰せず、リクエストの約半分が 10 秒待って
    レース詳細の仮ページに落ちた。min_size は worker 数を掛けて収まる値にする。
    """
    assert "default_min_size = 0 if trigger else 4" in SOURCE, (
        "min_size=1 に戻すと 2 本目以降で毎回 2.5 秒の再接続を払う。"
        "逆に大きすぎると worker 間で枠を奪い合って片方が飢える"
    )


def test_idle_connections_are_retired_before_the_pooler_kills_them():
    """遊休接続はこちらから先に retire する。

    2026-08-24: max_idle=1200 (20分) で抱えていたところ、Supabase 側が先に
    黙って接続を切っていた。プールは「空き 3 本」と表示したまま死んだ接続を
    並べ、取り出すたびに check が失敗して捨て直すので 1 回の取得に 16.7 秒
    かかり、レース詳細が「準備しています」に落ちた。こちらから先に retire
    すれば、張り直しは min_size を満たす背景処理になりリクエストを待たせない。
    """
    import re

    m = re.search(r"max_idle=(\d+),", SOURCE)
    assert m, "max_idle を読めない"
    assert 60 <= int(m.group(1)) <= 300, (
        "長すぎると Supavisor に先に切られ、短すぎると張り直しが増える"
    )


def test_cron_processes_stay_lean():
    """cron 側は従来どおり必要時に 1 本だけ (Supavisor の枠を Web に残す)。"""
    assert "default_min_size = 0 if trigger" in SOURCE
    assert 'default_pool_size = "1" if trigger' in SOURCE


def test_pool_has_room_for_nested_connections_per_thread():
    """枠はスレッド数の 2 倍以上あること。

    2026-08-24 実障害: gunicorn は 1 プロセス 4 スレッド、プール枠も 4 だった。
    1 リクエストの中で入れ子に db_connect() する経路があるため、4 スレッドが
    同時に「1 本持って 2 本目を待つ」状態になると誰も進めず、5 秒の取得待ちを
    2 回払って (=10.15 秒) レース詳細が「準備しています」に落ちた。
    --threads を増やすときは枠も増やさないと同じ状態に戻るので、ここで縛る。
    """
    import re

    render_yaml = Path("render.yaml").read_text(encoding="utf-8")
    m = re.search(r"--threads\s+(\d+)", render_yaml)
    assert m, "render.yaml の gunicorn 起動行から --threads を読めない"
    threads = int(m.group(1))

    m2 = re.search(r'default_pool_size = "1" if trigger else "(\d+)"', SOURCE)
    assert m2, "default_pool_size を読めない"
    pool_size = int(m2.group(1))

    assert pool_size >= threads * 2, (
        f"スレッド {threads} に対して上限 {pool_size} では入れ子接続で取り合いになる"
    )

    m3 = re.search(r"default_min_size = 0 if trigger else (\d+)", SOURCE)
    assert m3, "default_min_size を読めない"
    assert int(m3.group(1)) <= pool_size, "常時確保が上限を超えることはない"


def test_idle_connections_are_validated_in_the_background(monkeypatch):
    """遊休接続の生死は、閲覧者を待たせずに裏で確かめる。

    2026-08-24 実障害: psycopg は貸し出す瞬間まで生死を確かめないため、
    Supabase 側に切られた接続が「空き」として並び続けた。閲覧者がその 1 本を
    引くと検査失敗と再接続を取得待ちの中で払い、pool_available=1 と表示されて
    いるのに読み出しが 18.0 秒かかって空振りした。
    """
    import threading

    import src.db.connection as connection

    checked = threading.Event()

    class _Pool:
        def check(self):
            checked.set()

    monkeypatch.setattr(connection, "_PG_POOL_CHECKER_STARTED", False)
    monkeypatch.setenv("BOATRACE_DB_POOL_CHECK_INTERVAL_SEC", "5")

    connection._start_pool_health_checker(_Pool())

    assert checked.wait(timeout=20), "背景で pool.check() が回っていない"


def test_background_check_can_be_disabled(monkeypatch):
    import src.db.connection as connection

    class _Pool:
        def check(self):  # pragma: no cover - 呼ばれないことが期待値
            raise AssertionError("無効化したのに検査が走った")

    monkeypatch.setattr(connection, "_PG_POOL_CHECKER_STARTED", False)
    monkeypatch.setenv("BOATRACE_DB_POOL_CHECK", "0")

    connection._start_pool_health_checker(_Pool())
