# -*- coding: utf-8 -*-
"""レース照合の「未確定」表示が読める言葉になっていることを固定する。

以前は b1_ex_dev のような内部名がそのまま出ていて、何を待てばよいか
分からなかった (2026-09-09)。表示側は JS なので Node で直接動かして確かめる。
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

TEMPLATES = (
    Path("src/web/templates/kachisuji_search.html"),
    Path("src/kachisuji_web/templates/search.html"),
)
CASES = {
    "weather": "天候 待ち",
    "wind_speed": "風速 待ち",
    "wind_dir": "風向き 待ち",
    "b1_ex_dev": "1号艇の展示タイム(会場平均比) 待ち",
    "b3_ex_st": "3号艇の展示ST 待ち",
    "b6_ex_rank": "6号艇の展示タイム順位 待ち",
}


def _run(template: Path, columns: list[list[str]]) -> list[str]:
    source = template.read_text(encoding="utf-8")
    start = source.index("var sameDayLabels")
    end = source.index("function renderMatches")
    script = (
        source[start:end]
        + "const cases = " + json.dumps(columns) + ";\n"
        + "console.log(JSON.stringify(cases.map(pendingReason)));"
    )
    result = subprocess.run(
        [shutil.which("node") or "node", "-e", script],
        capture_output=True, text=True, encoding="utf-8", check=True,
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.parent.parent.name)
def test_same_day_columns_are_shown_in_japanese(template: Path):
    if shutil.which("node") is None:
        pytest.skip("node が無い環境では表示側を実行できない")
    got = _run(template, [[column] for column in CASES])
    assert got == list(CASES.values())


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.parent.parent.name)
def test_many_pending_items_are_summarised(template: Path):
    """全部並べるとカードが読めなくなるので、3件で打ち切って残数を出す。"""
    if shutil.which("node") is None:
        pytest.skip("node が無い環境では表示側を実行できない")
    many = ["b1_ex_st", "b2_ex_st", "b3_ex_st", "b4_ex_st", "wind_dir"]
    got = _run(template, [many, ["weather", "wind_speed"], []])
    assert got[0] == "1号艇の展示ST / 2号艇の展示ST / 3号艇の展示ST ほか2件 待ち"
    assert got[1] == "天候 / 風速 待ち"
    assert got[2] == "未確定の項目があります", "列が空でも内部表現を出さない"


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.parent.parent.name)
def test_confirmed_and_pending_are_separate_bands(template: Path):
    source = template.read_text(encoding="utf-8")
    assert "match-band-pending" in source
    assert "展示・天候が出ると確定します" in source
    assert "group.matched.length" in source and "group.pending.length" in source
