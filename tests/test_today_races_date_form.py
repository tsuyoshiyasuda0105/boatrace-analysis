# -*- coding: utf-8 -*-
"""本日のレース画面の日付フォームがレース一覧へ飛ぶことの回帰テスト。

2026-08-25 リッキーさん報告: 「日付を入れて表示と押しても今日の会場とレースが
出てこない」。日付フォームの飛び先がこの画面自身 (候補リスト) に固定されて
いたため、表示を押しても候補 2 件だけの同じ画面が再表示され、故障に見えた。
日付 + 表示 = その日の全会場・全レース (レース一覧)、過去の候補 = 過去履歴
ボタン、という分担にする。
"""
import re

from pathlib import Path


def test_today_races_date_form_leads_to_the_race_list():
    source = Path("src/web/app.py").read_text(encoding="utf-8")
    hits = re.findall(r'date_form_action=url_for\("member_today_races"\)', source)
    assert not hits, (
        "日付フォームを候補画面自身に戻すと、表示を押しても会場とレースが出ず"
        "故障に見える (2026-08-25)"
    )
