# -*- coding: utf-8 -*-
"""レース照合で会場を番号ではなく名前で出すこと (2026-09-10 要望)。

「24場 3R」では分からない。画面の JS に会場名の表を持たせたので、その表が
同じ画面の会場選択 (公式の場コード順) と食い違っていないかを確かめる。
本番 (src/web) と双子 (src/kachisuji_web) の両方を見る。
"""
from __future__ import annotations

from pathlib import Path
import re

import pytest

TEMPLATES = (
    Path("src/web/templates/kachisuji_search.html"),
    Path("src/kachisuji_web/templates/search.html"),
)


def _js_table(source: str) -> dict[int, str]:
    block = re.search(r"var VENUE_NAMES = \{(.*?)\};", source, re.S)
    assert block, "VENUE_NAMES が見つかりません"
    return {int(k): v for k, v in re.findall(r"(\d+): '([^']+)'", block.group(1))}


def _selector_table(source: str) -> dict[int, str]:
    """画面の会場選択 (本番はチェックボックス、双子は選択欄) から表を作る。"""
    boxes = re.findall(
        r'<input type="checkbox" name="venue" value="(\d+)"><span>([^<]+)</span>', source
    )
    if boxes:
        return {int(k): v.strip() for k, v in boxes}
    select = re.search(r'<select id="venue"[^>]*>(.*?)</select>', source, re.S)
    assert select, "会場選択が見つかりません"
    return {
        int(k): v.strip()
        for k, v in re.findall(r'<option value="(\d+)"[^>]*>([^<]+)</option>', select.group(1))
    }


@pytest.mark.parametrize("template", TEMPLATES, ids=["本番", "双子"])
def test_the_table_names_all_24_venues_like_the_venue_picker(template: Path) -> None:
    source = template.read_text(encoding="utf-8")
    table = _js_table(source)
    assert sorted(table) == list(range(1, 25))
    assert table == _selector_table(source)


@pytest.mark.parametrize("template", TEMPLATES, ids=["本番", "双子"])
def test_matched_races_show_the_venue_name(template: Path) -> None:
    source = template.read_text(encoding="utf-8")
    assert "escapeHtml(venueName(race.jcd))" in source
    assert "escapeHtml(race.jcd) + '場 '" not in source


@pytest.mark.parametrize("template", TEMPLATES, ids=["本番", "双子"])
def test_an_unknown_number_still_shows_as_a_number(template: Path) -> None:
    """表に無い番号が来ても空欄にしない。従来どおり「◯場」で出す。"""
    source = template.read_text(encoding="utf-8")
    assert "VENUE_NAMES[Number(jcd)] || (String(jcd) + '場')" in source


def test_both_copies_carry_the_same_table() -> None:
    first, second = (_js_table(path.read_text(encoding="utf-8")) for path in TEMPLATES)
    assert first == second
