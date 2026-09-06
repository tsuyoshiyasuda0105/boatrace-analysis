"""複数買い目 (S19) のブラウザ検証。

1 レースに複数点を賭けたときの UI・送信形式・画面に出る数値の整合を見る。
既存の単数前提の挙動を壊していないことは test_kachisuji_e2e.py 側が担保する。
"""
from __future__ import annotations

import pytest
from playwright.sync_api import expect

from tests.e2e.test_kachisuji_e2e import post_json


def _add_ticket(page, first: int, second: int | None = None, third: int | None = None):
    """買い目を 1 行追加して着順を設定する。"""
    before = page.locator("#extraTickets .ticket-row").count()
    page.locator("#btnAddTicket").click()
    expect(page.locator("#extraTickets .ticket-row")).to_have_count(before + 1)
    row = page.locator("#extraTickets .ticket-row").nth(before)
    row.locator('[data-leg="1"]').select_option(str(first))
    if second is not None:
        row.locator('[data-leg="2"]').select_option(str(second))
    if third is not None:
        row.locator('[data-leg="3"]').select_option(str(third))
    return row


def _run_search(page):
    page.locator("#fast").check()
    page.locator("#venue").select_option("1")
    page.locator("#btnSearch").click()


def test_s19_ticket_rows_can_be_added_and_removed(page):
    expect(page.locator("#extraTickets .ticket-row")).to_have_count(0)
    expect(page.locator("#ticketCountLabel")).to_have_text("1点 / 1レース100円")

    _add_ticket(page, 1, 2, 5)
    expect(page.locator("#ticketCountLabel")).to_have_text("2点 / 1レース200円")

    _add_ticket(page, 1, 3, 4)
    expect(page.locator("#ticketCountLabel")).to_have_text("3点 / 1レース300円")

    page.locator("#extraTickets .ticket-row").first.locator(".ticket-remove").click()
    expect(page.locator("#extraTickets .ticket-row")).to_have_count(1)
    expect(page.locator("#ticketCountLabel")).to_have_text("2点 / 1レース200円")


def test_s19_add_button_stops_at_the_twenty_ticket_cap(page):
    for index in range(19):
        page.locator("#btnAddTicket").click()
        expect(page.locator("#extraTickets .ticket-row")).to_have_count(index + 1)
    expect(page.locator("#ticketCountLabel")).to_have_text("20点 / 1レース2000円")
    expect(page.locator("#btnAddTicket")).to_be_disabled()


def test_s19_bet_type_change_hides_legs_in_every_row(page):
    _add_ticket(page, 2, 3, 4)
    row = page.locator("#extraTickets .ticket-row").first

    page.locator("#betType").select_option("nirentan")
    expect(row.locator('[data-legwrap="3"]')).to_be_hidden()
    expect(row.locator('[data-legwrap="2"]')).to_be_visible()

    page.locator("#betType").select_option("tansho")
    expect(row.locator('[data-legwrap="2"]')).to_be_hidden()
    expect(row.locator('[data-legwrap="3"]')).to_be_hidden()

    page.locator("#betType").select_option("sanrentan")
    expect(row.locator('[data-legwrap="2"]')).to_be_visible()
    expect(row.locator('[data-legwrap="3"]')).to_be_visible()


def test_s19_single_ticket_still_sends_the_legacy_shape(page):
    """1 点のときは従来形式で送ること。保存済み手法と同じ形を保つため。"""
    payloads: list[dict] = []
    page.route(
        "**/api/search",
        lambda route: (payloads.append(route.request.post_data_json), route.continue_()),
    )
    page.locator("#pos3").select_option("4")
    _run_search(page)
    expect(page.locator(".kpis")).to_be_visible(timeout=30_000)

    assert payloads, "検索リクエストが飛んでいない"
    bet = payloads[-1]["bet"]
    assert "tickets" not in bet
    assert bet == {"type": "sanrentan", "first": 1, "second": 2, "third": 4}


def test_s19_multiple_tickets_are_sent_as_a_ticket_list(page):
    payloads: list[dict] = []
    page.route(
        "**/api/search",
        lambda route: (payloads.append(route.request.post_data_json), route.continue_()),
    )
    _add_ticket(page, 1, 2, 5)
    _run_search(page)
    expect(page.locator(".kpis")).to_be_visible(timeout=30_000)

    bet = payloads[-1]["bet"]
    assert bet["type"] == "sanrentan"
    assert bet["tickets"] == [
        {"first": 1, "second": 2, "third": 3},
        {"first": 1, "second": 2, "third": 5},
    ]
    # 単数キーと tickets の併用はサーバが 400 で弾く形なので、送ってはいけない。
    assert "first" not in bet


def test_s19_breakdown_table_appears_only_for_multiple_tickets(page):
    _run_search(page)
    expect(page.locator(".kpis")).to_be_visible(timeout=30_000)
    expect(page.locator(".tickettbl")).to_have_count(0)
    expect(page.locator(".kpi")).to_have_count(4)

    _add_ticket(page, 1, 2, 5)
    page.locator("#btnSearch").click()
    expect(page.locator(".tickettbl")).to_be_visible(timeout=30_000)
    expect(page.locator(".tickettbl tbody tr")).to_have_count(2)
    expect(page.locator(".kpi")).to_have_count(5)


def test_s19_combined_roi_equals_the_average_of_the_breakdown(page):
    """画面の合算 ROI が、内訳 ROI の平均と一致すること。"""
    _add_ticket(page, 1, 2, 5)
    _run_search(page)
    expect(page.locator(".tickettbl")).to_be_visible(timeout=30_000)

    combined = float(
        page.locator(".kpis .kpi").first.locator(".v").inner_text().rstrip("%")
    )
    cells = page.locator(".tickettbl tbody tr td:nth-child(4)")
    rois = [float(cells.nth(i).inner_text().rstrip("%")) for i in range(cells.count())]
    assert rois, "内訳が空"
    # ROI は小数第 1 位に丸めているので、平均とは最大 0.1 ずれる。
    assert combined == pytest.approx(sum(rois) / len(rois), abs=0.11)


def test_s19_condition_summary_lists_every_ticket(page):
    _add_ticket(page, 1, 2, 5)
    _run_search(page)
    expect(page.locator(".condition-summary")).to_be_visible(timeout=30_000)
    summary = page.locator(".condition-summary").inner_text()
    assert "1-2-3" in summary
    assert "1-2-5" in summary
    assert "2点" in summary


def test_s19_duplicate_tickets_are_rejected_with_visible_guidance(page):
    """同じ目を 2 回入れたら、画面に理由が出ること。"""
    _add_ticket(page, 1, 2, 3)
    page.locator("#fast").check()
    page.locator("#btnSearch").click()
    alert = page.locator("#resultArea [role=alert]")
    expect(alert).to_be_visible(timeout=30_000)
    expect(alert).to_contain_text("重複")


def test_s19_api_rejects_mixing_single_keys_with_a_ticket_list(page):
    response = post_json(
        page,
        "/api/search",
        {
            "bet": {
                "type": "sanrentan",
                "first": 1,
                "second": 2,
                "third": 3,
                "tickets": [{"first": 1, "second": 2, "third": 5}],
            },
            "fast": True,
        },
    )
    assert response.status == 400, response.text()


def test_s19_api_rejects_more_than_twenty_tickets(page):
    tickets = []
    for first in range(1, 7):
        for second in range(1, 7):
            for third in range(1, 7):
                if len({first, second, third}) == 3 and len(tickets) < 21:
                    tickets.append({"first": first, "second": second, "third": third})
    assert len(tickets) == 21
    response = post_json(
        page, "/api/search", {"bet": {"type": "sanrentan", "tickets": tickets}, "fast": True}
    )
    assert response.status == 400, response.text()
    assert "20点" in response.text()


def test_s19_stake_total_matches_races_times_tickets(page):
    _add_ticket(page, 1, 2, 5)
    _run_search(page)
    expect(page.locator(".tickettbl")).to_be_visible(timeout=30_000)

    n = int(page.locator(".kpis .kpi").nth(2).locator(".v").inner_text())
    stake_text = page.locator(".kpis .kpi").nth(4).locator(".ci").inner_text()
    stake = int("".join(ch for ch in stake_text if ch.isdigit()))
    assert stake == n * 2 * 100


def test_s19_ticket_rows_fit_the_project_mobile_baseline(browser, kachisuji_server):
    """390px (既存 S7 と同じ基準幅) で、買い目を足しても横スクロールが出ないこと。"""
    page = browser.new_page(viewport={"width": 390, "height": 844})
    page.goto(kachisuji_server, wait_until="networkidle")
    try:
        for _ in range(5):
            page.locator("#btnAddTicket").click()
        overflow = page.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        assert overflow <= 0, f"横スクロールが発生している: {overflow}px"
    finally:
        page.close()


def test_s19_ticket_rows_do_not_overflow_mobile_viewport(browser, kachisuji_server):
    page = browser.new_page(viewport={"width": 360, "height": 780})
    page.goto(kachisuji_server, wait_until="networkidle")
    try:
        before = page.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        for _ in range(3):
            page.locator("#btnAddTicket").click()
        after = page.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        # はみ出した要素を名指しできないと、原因の切り分けに毎回時間がかかる。
        culprits = page.evaluate(
            """() => {
              const docW = document.documentElement.clientWidth;
              const out = [];
              document.querySelectorAll('*').forEach(el => {
                const r = el.getBoundingClientRect();
                if (r.right > docW + 0.5) {
                  out.push(el.tagName.toLowerCase()
                    + (el.id ? '#' + el.id : '')
                    + (el.className ? '.' + String(el.className).trim().split(/\\s+/).join('.') : '')
                    + ` right=${Math.round(r.right)}`);
                }
              });
              return out.slice(0, 6);
            }"""
        )
        # 買い目行を足したことで新たにはみ出さないこと。これがこの機能の責任範囲。
        # 絶対値で見ると、360px では section.panel が元から 14px はみ出している
        # (この機能とは無関係の既存の崩れ) を拾ってしまい、誤診の元になる。
        assert after <= before, (
            f"買い目行の追加で横スクロールが増えた: {before}px → {after}px / "
            f"はみ出し要素: {culprits}"
        )
    finally:
        page.close()
