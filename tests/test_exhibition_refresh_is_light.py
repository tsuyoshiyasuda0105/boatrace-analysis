# -*- coding: utf-8 -*-
"""日中の展示 cron が軽いままであることを固定する回帰テスト。

2026-08-22 実障害: 5分毎の展示 cron が 1-2 レースの反映に 300-530 秒かかり、
その間ワーカーを占有して閲覧者のリクエストが「ただいま混み合っています」に
落ちていた。原因はページ描画ではなく (実測 SQL 3回 / 0.23秒)、同居していた
重い処理だった。
"""
from pathlib import Path

REFRESH = Path("scripts/refresh_race_detail_after_exhibition.py").read_text(
    encoding="utf-8"
)
MAINTENANCE = Path("scripts/render_maintenance_scheduler.py").read_text(
    encoding="utf-8"
)


def test_daytime_refresh_does_not_load_prediction_models():
    """予測は別プロセスが担うので 54MB のモデルを読み込まない。"""
    assert "cached_predictions_only=True" in REFRESH, (
        "本番は CPU 1 コアで、モデル読み込みが 5 分毎の実行を押し下げる"
    )


def test_daytime_refresh_does_not_run_nightly_recomputes():
    """展示反映フェーズから重い再計算を戻さない。

    build_derived_start_stats は signal_refresh 側にも別の呼び出しがあり、
    そちらは戦略判定 (事故率・STへこみ) が当日値を必要とするため残す。
    ここで見張るのは「展示ページを作り直すフェーズ」に混ざっていないこと。
    """
    assert "render_cache_predictions.py" not in REFRESH, (
        "夜間フェーズに同じ実行があり日中の分は重複"
    )
    marker = "generate_start_predictions.py"
    phase_start = REFRESH.index("if not _render_daytime_lite_mode():")
    phase = REFRESH[phase_start : REFRESH.index(marker, phase_start)]
    assert "_run_py([\"scripts/build_derived_start_stats.py" not in phase, (
        "展示反映フェーズでは履歴集計を回さない (夜間へ移設済み)"
    )


def test_daytime_refresh_keeps_post_exhibition_start_predictions():
    """展示後にしか作れない ST 予測は日中に残す。"""
    assert "generate_start_predictions.py" in REFRESH, (
        "6艇の展示タイム・展示STが揃って初めて作れるので夜間では手遅れ"
    )


def test_nightly_owns_the_moved_recomputes():
    """日中から外した処理が夜間で確実に走ること。"""
    assert "build_derived_start_stats.py" in MAINTENANCE
    assert "render_cache_predictions.py" in MAINTENANCE
