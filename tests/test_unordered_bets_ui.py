"""2連複・3連複の画面 (A案) と、公開前の隠しスイッチ・保存の制限。

画面は本番 (src/web) とローカル双子 (src/kachisuji_web) の 2 コピー。e2e は
双子しか叩かないので、本番側はここで HTML を直接確かめる。
"""
from __future__ import annotations

from pathlib import Path
import re
import sqlite3

import pytest

import src.search.strategies as strategies
from src.features.asof_builder import create_output_schema
from src.kachisuji_web.app import create_app as create_twin_app
from src.search.strategies import save_strategy, unordered_bets_visible
from tests.test_kachisuji_design_pricing_step25 import _app, _login_as_paid_member

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = [
    ROOT / "src" / "web" / "templates" / "kachisuji_search.html",
    ROOT / "src" / "kachisuji_web" / "templates" / "search.html",
]
CSS = [
    ROOT / "src" / "web" / "static" / "kachisuji.css",
    ROOT / "src" / "kachisuji_web" / "static" / "kachisuji.css",
]
TRIO = {"type": "sanrenpuku", "first": 1, "second": 2, "third": 3}


def _options(html: str) -> list[str]:
    select = re.search(r'<select id="betType"[^>]*>(.*?)</select>', html, re.S).group(1)
    return re.findall(r'<option value="([a-z]+)"', select)


@pytest.fixture
def search_db(tmp_path: Path) -> Path:
    path = tmp_path / "search.db"
    with sqlite3.connect(path) as connection:
        create_output_schema(connection)
    return path


def test_unordered_bets_are_hidden_until_released() -> None:
    assert strategies.UNORDERED_BETS_RELEASED is False
    assert unordered_bets_visible(None) is False
    assert unordered_bets_visible("other") is False
    assert unordered_bets_visible("renpuku") is True


@pytest.mark.parametrize("preview, expected", [
    (None, ["sanrentan", "nirentan", "tansho"]),
    ("renpuku", ["sanrentan", "nirentan", "tansho", "sanrenpuku", "nirenpuku"]),
])
def test_production_page_shows_new_bet_types_only_with_the_preview_switch(
    monkeypatch, tmp_path: Path, preview, expected
) -> None:
    available_db = tmp_path / "kachisuji.db"
    available_db.touch()
    monkeypatch.setenv("KACHISUJI_DB", str(available_db))
    client = _app(monkeypatch).test_client()
    _login_as_paid_member(client)

    url = "/kachisuji" + (f"?preview={preview}" if preview else "")
    response = client.get(url)
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert _options(html) == expected
    # チェック欄そのものは常にあり、券種を選ぶまで隠れている
    assert 'id="boxes0"' in html
    assert len(re.findall(r'<input type="checkbox" value="[1-6]" data-box>', html)) == 6


@pytest.mark.parametrize("preview, expected", [
    (None, ["sanrentan", "nirentan", "tansho"]),
    ("renpuku", ["sanrentan", "nirentan", "tansho", "sanrenpuku", "nirenpuku"]),
])
def test_twin_page_shows_new_bet_types_only_with_the_preview_switch(
    search_db: Path, tmp_path: Path, preview, expected
) -> None:
    app = create_twin_app(search_db, tmp_path / "strategies.db")
    app.config.update(TESTING=True)
    html = app.test_client().get("/" + (f"?preview={preview}" if preview else "")).get_data(as_text=True)

    assert _options(html) == expected
    assert 'id="boxes0"' in html


def test_both_templates_and_stylesheets_carry_the_same_unordered_ui() -> None:
    """片方の画面だけ直る事故を防ぐ (2 コピーの同期)。"""
    markers = [
        "var UNORDERED_LEGS = {sanrenpuku: 3, nirenpuku: 2};",
        "function boxGroup(row)",
        "function collectTickets(lenient)",
        "collectConditions(true)",
        "艇を選んでください（いま",
        "{% if show_unordered_bets %}",
        # 買い目ごとの券種 (2026-09-20)
        "function rowKind(row)",
        "function typeSelectHtml(seq)",
        'class="ticket-type"',
        # チェックの引き継ぎは券種を変えた行だけ (行を消しても戻らない)
        "function refreshBetUI(seedRow)",
        "data-seeded-for",
    ]
    for path in TEMPLATES:
        source = path.read_text(encoding="utf-8")
        for marker in markers:
            assert marker in source, (path.name, marker)
    for path in CSS:
        source = path.read_text(encoding="utf-8")
        # .ticket-legs には display 指定があるので、hidden を明示的に効かせる必要がある
        assert ".ticket-legs[hidden], .ticket-boxes[hidden] { display: none; }" in source, path


def test_saving_an_unordered_strategy_is_refused_before_release(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "strategies.db"
    monkeypatch.setenv("KACHISUJI_STRATEGY_DB", str(db))
    with pytest.raises(ValueError, match="^買い目は2連複・3連複の手法保存を準備中です"):
        save_strategy("3連複", {"bet": TRIO}, db_path=db)
    # 既存の券種は従来どおり保存できる
    assert save_strategy("3連単", {"bet": {**TRIO, "type": "sanrentan"}}, db_path=db) > 0


def test_saving_an_unordered_strategy_works_after_release(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(strategies, "UNORDERED_BETS_RELEASED", True)
    db = tmp_path / "strategies.db"
    strategy_id = save_strategy("3連複", {"bet": TRIO}, db_path=db)
    assert strategies.get_strategy(strategy_id, db_path=db)["conditions"]["bet"]["type"] == "sanrenpuku"


def test_twin_save_api_explains_the_refusal_in_japanese(search_db: Path, tmp_path: Path) -> None:
    app = create_twin_app(search_db, tmp_path / "strategies.db")
    app.config.update(TESTING=True)
    response = app.test_client().post(
        "/api/strategies", json={"name": "3連複", "conditions": {"bet": TRIO}}
    )
    assert response.status_code == 400
    assert response.get_json()["error"].startswith("買い目は2連複・3連複の手法保存を準備中です")


def test_production_save_api_explains_the_refusal_in_japanese(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("KACHISUJI_STRATEGY_DB", str(tmp_path / "strategies.db"))
    client = _app(monkeypatch).test_client()
    _login_as_paid_member(client)
    response = client.post(
        "/kachisuji/api/strategies", json={"name": "3連複", "conditions": {"bet": TRIO}}
    )
    assert response.status_code == 400
    assert response.get_json()["error"].startswith("買い目は2連複・3連複の手法保存を準備中です")


def test_the_first_ticket_type_select_is_also_a_row_selector() -> None:
    """1 点目の券種は従来の #betType のまま。行ごとの扱いに入れるため印を付ける。"""
    for path in TEMPLATES:
        source = path.read_text(encoding="utf-8")
        assert '<select id="betType" name="bet_type" class="ticket-type"' in source, path.name


def test_deleting_a_ticket_row_does_not_reseed_the_boat_checkboxes() -> None:
    """行を消したときの再描画は引き継ぎ無しで呼ぶ (呼び出しの形で固定する)。

    引き継ぎ有りで呼ぶと、選び直そうとして全部外した行が 1・2・3 に戻る
    (2026-09-20 リッキーさん報告)。
    """
    for path in TEMPLATES:
        source = path.read_text(encoding="utf-8")
        remove_handler = source.split(".ticket-remove').addEventListener")[1][:200]
        assert "refreshBetUI();" in remove_handler, path.name
        assert "refreshBetUI(true)" not in remove_handler, path.name


MIXED = {"bet": {"type": "sanrentan", "tickets": [
    {"first": 1, "second": 2, "third": 3},
    {"type": "sanrenpuku", "first": 1, "second": 2, "third": 3},
]}}


def test_a_mixed_ticket_list_cannot_sneak_an_unreleased_bet_type_past_saving(tmp_path: Path) -> None:
    """点ごとに券種を選べるので、bet.type だけ見ると 2 点目の 3連複を見落とす。"""
    with pytest.raises(ValueError, match="^買い目は2連複・3連複の手法保存を準備中です"):
        save_strategy("混在", MIXED, db_path=tmp_path / "s.db")


def test_a_saved_mixed_strategy_with_an_unknown_bet_type_is_skipped(tmp_path: Path) -> None:
    """公開後に古いコードへ戻したときの守り。1 件のために全員の一覧を止めない。"""
    from src.search.strategies import _bet_kind_is_known

    future = {"bet": {"type": "sanrentan", "tickets": [
        {"first": 1, "second": 2, "third": 3},
        {"type": "mirai", "first": 1, "second": 2},
    ]}}
    assert _bet_kind_is_known(future, 1) is False
    assert _bet_kind_is_known(MIXED, 2) is True
    assert _bet_kind_is_known({}, 3) is True
