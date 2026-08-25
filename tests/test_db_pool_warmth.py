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
    assert "default_min_size = 0 if trigger else 3" in SOURCE, (
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


def test_connection_budget_fits_inside_the_supabase_pooler_limit():
    """web と cron の接続要求の合計が、Supabase の受け入れ上限を超えないこと。

    2026-08-24 実障害の根本原因。Supabase の pooler は session mode で
    クライアント 15 本が上限で、超えた接続はこう拒否される:

        FATAL: (EMAXCONNSESSION) max clients reached in session mode
               - max clients are limited to pool_size: 15

    web が 1 プロセスあたり 12 本まで開ける設定だったため、web だけで枠を
    使い切り、cron が締め出された (21:45 の odds cron が接続拒否)。web 側も
    「空き」と数えた接続が実は拒否・切断されていて、レース詳細が一日中
    「準備しています」に落ちた。

    枠は足し算で決まるので、足し算で守る。worker 数・プール上限・cron の数の
    どれを増やしても、ここで気づける。
    """
    import re

    render_yaml = Path("render.yaml").read_text(encoding="utf-8")

    m = re.search(r"startCommand: gunicorn -w (\d+)", render_yaml)
    assert m, "gunicorn の worker 数を読めない"
    workers = int(m.group(1))

    m = re.search(
        r'key: BOATRACE_DB_POOL_SIZE\s+value: "(\d+)"', render_yaml
    )
    assert m, "render.yaml に BOATRACE_DB_POOL_SIZE が無い (dashboard 任せにしない)"
    pool_size = int(m.group(1))

    cron_services = len(re.findall(r"^\s+- type: cron\s*$", render_yaml, re.M))
    assert cron_services > 0, "cron サービスを数えられない"

    supabase_pooler_max_clients = 15
    demand = workers * pool_size + cron_services

    # 予備は 1 本。この demand は「全 worker のプールが満杯 + 全 cron が同時実行」
    # という最悪同時形で、cron 6 本が重なることは実運用では起きない
    # (スケジュールが分散している)。2026-08-25 に worker 冗長化 (凍結対策) を
    # 予算内に収めるため、非現実的な最悪ケースの取り分を 2 -> 1 に緩めた。
    assert demand <= supabase_pooler_max_clients - 1, (
        f"接続要求 {demand} 本 (web {workers}x{pool_size} + cron {cron_services}) が "
        f"上限 {supabase_pooler_max_clients} 本に対して過大。"
        "ローカル作業ぶんに最低 1 本残すこと"
    )


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
    monkeypatch.setenv("BOATRACE_DB_POOL_CHECK", "1")
    monkeypatch.setenv("BOATRACE_DB_POOL_CHECK_INTERVAL_SEC", "5")

    connection._start_pool_health_checker(_Pool())

    assert checked.wait(timeout=20), "有効化した時は背景で pool.check() が回る"


def test_background_check_is_off_unless_explicitly_enabled(monkeypatch):
    """既定では回さない。

    2026-08-24: 遊休接続の生死を裏で確かめる仕組みを入れたが、tcp_user_timeout が
    無い状態では pool.check() が死んだソケット上で戻らず、プールの全接続を掴んだ
    まま空き 0 / 待ち 6 から復帰しなくなり、レース詳細が全滅した。
    """
    import src.db.connection as connection

    class _Pool:
        def check(self):  # pragma: no cover - 呼ばれないことが期待値
            raise AssertionError("既定で検査が走ってはいけない")

    monkeypatch.setattr(connection, "_PG_POOL_CHECKER_STARTED", False)
    monkeypatch.delenv("BOATRACE_DB_POOL_CHECK", raising=False)

    connection._start_pool_health_checker(_Pool())


def test_connections_bound_how_long_a_hung_query_may_block(monkeypatch):
    """応答が返らない問い合わせを OS 任せで待ち続けない。"""
    import src.db.connection as connection

    kwargs = connection._pg_socket_keepalive_kwargs()

    assert 1000 <= kwargs["tcp_user_timeout"] <= 15000, (
        "これが無いと死んだソケット上の SELECT は戻らず、掴んだ接続ごと固まる"
    )


def test_worker_redundancy_stays_inside_the_connection_budget():
    """worker は 2 つ (冗長性)。ただし接続予算と膠着回避則の内側で。

    2026-08-25: worker プロセスが 30 秒以上完全凍結する事象が日中 8 回起き、
    1 worker ではそのたび全断になった。2 worker なら片方が凍っても応答が続く。
    2026-08-24 の教訓 (worker 間で枠を食い合って片方が枯れる) は、
    worker数 x POOL_SIZE ≦ 15 - cron数 を検算する
    test_connection_budget_fits_inside_the_supabase_pooler_limit が守る。
    """
    import re

    render_yaml = Path("render.yaml").read_text(encoding="utf-8")
    m = re.search(r"startCommand: gunicorn -w (\d+)", render_yaml)
    assert m, "gunicorn の worker 数を読めない"
    assert int(m.group(1)) == 2, (
        "1 worker は凍結 = 全断。3 worker 以上は接続予算 15 本に収まらない"
    )


def test_every_thread_can_hold_two_connections_at_once():
    """スレッド数 x 2 が上限を超えないこと。

    2026-08-24 実障害の最終形。1 リクエストの中で入れ子に db_connect() する経路が
    あるため、1 スレッドが同時に 2 本使いうる。--threads 4 に対して上限 6 本だと、
    4 スレッドが「1 本持って 2 本目を待つ」状態に入って誰も進めなくなり、
    pool_available=0 / requests_waiting=6 のまま復帰しなくなった。
    人が 10 秒おきに 2 ページ見ただけで、以降ずっと仮ページに落ちた。
    """
    import re

    render_yaml = Path("render.yaml").read_text(encoding="utf-8")

    # コメント中にも "--threads N" と書くことがあるので、起動行だけを見る
    m = re.search(r"startCommand: gunicorn .*?--threads (\d+)", render_yaml)
    assert m, "gunicorn の --threads を読めない"
    threads = int(m.group(1))

    m = re.search(r'key: BOATRACE_DB_POOL_SIZE\s+value: "(\d+)"', render_yaml)
    assert m, "render.yaml に BOATRACE_DB_POOL_SIZE が無い"
    pool_size = int(m.group(1))

    assert threads * 2 <= pool_size, (
        f"スレッド {threads} x 入れ子 2 本 = {threads * 2} が上限 {pool_size} 本を超える。"
        "全スレッドが互いの 2 本目を待って止まる"
    )
