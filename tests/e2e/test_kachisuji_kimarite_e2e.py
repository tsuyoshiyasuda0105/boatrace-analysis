"""決まり手率の上限指定と複数指定 (S22) のブラウザ検証。

前半は項目単体、後半は他の項目 (季節・グレード・複数買い目・リセット・保存手法) との
組み合わせ。1 件だけのときは保存済み手法と同じ単数形式で送ることを最初に固定する。
"""
from __future__ import annotations

import time
from urllib.parse import urljoin

import pytest
from playwright.sync_api import expect

from tests.e2e.test_kachisuji_e2e import _confirmed_match_case, get_url, post_json


def _open_boat(page, boat: int = 1):
    """艇別条件のブロックを開く。1号艇だけ既定で open、他は閉じている。

    開いているものを押すと閉じてしまうので、必ず状態を見てから押す。
    """
    block = page.locator(f"#boat{boat}")
    if not block.evaluate("el => el.open"):
        block.locator("summary").click()
    page.wait_for_function(
        "id => { const el = document.getElementById(id); return !!el && el.open; }",
        arg=f"boat{boat}",
    )
    return block


def _first_row(page, boat: int = 1):
    return page.locator(f'.kimarite-row[data-boat="{boat}"]')


def _extra_rows(page, boat: int = 1):
    return page.locator(f"#b{boat}KimariteExtra .kimarite-row")


def _add_kimarite(page, boat: int, name: str, direction: str, rate: int):
    before = _extra_rows(page, boat).count()
    page.locator(f"#b{boat}KimariteAdd").click()
    expect(_extra_rows(page, boat)).to_have_count(before + 1)
    row = _extra_rows(page, boat).nth(before)
    row.locator(".kimarite-name").select_option(name)
    row.locator(".kimarite-dir").select_option(direction)
    row.locator(".kimarite-rate").fill(str(rate))
    return row


def _set_first(page, boat: int, name: str, direction: str, rate: int):
    page.locator(f"#b{boat}Kimarite").select_option(name)
    page.locator(f"#b{boat}KimariteDir").select_option(direction)
    page.locator(f"#b{boat}KimariteRate").fill(str(rate))


def _capture(page) -> list[dict]:
    payloads: list[dict] = []
    page.route(
        "**/api/search",
        lambda route: (payloads.append(route.request.post_data_json), route.continue_()),
    )
    return payloads


def _run_search(page, venue: str = "1"):
    page.locator("#fast").check()
    if venue:
        page.locator("#venue").select_option(venue)
    page.locator("#btnSearch").click()


def _kpi_n(page) -> int:
    return int(page.locator(".kpis .kpi").nth(2).locator(".v").inner_text())


# ---------------------------------------------------------------------------
# 項目単体
# ---------------------------------------------------------------------------


def test_s22_direction_select_offers_above_and_below(page):
    _open_boat(page, 1)
    options = page.locator("#b1KimariteDir option").all_inner_texts()
    assert options == ["以上", "以下"]


def test_s22_single_entry_is_sent_in_the_legacy_shape(page):
    """1 件だけなら保存済み手法と同じ単数形式で送ること。"""
    payloads = _capture(page)
    _open_boat(page, 1)
    _set_first(page, 1, "nige", "rate_min", 60)
    _run_search(page)
    expect(page.locator(".kpis")).to_be_visible(timeout=30_000)
    kim = payloads[-1]["boats"]["1"]["kimarite"]
    assert isinstance(kim, dict), kim
    assert kim == {"name": "nige", "rate_min": 60}


def test_s22_upper_bound_is_sent_as_rate_max(page):
    payloads = _capture(page)
    _open_boat(page, 1)
    _set_first(page, 1, "nige", "rate_max", 30)
    _run_search(page)
    expect(page.locator(".kpis")).to_be_visible(timeout=30_000)
    assert payloads[-1]["boats"]["1"]["kimarite"] == {"name": "nige", "rate_max": 30}


def test_s22_two_entries_are_sent_as_a_list(page):
    payloads = _capture(page)
    _open_boat(page, 1)
    _set_first(page, 1, "nige", "rate_min", 60)
    _add_kimarite(page, 1, "sashi", "rate_max", 5)
    _run_search(page)
    expect(page.locator(".kpis")).to_be_visible(timeout=30_000)
    assert payloads[-1]["boats"]["1"]["kimarite"] == [
        {"name": "nige", "rate_min": 60},
        {"name": "sashi", "rate_max": 5},
    ]


def test_s22_upper_bound_returns_more_races_than_lower_bound(page):
    """「以下」が実際に別の集合を返すこと (逃げ率が低い1号艇)。"""
    _open_boat(page, 1)
    _set_first(page, 1, "nige", "rate_min", 70)
    _run_search(page)
    expect(page.locator(".kpis")).to_be_visible(timeout=30_000)
    high = _kpi_n(page)

    page.locator("#b1KimariteDir").select_option("rate_max")
    page.locator("#b1KimariteRate").fill("30")
    page.locator("#btnSearch").click()
    expect(page.locator(".kpis")).to_be_visible(timeout=30_000)
    page.wait_for_function(
        "n => { const k = document.querySelectorAll('.kpis .kpi')[2];"
        " return !!k && k.querySelector('.v').textContent !== n; }",
        arg=str(high),
    )
    assert _kpi_n(page) != high


def test_s22_add_button_stops_at_six(page):
    _open_boat(page, 1)
    for _ in range(5):
        page.locator("#b1KimariteAdd").click()
    expect(_extra_rows(page, 1)).to_have_count(5)
    expect(page.locator("#b1KimariteAdd")).to_be_disabled()


def test_s22_rows_can_be_removed(page):
    _open_boat(page, 1)
    _add_kimarite(page, 1, "sashi", "rate_min", 10)
    _add_kimarite(page, 1, "makuri", "rate_min", 5)
    _extra_rows(page, 1).first.locator(".kimarite-remove").click()
    expect(_extra_rows(page, 1)).to_have_count(1)
    expect(page.locator("#b1KimariteAdd")).to_be_enabled()


def test_s22_condition_summary_lists_every_entry_with_direction(page):
    _open_boat(page, 1)
    _set_first(page, 1, "nige", "rate_min", 60)
    _add_kimarite(page, 1, "sashi", "rate_max", 5)
    _run_search(page)
    expect(page.locator(".condition-summary")).to_be_visible(timeout=30_000)
    summary = page.locator(".condition-summary").inner_text()
    assert "逃げ・勝率60%以上" in summary
    assert "差し・勝率5%以下" in summary


def test_s22_reset_removes_added_kimarite_rows(page):
    _open_boat(page, 1)
    _add_kimarite(page, 1, "sashi", "rate_min", 10)
    page.locator("#btnReset").click()
    expect(_extra_rows(page, 1)).to_have_count(0)
    expect(page.locator("#b1KimariteAdd")).to_be_enabled()


def test_s22_api_rejects_a_band_with_min_above_max(page):
    response = post_json(page, "/api/search", {
        "fast": True, "venue": 1,
        "boats": {"1": {"kimarite": {"name": "nige", "rate_min": 60, "rate_max": 40}}},
    })
    assert response.status == 400, response.text()
    assert "下限が上限" in response.text()


def test_s22_api_rejects_the_same_kimarite_twice(page):
    response = post_json(page, "/api/search", {
        "fast": True, "venue": 1,
        "boats": {"1": {"kimarite": [
            {"name": "nige", "rate_min": 10}, {"name": "nige", "rate_max": 90}]}},
    })
    assert response.status == 400, response.text()
    assert "1回だけ" in response.text()


# ---------------------------------------------------------------------------
# 組み合わせ
# ---------------------------------------------------------------------------


def test_s22_kimarite_with_season_grade_and_two_tickets(page):
    """4 つ同時に使い、送信内容と 3 つの内訳表の整合を確かめる。"""
    payloads = _capture(page)
    page.locator("#btnAddTicket").click()
    row = page.locator("#extraTickets .ticket-row").first
    row.locator('[data-leg="3"]').select_option("5")
    page.locator('#seasonChips .wchip[data-value="冬"]').click()
    page.locator('#gradeChips .wchip[data-value="5"]').click()
    _open_boat(page, 1)
    _set_first(page, 1, "nige", "rate_min", 50)
    _add_kimarite(page, 1, "sashi", "rate_max", 10)
    _run_search(page)
    expect(page.locator(".tickettbl")).to_be_visible(timeout=60_000)

    sent = payloads[-1]
    assert len(sent["bet"]["tickets"]) == 2
    assert sent["season"] == ["冬"]
    assert sent["grade"] == [5]
    assert len(sent["boats"]["1"]["kimarite"]) == 2

    n = _kpi_n(page)
    for selector in (".seasontbl", ".gradetbl"):
        rows = page.locator(f"{selector} tbody tr")
        total = sum(int(rows.nth(i).locator("td").nth(1).inner_text()) for i in range(rows.count()))
        assert total == n, selector


def test_s22_two_boats_each_with_two_kimarite(page):
    """別々の艇にそれぞれ複数指定しても混ざらずに送られること。

    合致 0 件でも成立するテストにする。KPI だけを待つと、条件が厳しくて 0 件の
    ときに「未探査」表示になって待ち続けてしまう。
    """
    payloads = _capture(page)
    _open_boat(page, 1)
    _set_first(page, 1, "nige", "rate_min", 40)
    _add_kimarite(page, 1, "nuki", "rate_max", 10)
    _open_boat(page, 4)
    _set_first(page, 4, "makuri", "rate_min", 5)
    _add_kimarite(page, 4, "makurizashi", "rate_min", 2)
    _run_search(page)
    expect(page.locator(".condition-summary")).to_be_visible(timeout=60_000)

    boats = payloads[-1]["boats"]
    assert [e["name"] for e in boats["1"]["kimarite"]] == ["nige", "nuki"]
    assert [e["name"] for e in boats["1"]["kimarite"]][0] == "nige"
    assert boats["1"]["kimarite"][1] == {"name": "nuki", "rate_max": 10}
    assert [e["name"] for e in boats["4"]["kimarite"]] == ["makuri", "makurizashi"]
    assert boats["4"]["kimarite"] == [
        {"name": "makuri", "rate_min": 5},
        {"name": "makurizashi", "rate_min": 2},
    ]


def test_s22_reset_clears_kimarite_tickets_and_chips_together(page):
    page.locator("#btnAddTicket").click()
    page.locator('#seasonChips .wchip[data-value="春"]').click()
    page.locator('#gradeChips .wchip[data-value="2"]').click()
    _open_boat(page, 1)
    _add_kimarite(page, 1, "sashi", "rate_min", 10)

    page.locator("#btnReset").click()

    expect(page.locator("#extraTickets .ticket-row")).to_have_count(0)
    expect(_extra_rows(page, 1)).to_have_count(0)
    expect(page.locator('#seasonChips .wchip[data-value="春"]')).to_have_attribute("aria-pressed", "false")
    expect(page.locator('#gradeChips .wchip[data-value="2"]')).to_have_attribute("aria-pressed", "false")


def test_s22_zero_result_with_every_filter_does_not_break(page):
    _open_boat(page, 1)
    _set_first(page, 1, "nige", "rate_min", 95)
    _add_kimarite(page, 1, "sashi", "rate_max", 0)
    page.locator('#gradeChips .wchip[data-value="1"]').click()
    page.locator("#dateFrom").fill("2099-01-01")
    page.locator("#dateTo").fill("2099-12-31")
    _run_search(page, venue="")
    body = page.locator("#resultArea").inner_text()
    assert "NaN" not in body and "undefined" not in body


def test_s22_saved_strategy_with_multiple_kimarite_round_trips(page):
    case = _confirmed_match_case()
    conditions = {
        "bet": {"type": "sanrentan", "first": 1, "second": 2, "third": 3},
        "venue": case["jcd"],
        "boats": {"1": {"kimarite": [
            {"name": "nige", "rate_min": 0}, {"name": "sashi", "rate_max": 100}]}},
        "fast": True,
    }
    response = post_json(page, "/api/strategies", {
        "name": "s22-決まり手往復", "conditions": conditions,
        "backtest": {"roi": 100.0, "n": 10}})
    assert response.status == 200, response.text()
    strategy_id = response.json()["id"]
    try:
        page.reload(wait_until="networkidle")
        card = page.locator(f'.strategy-performance[data-strategy-id="{strategy_id}"]')
        expect(card).to_contain_text("s22-決まり手往復")
        card.locator(".load-performance").click()
        card = page.locator(f'.strategy-performance[data-strategy-id="{strategy_id}"]')
        expect(card.locator(".performance-grid")).to_contain_text("探索時", timeout=60_000)
        matched = get_url(page, f"/api/strategies/{strategy_id}/matches",
                          params={"date": case["race_date"]})
        assert matched.status == 200, matched.text()
    finally:
        page.request.delete(urljoin(page.url, f"/api/strategies/{strategy_id}"))


def test_s22_kimarite_rows_fit_the_mobile_baseline(browser, kachisuji_server):
    page = browser.new_page(viewport={"width": 390, "height": 844})
    page.goto(kachisuji_server, wait_until="networkidle")
    try:
        before = page.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        block = page.locator("#boat1")
        if not block.evaluate("el => el.open"):
            block.locator("summary").click()
        for _ in range(3):
            page.locator("#b1KimariteAdd").click()
        after = page.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        assert after <= max(before, 0), f"横スクロールが増えた: {before}px→{after}px"
    finally:
        page.close()


def test_s22_search_with_kimarite_band_finishes_promptly(page):
    _open_boat(page, 1)
    _set_first(page, 1, "nige", "rate_min", 40)
    _add_kimarite(page, 1, "nuki", "rate_max", 20)
    started = time.perf_counter()
    _run_search(page)
    expect(page.locator(".kpis")).to_be_visible(timeout=60_000)
    assert time.perf_counter() - started < 15


def test_s22_any_whole_percent_is_accepted(page):
    """5 の倍数でない率でも検索できること。

    刻みが 5 のままだと 43% のような値がブラウザの検証に弾かれ、
    submit 自体が起きず「検索を押しても無反応」になっていた。
    """
    payloads = _capture(page)
    _open_boat(page, 1)
    _set_first(page, 1, "nige", "rate_min", 43)
    _add_kimarite(page, 1, "sashi", "rate_max", 7)
    _run_search(page)
    expect(page.locator(".kpis")).to_be_visible(timeout=60_000)
    assert payloads[-1]["boats"]["1"]["kimarite"] == [
        {"name": "nige", "rate_min": 43},
        {"name": "sashi", "rate_max": 7},
    ]


def test_s22_out_of_range_value_opens_the_collapsed_block(page):
    """折りたたみの中の不正値でも、無反応にならず該当ブロックが開くこと。

    閉じた <details> の中の入力は吹き出しを出せないため、以前は検索ボタンが
    まったく反応しない状態になっていた。
    """
    _open_boat(page, 2)
    _set_first(page, 2, "nige", "rate_min", 50)
    page.locator("#b2KimariteRate").fill("150")
    page.locator("#boat2 summary").click()
    expect(page.locator("#boat2")).not_to_have_attribute("open", "")

    page.locator("#btnSearch").click()

    expect(page.locator("#boat2")).to_have_attribute("open", "")
