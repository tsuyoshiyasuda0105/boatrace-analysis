"""季節 (春夏秋冬) の絞り込みと内訳 (S20) のブラウザ検証。

季節は race_date から導出するので DB に列は無い。画面側はチップで選び、
サーバは season の配列を受ける。内訳表は常に出て、絞り込むとその季節だけになる。
"""
from __future__ import annotations

import pytest
from playwright.sync_api import expect

from tests.e2e.test_kachisuji_e2e import post_json


def _chip(page, season: str):
    return page.locator(f'#seasonChips .wchip[data-value="{season}"]')


def _run_search(page):
    page.locator("#fast").check()
    page.locator("#venue").select_option("1")
    page.locator("#btnSearch").click()


def _capture_payloads(page) -> list[dict]:
    payloads: list[dict] = []
    page.route(
        "**/api/search",
        lambda route: (payloads.append(route.request.post_data_json), route.continue_()),
    )
    return payloads


def test_s20_season_chips_are_present_and_toggle(page):
    for season in ("春", "夏", "秋", "冬"):
        expect(_chip(page, season)).to_be_visible()
        expect(_chip(page, season)).to_have_attribute("aria-pressed", "false")

    _chip(page, "夏").click()
    expect(_chip(page, "夏")).to_have_attribute("aria-pressed", "true")
    _chip(page, "夏").click()
    expect(_chip(page, "夏")).to_have_attribute("aria-pressed", "false")


def test_s20_no_chip_sends_no_season_key(page):
    """季節を触らなければリクエストに season が無いこと (従来と同じ形)。"""
    payloads = _capture_payloads(page)
    _run_search(page)
    expect(page.locator(".kpis")).to_be_visible(timeout=30_000)
    assert "season" not in payloads[-1]


def test_s20_selected_chips_are_sent_as_a_list(page):
    payloads = _capture_payloads(page)
    _chip(page, "夏").click()
    _chip(page, "冬").click()
    _run_search(page)
    expect(page.locator(".kpis")).to_be_visible(timeout=30_000)
    assert payloads[-1]["season"] == ["夏", "冬"]


def test_s20_seasonal_table_always_appears_with_four_rows(page):
    _run_search(page)
    expect(page.locator(".seasontbl")).to_be_visible(timeout=30_000)
    rows = page.locator(".seasontbl tbody tr")
    expect(rows).to_have_count(4)
    names = [rows.nth(i).locator("td").first.inner_text()[:1] for i in range(4)]
    assert names == ["春", "夏", "秋", "冬"]


def test_s20_filtering_narrows_the_seasonal_table(page):
    _chip(page, "夏").click()
    _run_search(page)
    expect(page.locator(".seasontbl")).to_be_visible(timeout=30_000)
    rows = page.locator(".seasontbl tbody tr")
    expect(rows).to_have_count(1)
    assert rows.first.locator("td").first.inner_text().startswith("夏")


def test_s20_seasonal_rows_add_up_to_the_headline_n(page):
    """内訳の N を足すと、見出しの合致レース数と一致すること。"""
    _run_search(page)
    expect(page.locator(".seasontbl")).to_be_visible(timeout=30_000)
    headline_n = int(page.locator(".kpis .kpi").nth(2).locator(".v").inner_text())
    cells = page.locator(".seasontbl tbody tr td:nth-child(2)")
    total = sum(int(cells.nth(i).inner_text()) for i in range(cells.count()))
    assert total == headline_n


def test_s20_condition_summary_lists_seasons(page):
    _chip(page, "春").click()
    _chip(page, "秋").click()
    _run_search(page)
    expect(page.locator(".condition-summary")).to_be_visible(timeout=30_000)
    assert "季節: 春・秋" in page.locator(".condition-summary").inner_text()


def test_s20_reset_clears_season_chips(page):
    _chip(page, "冬").click()
    expect(_chip(page, "冬")).to_have_attribute("aria-pressed", "true")
    page.locator("#btnReset").click()
    expect(_chip(page, "冬")).to_have_attribute("aria-pressed", "false")


@pytest.mark.parametrize("season", [["Spring"], ["春夏"], ["夏", "夏"]])
def test_s20_api_rejects_invalid_seasons(page, season):
    response = post_json(
        page, "/api/search", {"season": season, "fast": True, "venue": 1}
    )
    assert response.status == 400, response.text()
    assert "季節" in response.text()


def test_s20_all_four_seasons_equals_no_filter(page):
    """4 つ全部選んでも、無指定と同じ結果になること。"""
    payloads = _capture_payloads(page)
    _run_search(page)
    expect(page.locator(".kpis")).to_be_visible(timeout=30_000)
    baseline_n = page.locator(".kpis .kpi").nth(2).locator(".v").inner_text()

    for season in ("春", "夏", "秋", "冬"):
        _chip(page, season).click()
    page.locator("#btnSearch").click()
    expect(page.locator(".kpis")).to_be_visible(timeout=30_000)
    page.wait_for_function(
        "n => document.querySelectorAll('.seasontbl tbody tr').length === 4"
    )
    assert payloads[-1]["season"] == ["春", "夏", "秋", "冬"]
    assert page.locator(".kpis .kpi").nth(2).locator(".v").inner_text() == baseline_n


def test_s20_first_ticket_legs_stay_on_one_line(page):
    """着順の組 (1着〜号艇) が 1 行に収まること。

    本番画面で「2着 3着 号艇」が別行に割れていた (2026-09-06 のスクリーンショット)。
    """
    tops = page.evaluate(
        """() => ['#pos1', '#pos2', '#pos3'].map(
             s => Math.round(document.querySelector(s).getBoundingClientRect().top))"""
    )
    assert len(set(tops)) == 1, f"着順の select が別行に割れている: {tops}"


def test_s20_added_ticket_legs_stay_on_one_line(page):
    page.locator("#btnAddTicket").click()
    tops = page.evaluate(
        """() => {
             const row = document.querySelector('#extraTickets .ticket-row');
             return [1, 2, 3].map(n => Math.round(
               row.querySelector(`[data-leg="${n}"]`).getBoundingClientRect().top));
           }"""
    )
    assert len(set(tops)) == 1, f"追加行の着順が別行に割れている: {tops}"


def test_s20_season_chips_fit_the_mobile_baseline(browser, kachisuji_server):
    page = browser.new_page(viewport={"width": 390, "height": 844})
    page.goto(kachisuji_server, wait_until="networkidle")
    try:
        overflow = page.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        assert overflow <= 0, f"横スクロールが発生している: {overflow}px"
    finally:
        page.close()
