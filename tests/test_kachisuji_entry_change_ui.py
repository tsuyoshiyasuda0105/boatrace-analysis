# -*- coding: utf-8 -*-
"""進入変更リスクの入力欄が、検索エンジンの受け取る形とつながっていること。

画面は本番 (src/web) とローカル双子 (src/kachisuji_web) の 2 コピーある。
片方だけ直すと e2e は通るのに本番が古いままになるので、両方を同じ基準で見る。

選択肢の文字列 ("," 区切り) は JS がそのまま範囲に変換して API へ送る。
ここでは同じ変換を Python 側で再現し、実際に search_roi へ通して
「画面に出ている選択肢は必ず検索できる」ことを確かめる。
"""
from __future__ import annotations

from pathlib import Path
import re
import sqlite3

import pytest

from src.features.asof_builder import create_output_schema
from src.search.roi_search import search_roi

TEMPLATES = (
    Path("src/web/templates/kachisuji_search.html"),
    Path("src/kachisuji_web/templates/search.html"),
)
BET = {"type": "sanrentan", "first": 1, "second": 2, "third": 3}


def _text(template: Path) -> str:
    return template.read_text(encoding="utf-8")


def _risk_options(source: str) -> list[tuple[str, str]]:
    """<select id="entryRisk"> の選択肢を (値, 表示文字) で返す。"""
    block = re.search(
        r'<select id="entryRisk">(.*?)</select>', source, re.S
    )
    assert block, "進入変更リスクの選択欄が見つかりません"
    return re.findall(r'<option value="([^"]*)"[^>]*>([^<]*)</option>', block.group(1))


def _as_range(value: str) -> dict[str, float]:
    """JS と同じ変換。'…,10' → {max: 10} / '40,' → {min: 40}。"""
    low, high = (value.split(",") + [""])[:2]
    out: dict[str, float] = {}
    if low:
        out["min"] = float(low)
    if high:
        out["max"] = float(high)
    return out


@pytest.fixture(scope="module")
def db(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("ui") / "entry.db"
    with sqlite3.connect(path) as conn:
        create_output_schema(conn)
        conn.execute(
            "INSERT INTO asof_race_features (race_id,race_date,asof_date,built_at,"
            "schema_version,jcd,race_no,b2_entry_change_rate,result_sanrentan_json,"
            "payout_sanrentan_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("a", "2024-01-10", "2024-01-09", "x", 11, 12, 1, 12.0,
             '["1-2-3"]', '{"1-2-3":1000}'),
        )
    return path


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.parts[1])
def test_the_screen_offers_the_risk_filter(template: Path) -> None:
    source = _text(template)
    assert 'id="entryRisk"' in source
    assert "進入変更リスク" in source


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.parts[1])
def test_no_filter_is_the_first_and_default_choice(template: Path) -> None:
    """既定は「指定なし」。黙ってレースを 2 割に絞り込まない。"""
    options = _risk_options(_text(template))
    assert options[0][0] == ""
    assert options[0][1] == "指定なし"
    assert "selected" not in _text(template).split('id="entryRisk"')[1].split("</select>")[0]


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.parts[1])
def test_every_choice_on_screen_is_accepted_by_the_search(
    template: Path, db: Path
) -> None:
    for value, label in _risk_options(_text(template)):
        if not value:
            continue
        condition = _as_range(value)
        assert condition, f"「{label}」が空の範囲になっています"
        result = search_roi(
            db, {"bet": BET, "entry_change_risk": condition}, fast=True
        )
        assert result["n"] in (0, 1)


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.parts[1])
def test_choice_labels_match_the_numbers_they_send(template: Path) -> None:
    """表示の数字と、実際に送る値がずれていないこと。"""
    for value, label in _risk_options(_text(template)):
        if not value:
            continue
        shown = re.findall(r"(\d+)%", label)
        assert shown, f"「{label}」に数字が書かれていません"
        sent = _as_range(value)
        assert float(shown[0]) in sent.values(), (label, value)


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.parts[1])
def test_the_boat_panel_offers_the_rate(template: Path) -> None:
    source = _text(template)
    assert "EntryChangeCmp" in source
    assert "前づけ率" in source
    assert "addRange(boat, 'entry_change_rate'" in source


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.parts[1])
def test_the_summary_names_the_condition(template: Path) -> None:
    """保存した手法を開き直したとき、何で絞ったか読めること。"""
    source = _text(template)
    assert "rangeText('進入変更リスク'" in source
    assert "entry_change_rate: ['EntryChange', '%']" in source


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.parts[1])
def test_the_period_chip_says_where_the_data_starts(template: Path) -> None:
    """進入データは 2016/6 から。画面の帯と検索の下限をそろえる。"""
    source = _text(template)
    block = source.split('for="entryRisk"')[1].split("</div>")[0]
    assert "2016/6" in block
    assert "前づけ率" in source.split("legendbar")[1].split("</div>")[0]


def test_both_copies_carry_the_same_controls() -> None:
    """片方だけ直した状態を残さない。"""
    first, second = (_text(path) for path in TEMPLATES)
    for marker in (
        'id="entryRisk"',
        "EntryChangeCmp",
        "entry_change_risk",
        "addRange(boat, 'entry_change_rate'",
        "rangeText('進入変更リスク'",
    ):
        assert first.count(marker) == second.count(marker), marker
    assert _risk_options(first) == _risk_options(second)
