"""差分更新が data_status を焼き直すことを固定する回帰テスト。

2026-09-05 の実障害:
  朝、まだ predictions が 0 件の時刻に market-signals のスナップショットが焼かれ、
  その data_status には race_basic.count = 0 が入った。昼に予測 1008 件を投入して
  DB は正常になったが、cron が使う差分更新 (race_ids 付きの recompute) は
  data_status をキャッシュから引き写すだけで DB を見ないため、0 を運び続けた。
  prewarm_strategy_pages の検証が
  "race source incomplete: predictions=0/168" で落ち、
  boatrace-exhibition-detail-cron が約 10 時間 Failed run のままになった。
  フル再計算 (race_ids なし) を手で 1 回走らせるまで自力復旧しなかった。
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _market_signals_source() -> str:
    return (ROOT / "src" / "web" / "app.py").read_text(encoding="utf-8")


def test_data_status_is_reloaded_even_on_incremental_recompute():
    """差分更新でも _load_market_data_status() を必ず呼ぶこと。"""
    source = _market_signals_source()

    # 旧実装: 差分更新のときだけキャッシュの data_status を引き写していた。
    stale_pattern = (
        'dict(incremental_base_payload.get("data_status") or {})\n'
        "            if incremental_base_payload is not None and requested_race_ids"
    )
    assert stale_pattern not in source, (
        "差分更新が data_status をキャッシュから引き写している。"
        "一度欠けた状態で焼かれると DB が直っても永久に古い件数を返す"
    )
    assert "data_status = _load_market_data_status()" in source


def test_cache_flags_are_still_carried_over_from_the_incremental_base():
    """cache_only / cache_miss はソース件数とは別軸なので引き継ぎ続けること。"""
    source = _market_signals_source()
    anchor = source.index("data_status = _load_market_data_status()")
    window = source[anchor : anchor + 700]

    assert "cache_only" in window and "cache_miss" in window, (
        "キャッシュ由来を示す印まで捨てると、呼び出し側の判定が変わってしまう"
    )


def test_the_assignment_is_syntactically_a_plain_call():
    """条件式に戻されていないこと (戻すと同じ障害が再発する)。"""
    source = _market_signals_source()
    line = next(
        ln.strip()
        for ln in source.splitlines()
        if ln.strip().startswith("data_status = _load_market_data_status()")
    )
    node = ast.parse(line).body[0]
    assert isinstance(node, ast.Assign)
    assert isinstance(node.value, ast.Call), "無条件の呼び出しであること"
