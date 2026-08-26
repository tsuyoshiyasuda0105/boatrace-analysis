# -*- coding: utf-8 -*-
"""会員の役割再確認が体感を悪くしないことの回帰テスト。

2026-08-26 リッキーさん報告「会員トップが遅い」。ページ自体は SQL 0 回・
0.4 秒で速い。遅さの出どころは会員だけが払うコストで、役割の再確認が
そのたびに直結接続を張り直す (Render→Supabase で実測 2.5 秒)。
60 秒間隔だと会員は 1 分に 1 度その待ちを踏む。
"""
from pathlib import Path

import src.web.auth as auth


def test_role_is_not_revalidated_every_minute():
    assert auth._SUPABASE_ROLE_REFRESH_TTL_SEC >= 300, (
        "再確認のたびに接続を張り直すので、短い間隔は会員の体感を直撃する"
    )


def test_staleness_safety_net_is_wider_than_the_refresh_interval():
    """再確認に失敗しても、その場でログアウトさせない余裕があること。"""
    assert auth._SUPABASE_ROLE_MAX_STALE_SEC > auth._SUPABASE_ROLE_REFRESH_TTL_SEC


def test_auth_connect_cost_is_measurable():
    """会員だけが払うコストは、切り分けのために外から読めること。"""
    source = Path("src/web/membership.py").read_text(encoding="utf-8")
    assert "LAST_AUTH_CONNECT_SEC" in source
    bp = Path("src/web/kachisuji_bp.py").read_text(encoding="utf-8")
    assert "auth_connect" in bp
