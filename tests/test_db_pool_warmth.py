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
    """Web は使う本数を最初から温めておく (min_size == max_size)。"""
    assert "default_min_size = 0 if trigger else 4" in SOURCE, (
        "min_size=1 に戻すと 2 本目以降で毎回 2.5 秒の再接続を払う"
    )
    assert 'default_pool_size = "1" if trigger else "4"' in SOURCE, (
        "min_size と max_size を揃えておく"
    )


def test_idle_connections_are_not_discarded_between_races():
    """レース間隔程度の空きで接続を捨てない。"""
    assert "max_idle=1200," in SOURCE, "120秒では空き時間のたびに張り直す"
    assert "max_idle=120," not in SOURCE


def test_cron_processes_stay_lean():
    """cron 側は従来どおり必要時に 1 本だけ (Supavisor の枠を Web に残す)。"""
    assert "default_min_size = 0 if trigger" in SOURCE
    assert 'default_pool_size = "1" if trigger' in SOURCE
