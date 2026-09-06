"""グレード (SG/G1/G2/G3/一般) の絞り込みと内訳 (S21) のブラウザ検証。

前半は項目単体、後半は他の項目 (複数買い目・季節・保存した手法・リセット) との
組み合わせ。grade は約 1 割が欠損なので、絞り込み時に除外件数へ計上されること、
内訳では「不明」行として合計が N に一致することを画面で確かめる。
"""
from __future__ import annotations

import time
from urllib.parse import urljoin

import pytest
from playwright.sync_api import expect

from tests.e2e.test_kachisuji_e2e import (
    _confirmed_match_case,
    _search_db_rows,
    get_url,
    post_json,
)

GRADES = {"1": "SG", "2": "G1", "3": "G2", "4": "G3", "5": "一般"}


def _grade_chip(page, value: str):
    return page.locator(f'#gradeChips .wchip[data-value="{value}"]')


def _season_chip(page, season: str):
    return page.locator(f'#seasonChips .wchip[data-value="{season}"]')


def _add_ticket(page, a: int, b: int, c: int):
    before = page.locator("#extraTickets .ticket-row").count()
    page.locator("#btnAddTicket").click()
    row = page.locator("#extraTickets .ticket-row").nth(before)
    row.locator('[data-leg="1"]').select_option(str(a))
    row.locator('[data-leg="2"]').select_option(str(b))
    row.locator('[data-leg="3"]').select_option(str(c))


def _run_search(page, venue: str = "1"):
    page.locator("#fast").check()
    if venue:
        page.locator("#venue").select_option(venue)
    page.locator("#btnSearch").click()


def _capture_payloads(page) -> list[dict]:
    payloads: list[dict] = []
    page.route(
        "**/api/search",
        lambda route: (payloads.append(route.request.post_data_json), route.continue_()),
    )
    return payloads


def _kpi_n(page) -> int:
    return int(page.locator(".kpis .kpi").nth(2).locator(".v").inner_text())


def _condition_null_count(page) -> int:
    text = page.locator(".exclusions .condition-null dd").inner_text()
    return int("".join(ch for ch in text if ch.isdigit()))


def _table_rows(page, selector: str) -> list[list[str]]:
    rows = page.locator(f"{selector} tbody tr")
    out = []
    for i in range(rows.count()):
        cells = rows.nth(i).locator("td")
        out.append([cells.nth(j).inner_text() for j in range(cells.count())])
    return out


# ---------------------------------------------------------------------------
# 項目単体
# ---------------------------------------------------------------------------


def test_s21_grade_chips_present_and_toggle(page):
    for value, label in GRADES.items():
        chip = _grade_chip(page, value)
        expect(chip).to_be_visible()
        expect(chip).to_contain_text(label)
        expect(chip).to_have_attribute("aria-pressed", "false")
    _grade_chip(page, "1").click()
    expect(_grade_chip(page, "1")).to_have_attribute("aria-pressed", "true")
    _grade_chip(page, "1").click()
    expect(_grade_chip(page, "1")).to_have_attribute("aria-pressed", "false")


def test_s21_no_chip_sends_no_grade_key(page):
    payloads = _capture_payloads(page)
    _run_search(page)
    expect(page.locator(".kpis")).to_be_visible(timeout=30_000)
    assert "grade" not in payloads[-1]


def test_s21_selected_chips_are_sent_as_numbers(page):
    payloads = _capture_payloads(page)
    _grade_chip(page, "1").click()
    _grade_chip(page, "5").click()
    _run_search(page)
    expect(page.locator(".kpis")).to_be_visible(timeout=30_000)
    assert payloads[-1]["grade"] == [1, 5]


def test_s21_grade_table_always_appears_with_unknown_row(page):
    _run_search(page)
    expect(page.locator(".gradetbl")).to_be_visible(timeout=30_000)
    labels = [row[0][:2] for row in _table_rows(page, ".gradetbl")]
    assert "不明" in [label[:2] for label in labels], labels
    # 表の順序は SG → 一般 → 不明 の並びを保つ (不明は最後)
    assert labels[-1].startswith("不明")


def test_s21_grade_rows_add_up_to_the_headline_n(page):
    _run_search(page)
    expect(page.locator(".gradetbl")).to_be_visible(timeout=30_000)
    total = sum(int(row[1]) for row in _table_rows(page, ".gradetbl"))
    assert total == _kpi_n(page)


def test_s21_filtering_drops_unknown_row_and_raises_excluded_count(page):
    """絞り込むと「不明」行が消え、除外件数 (判定不能) が増えること。"""
    _run_search(page)
    expect(page.locator(".gradetbl")).to_be_visible(timeout=30_000)
    baseline_excluded = _condition_null_count(page)

    _grade_chip(page, "5").click()
    page.locator("#btnSearch").click()
    expect(page.locator(".kpis")).to_be_visible(timeout=30_000)
    page.wait_for_function(
        "() => [...document.querySelectorAll('.gradetbl tbody tr')].length === 1"
    )
    rows = _table_rows(page, ".gradetbl")
    assert rows[0][0].startswith("一般")
    assert _condition_null_count(page) > baseline_excluded


def test_s21_condition_summary_shows_labels_not_numbers(page):
    _grade_chip(page, "1").click()
    _grade_chip(page, "2").click()
    _run_search(page)
    expect(page.locator(".condition-summary")).to_be_visible(timeout=30_000)
    summary = page.locator(".condition-summary").inner_text()
    assert "グレード: SG・G1" in summary
    assert "グレード: 1" not in summary


def test_s21_reset_clears_grade_chips(page):
    _grade_chip(page, "3").click()
    page.locator("#btnReset").click()
    expect(_grade_chip(page, "3")).to_have_attribute("aria-pressed", "false")


@pytest.mark.parametrize("grade", [[0], [6], ["SG"], [1, 1]])
def test_s21_api_rejects_invalid_grades_with_a_readable_message(page, grade):
    response = post_json(page, "/api/search", {"grade": grade, "fast": True, "venue": 1})
    assert response.status == 400, response.text()
    assert "グレードは" in response.text()


def test_s21_all_five_grades_equals_no_filter(page):
    _run_search(page)
    expect(page.locator(".kpis")).to_be_visible(timeout=30_000)
    baseline_n = _kpi_n(page)
    baseline_excluded = _condition_null_count(page)
    for value in GRADES:
        _grade_chip(page, value).click()
    page.locator("#btnSearch").click()
    expect(page.locator(".kpis")).to_be_visible(timeout=30_000)
    # 結果領域は描き直しの瞬間に一時的に空になる。KPI が無い間は false を返して
    # 待ち続けないと、述語自体が TypeError で落ちる (探索テストで実際に起きた)。
    page.wait_for_function(
        """n => {
             const kpi = document.querySelectorAll('.kpis .kpi')[2];
             return !!kpi && kpi.querySelector('.v').textContent === n;
           }""",
        arg=str(baseline_n),
    )
    assert _condition_null_count(page) == baseline_excluded, "5つ全部なら欠損も除外されない"


def test_s21_grade_chips_fit_the_mobile_baseline(browser, kachisuji_server):
    page = browser.new_page(viewport={"width": 390, "height": 844})
    page.goto(kachisuji_server, wait_until="networkidle")
    try:
        overflow = page.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        assert overflow <= 0, f"横スクロールが発生している: {overflow}px"
    finally:
        page.close()


# ---------------------------------------------------------------------------
# 組み合わせ
# ---------------------------------------------------------------------------


def test_s21_grade_season_and_two_tickets_in_one_search(page):
    """3 つ同時に使って、3 つの内訳表が全部出て、それぞれの合計が N に一致すること。"""
    payloads = _capture_payloads(page)
    _add_ticket(page, 1, 2, 5)
    _season_chip(page, "夏").click()
    _season_chip(page, "冬").click()
    _grade_chip(page, "5").click()
    _run_search(page)
    expect(page.locator(".tickettbl")).to_be_visible(timeout=30_000)
    expect(page.locator(".seasontbl")).to_be_visible()
    expect(page.locator(".gradetbl")).to_be_visible()

    sent = payloads[-1]
    assert sent["grade"] == [5]
    assert sent["season"] == ["夏", "冬"]
    assert len(sent["bet"]["tickets"]) == 2

    n = _kpi_n(page)
    assert sum(int(r[1]) for r in _table_rows(page, ".seasontbl")) == n
    assert sum(int(r[1]) for r in _table_rows(page, ".gradetbl")) == n
    # 季節は 2 つに絞ったので内訳も 2 行、グレードは 1 行
    assert len(_table_rows(page, ".seasontbl")) == 2
    assert len(_table_rows(page, ".gradetbl")) == 1


def test_s21_reset_clears_grade_season_and_ticket_rows_together(page):
    _add_ticket(page, 1, 2, 5)
    _season_chip(page, "春").click()
    _grade_chip(page, "2").click()
    page.locator("#btnReset").click()
    expect(page.locator("#extraTickets .ticket-row")).to_have_count(0)
    expect(_season_chip(page, "春")).to_have_attribute("aria-pressed", "false")
    expect(_grade_chip(page, "2")).to_have_attribute("aria-pressed", "false")
    expect(page.locator("#ticketCountLabel")).to_have_text("1点 / 1レース100円")


def test_s21_zero_rows_with_every_filter_does_not_break_the_page(page):
    _add_ticket(page, 1, 2, 5)
    _season_chip(page, "春").click()
    _grade_chip(page, "1").click()
    page.locator("#dateFrom").fill("2099-01-01")
    page.locator("#dateTo").fill("2099-12-31")
    _run_search(page, venue="")
    body = page.locator("#resultArea").inner_text()
    assert "NaN" not in body and "undefined" not in body
    expect(page.locator(".gradetbl")).to_have_count(0)


def test_s21_saved_strategy_with_grade_round_trips_and_matches(page):
    """グレード付きで保存 → 成績 → その日の合致レース。保存日の実グレードに合わせる。"""
    case = _confirmed_match_case()
    rows = _search_db_rows(
        "SELECT grade FROM asof_race_features WHERE race_date = ? AND jcd = ? "
        "AND grade IS NOT NULL LIMIT 1",
        (case["race_date"], case["jcd"]),
    )
    if not rows:
        pytest.skip("照合日のレースにグレードが無い")
    grade = int(rows[0]["grade"])
    conditions = {
        "bet": {"type": "sanrentan", "tickets": [
            {"first": 1, "second": 2, "third": 3}, {"first": 1, "second": 2, "third": 5}]},
        "grade": [grade],
        "venue": case["jcd"],
        "fast": True,
    }
    response = post_json(
        page, "/api/strategies",
        {"name": "s21-グレード往復", "conditions": conditions, "backtest": {"roi": 100.0, "n": 10}},
    )
    assert response.status == 200, response.text()
    strategy_id = response.json()["id"]
    try:
        page.reload(wait_until="networkidle")
        card = page.locator(f'.strategy-performance[data-strategy-id="{strategy_id}"]')
        expect(card).to_contain_text("s21-グレード往復")
        card.locator(".load-performance").click()
        card = page.locator(f'.strategy-performance[data-strategy-id="{strategy_id}"]')
        expect(card.locator(".performance-grid")).to_contain_text("探索時", timeout=60_000)

        matched = get_url(
            page, f"/api/strategies/{strategy_id}/matches", params={"date": case["race_date"]}
        )
        assert matched.status == 200, matched.text()
        body = matched.json()
        assert body["counts"]["matched"] + body["counts"]["pending"] > 0
        assert {item["bet"] for item in body["matched"] + body["pending"]} == {"3連単 1-2-3 ほか1点"}
    finally:
        page.request.delete(urljoin(page.url, f"/api/strategies/{strategy_id}"))


def test_s21_profit_curve_search_with_grade_and_tickets_has_no_nan(page):
    _add_ticket(page, 1, 2, 5)
    _grade_chip(page, "5").click()
    if page.locator("#fast").is_checked():
        page.locator("#fast").uncheck()
    page.locator("#venue").select_option("1")
    page.locator("#btnSearch").click()
    expect(page.locator(".kpis")).to_be_visible(timeout=180_000)
    assert "NaN" not in page.locator("#resultArea").inner_text()


def test_s21_twenty_tickets_with_grade_and_season_finish_promptly(page):
    combos = [
        (a, b, c)
        for a in (1, 2, 3) for b in (1, 2, 3, 4) for c in (3, 4, 5, 6)
        if len({a, b, c}) == 3 and (a, b, c) != (1, 2, 3)
    ][:19]
    for legs in combos:
        _add_ticket(page, *legs)
    _grade_chip(page, "5").click()
    _season_chip(page, "夏").click()
    started = time.perf_counter()
    _run_search(page)
    expect(page.locator(".tickettbl")).to_be_visible(timeout=60_000)
    elapsed = time.perf_counter() - started
    expect(page.locator(".tickettbl tbody tr")).to_have_count(20)
    assert elapsed < 15, f"20点＋グレード＋季節の検索に {elapsed:.1f}s"
