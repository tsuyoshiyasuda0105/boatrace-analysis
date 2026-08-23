# -*- coding: utf-8 -*-
"""バックテスト取込の鮮度点検が cron からも成立することを固定する。

2026-08-23 実障害: 永続ディスクは web サービスにしか繋がらない (Render の仕様)
ため、cron の朝の点検が毎回 "OperationalError: unable to open database file" で
失敗していた。設定ミスではなく構造上の制約なので、cron はファイルを直接
読まず web の内部 API に聞く。
"""
from pathlib import Path

SCHED = Path("scripts/render_maintenance_scheduler.py").read_text(encoding="utf-8")
BP = Path("src/web/kachisuji_bp.py").read_text(encoding="utf-8")


def test_web_reports_latest_race_date():
    """web の内部 API が取込の鮮度を返すこと。"""
    assert '"latest_race_date"' in BP
    assert "def _slim_latest_race_date" in BP
    assert "MAX(race_date)" in BP


def test_cron_falls_back_to_web_when_file_unreadable():
    """cron はファイルが開けなければ web に聞くこと。"""
    assert "_kachisuji_latest_date_via_web" in SCHED
    fallback_at = SCHED.index("remote = _kachisuji_latest_date_via_web()")
    except_at = SCHED.index("except Exception as exc:  # noqa: BLE001", 0)
    assert fallback_at > except_at, "ファイル読み取り失敗時の経路に置くこと"


def test_cron_fallback_never_breaks_the_check():
    """web への問い合わせが失敗しても点検自体は落ちないこと。"""
    start = SCHED.index("def _kachisuji_latest_date_via_web")
    body = SCHED[start : start + 1400]
    assert "except Exception" in body, "通信失敗で点検を止めない"
    assert "backtest_remote_error" in body, "失敗理由は残す"
