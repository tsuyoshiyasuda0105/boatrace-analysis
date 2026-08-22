# -*- coding: utf-8 -*-
"""会員も非会員と同じレース詳細の土台を見ることを固定する回帰テスト。

発注者方針 (2026-08-22): 会員特典は「見られるページが増える / 使えるサービスが
増える」ことであって、同じレース詳細を速く見られることではない。

実障害: 会員だけがキャッシュ不在時に同期のフル生成へ進んでいたため、本番で
12-16 秒かかり接続待ち予算 (10秒) を超えて「準備中」に落ちていた。未ログインで
測ると 0.5-0.9 秒で、管理者ログイン時だけ再現するため気付きにくかった。
"""
from pathlib import Path

SOURCE = Path("src/web/app.py").read_text(encoding="utf-8")


def test_race_detail_does_not_branch_on_membership_for_rendering():
    """キャッシュ不在時に会員だけフル生成へ進む分岐を残さない。"""
    assert "if not is_member():\n                return _race_preparing_page_response" not in SOURCE, (
        "会員・非会員で描画経路を分けない"
    )


def test_race_detail_cache_key_is_shared_across_roles():
    """会員と非会員で同じキャッシュを共有する (HTML は同一)。"""
    assert (
        'return f"race_detail_page:{RACE_DETAIL_PAGE_CACHE_VERSION}:{race_id}"'
        in SOURCE
    ), "役割をキーに混ぜない"


def test_race_page_has_no_member_only_markup():
    """レース詳細の HTML に会員限定の出し分けが無いこと。

    出し分けが入るとキャッシュ共有が成立しなくなるので、その時は
    キー設計から見直す必要がある。
    """
    template = Path("src/web/templates/race.html").read_text(encoding="utf-8")
    for marker in ("is_member(", "can_use_backtest("):
        assert marker not in template, (
            f"{marker} が入ったらキャッシュ共有の前提が崩れる"
        )


def test_preparing_page_starts_a_background_build():
    """準備中を返すときは裏側で生成を起こす (放置しない)。"""
    start = SOURCE.index("def _race_preparing_page_response(race_id: str):")
    body = SOURCE[start : start + 900]
    assert "_start_race_detail_background_refresh(app, race_id)" in body, (
        "誰も作らないと次の展示cronまで準備中のままになる"
    )


def test_background_rebuilds_are_globally_capped():
    """裏側再生成の同時本数に全体上限があること。

    2026-08-22 実測: レース単位の重複防止だけでは、閲覧のたびに別レースの
    再生成が積み上がり、後続リクエストが 10-23 秒待たされて「準備中」になり、
    それがまた再生成を増やす連鎖になった。
    """
    assert "_RACE_DETAIL_REFRESH_MAX_CONCURRENT" in SOURCE
    assert (
        "if len(_RACE_DETAIL_REFRESH_IN_FLIGHT) >= _RACE_DETAIL_REFRESH_MAX_CONCURRENT"
        in SOURCE
    ), "全体本数のチェックが要る (レース単位の重複防止だけでは不足)"
