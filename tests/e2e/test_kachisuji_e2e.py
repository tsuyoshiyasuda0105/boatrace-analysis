from __future__ import annotations

import time
from urllib.parse import urljoin

import pytest
from playwright.sync_api import expect


def valid_conditions(**extra):
    conditions = {"bet": {"type": "sanrentan", "first": 1, "second": 2, "third": 3}, "fast": True}
    conditions.update(extra)
    return conditions


def post_json(page, path: str, payload):
    return page.request.post(urljoin(page.url, path), data=payload, headers={"Content-Type": "application/json"})


def get_url(page, path: str, **kwargs):
    return page.request.get(urljoin(page.url, path), **kwargs)


# S1: basic rendering, search and bet-type UI.
def test_s1_top_sections_and_default_state(page):
    expect(page).to_have_title("勝ち筋サーチ")
    expect(page.locator("#conditions-title")).to_be_visible()
    expect(page.locator("#results-title")).to_be_visible()
    expect(page.locator("#myStrategies")).to_contain_text("保存した手法はまだありません")
    expect(page.locator("#pos2wrap")).to_be_visible()
    expect(page.locator("#pos3wrap")).to_be_visible()


@pytest.mark.parametrize(
    ("kind", "second_visible", "third_visible"),
    [("tansho", False, False), ("nirentan", True, False), ("sanrentan", True, True)],
)
def test_s1_bet_type_changes_position_controls(page, kind, second_visible, third_visible):
    page.locator("#betType").select_option(kind)
    expect(page.locator("#pos2wrap")).to_be_visible() if second_visible else expect(page.locator("#pos2wrap")).to_be_hidden()
    expect(page.locator("#pos3wrap")).to_be_visible() if third_visible else expect(page.locator("#pos3wrap")).to_be_hidden()


def test_s1_search_renders_result_kpis(page):
    page.locator("#fast").check()
    page.locator("#venue").select_option("1")
    page.locator("#btnSearch").click()
    expect(page.locator(".kpis")).to_be_visible(timeout=30_000)
    expect(page.locator(".kpi")).to_have_count(4)
    expect(page.locator("#btnSaveStrategy")).to_be_enabled()


# S2: ticket validation and unused legs.
def test_s2_duplicate_ticket_is_rejected_with_visible_guidance(page):
    page.locator("#pos2").select_option("1")
    page.locator("#fast").check()
    page.locator("#btnSearch").click()
    alert = page.locator("#resultArea [role=alert]")
    expect(alert).to_be_visible()
    expect(alert).to_contain_text("異なる艇番")


@pytest.mark.parametrize(
    ("bet", "forbidden"),
    [({"type": "tansho", "first": 1}, ("second", "third")), ({"type": "nirentan", "first": 1, "second": 2}, ("third",))],
)
def test_s2_api_accepts_only_needed_ticket_legs(page, bet, forbidden):
    response = post_json(page, "/api/search", {"bet": bet, "fast": True, "venue": 1})
    assert response.status == 200, response.text()
    assert all(key not in bet for key in forbidden)


# S3: boat controls and comparisons.
def test_s3_boat_details_and_condition_badge(page):
    details = page.locator("#boat2")
    expect(details).not_to_have_attribute("open", "")
    details.locator("summary").click()
    expect(details).to_have_attribute("open", "")
    page.locator("#b2Classes .wchip", has_text="A1").click()
    page.locator("#b2Classes .wchip", has_text="B1").click()
    expect(page.locator("#boatSummary2")).to_have_text("条件 1 個")
    page.locator("#b2AgeCmp").select_option("min")
    expect(page.locator("#boatSummary2")).to_have_text("条件 2 個")
    page.locator("#b2Classes .wchip", has_text="A1").click()
    page.locator("#b2Classes .wchip", has_text="B1").click()
    expect(page.locator("#boatSummary2")).to_have_text("条件 1 個")


def test_s3_all_class_chips_are_selectable_and_removable(page):
    chips = page.locator("#b1Classes .wchip")
    for index in range(chips.count()):
        chips.nth(index).click()
    expect(page.locator("#boatSummary1")).to_have_text("条件 1 個")
    assert all(chips.nth(i).get_attribute("aria-pressed") == "true" for i in range(chips.count()))
    for index in range(chips.count()):
        chips.nth(index).click()
    expect(page.locator("#boatSummary1")).to_have_text("条件なし")


def test_s3_racer_name_only_shows_400_guidance(page):
    page.locator("#b1Racer").fill("峰竜太")
    page.locator("#fast").check()
    page.locator("#btnSearch").click()
    expect(page.locator("#resultArea [role=alert]")).to_contain_text("選手名には未対応です")


@pytest.mark.parametrize(
    ("metric", "unit"),
    [("motor_rate2", "pt"), ("avg_st", "秒"), ("age", "歳")],
)
def test_s3_comparison_units_change_with_metric(page, metric, unit):
    page.locator("#btnAddCompare").click()
    row = page.locator(".compare-row")
    row.locator(".compare-metric").select_option(metric)
    expect(row.locator(".compare-unit")).to_have_text(unit)
    row.locator(".remove-compare").click()
    expect(page.locator(".compare-row")).to_have_count(0)


def test_s3_same_boat_comparison_has_clear_visible_error(page):
    page.locator("#btnAddCompare").click()
    row = page.locator(".compare-row")
    row.locator(".compare-metric").select_option("age")
    row.locator(".compare-other").select_option("1")
    page.locator("#fast").check()
    page.locator("#btnSearch").click()
    alert = page.locator("#resultArea [role=alert]")
    expect(alert).to_be_visible()
    expect(alert).to_contain_text("同じ艇同士は比較できません")


# S4: numeric and date boundaries.
@pytest.mark.parametrize(
    "payload",
    [
        valid_conditions(venue=-1),
        valid_conditions(venue=10**100),
        valid_conditions(boats={"1": {"age": {"min": "abc"}}}),
        valid_conditions(boats={"1": {"age": {"min": -1, "max": -2}}}),
        valid_conditions(compare=[{"metric": "age", "boat": 1, "other": 2, "op": "ge", "margin": -0.01}]),
    ],
)
def test_s4_invalid_numeric_api_inputs_are_400(page, payload):
    response = post_json(page, "/api/search", payload)
    assert response.status == 400, response.text()


@pytest.mark.parametrize(
    "dates",
    [
        {"date_from": "2025-02-02", "date_to": "2025-02-01"},
        {"date_from": "not-a-date"},
        {"date_to": "2025-02-30"},
    ],
)
def test_s4_invalid_date_ranges_are_400(page, dates):
    response = post_json(page, "/api/search", valid_conditions(**dates))
    assert response.status == 400, response.text()


def test_s4_future_range_is_valid_but_empty(page):
    response = post_json(page, "/api/search", valid_conditions(date_from="2099-01-01", date_to="2099-12-31"))
    assert response.status == 200, response.text()
    assert response.json()["n"] == 0


def test_s4_unfiltered_search_completes_without_server_error(page):
    started = time.monotonic()
    response = post_json(page, "/api/search", valid_conditions())
    elapsed = time.monotonic() - started
    assert response.status == 200, response.text()
    assert response.json()["n"] >= 0
    assert elapsed < 30


# S5: isolated strategy CRUD, matching, volume and XSS.
def test_s5_empty_strategy_name_is_rejected(page):
    response = post_json(page, "/api/strategies", {"name": "   ", "conditions": valid_conditions(), "backtest": {"roi": 1, "n": 1}})
    assert response.status == 400


def test_s5_strategy_save_list_delete_roundtrip(page):
    response = post_json(page, "/api/strategies", {"name": "roundtrip", "conditions": valid_conditions(), "backtest": {"roi": 101.2, "n": 12}})
    assert response.status == 200, response.text()
    strategy_id = response.json()["id"]
    page.reload(wait_until="networkidle")
    expect(page.locator("#myStrategies")).to_contain_text("roundtrip")
    deleted = page.request.delete(urljoin(page.url, f"/api/strategies/{strategy_id}"))
    assert deleted.status == 200
    assert all(item["id"] != strategy_id for item in get_url(page, "/api/strategies").json())


def test_s5_strategy_name_is_escaped_and_does_not_execute(page):
    payload = '<script>window.__round1Xss=1</script><img src=x onerror="window.__round1Xss=2">'
    response = post_json(page, "/api/strategies", {"name": payload, "conditions": valid_conditions(), "backtest": {"roi": 100, "n": 1}})
    assert response.status == 200, response.text()
    page.reload(wait_until="networkidle")
    assert page.evaluate("window.__round1Xss") is None
    expect(page.locator("#myStrategies")).to_contain_text(payload)
    expect(page.locator("#myStrategies script")).to_have_count(0)
    expect(page.locator("#myStrategies img")).to_have_count(0)


def test_s5_twenty_strategies_list_and_match(page):
    for index in range(20):
        response = post_json(page, "/api/strategies", {"name": f"bulk-{index:02d}", "conditions": valid_conditions(venue=(index % 24) + 1), "backtest": {"roi": index, "n": index}})
        assert response.status == 200, response.text()
    page.reload(wait_until="networkidle")
    assert page.locator("#myStrategies .mycard").count() >= 20
    page.locator("#matchDate").fill("2099-01-01")
    page.locator("#btnMatch").click()
    expect(page.locator("#matchResults .match-group").first).to_be_visible(timeout=30_000)
    expect(page.locator("#matchResults")).to_contain_text("確定 0 / 未確定 0")


def test_s5_confirmed_match_rows_are_returned(page):
    response = post_json(page, "/api/strategies", {"name": "confirmed-baseline", "conditions": valid_conditions(venue=1), "backtest": {"roi": 100, "n": 1}})
    assert response.status == 200, response.text()
    strategy_id = response.json()["id"]
    matched = get_url(page, f"/api/strategies/{strategy_id}/matches", params={"date": "2026-08-14"})
    assert matched.status == 200, matched.text()
    body = matched.json()
    assert body["counts"]["matched"] > 0
    assert body["counts"]["pending"] == 0
    assert all(item["status"] == "confirmed" for item in body["matched"])


def test_s5_missing_same_day_values_are_pending(page):
    response = post_json(page, "/api/strategies", {"name": "pending-weather", "conditions": valid_conditions(weather=["晴"]), "backtest": {"roi": 100, "n": 1}})
    assert response.status == 200, response.text()
    strategy_id = response.json()["id"]
    matched = get_url(page, f"/api/strategies/{strategy_id}/matches", params={"date": "2026-08-15"})
    assert matched.status == 200, matched.text()
    body = matched.json()
    assert body["counts"]["races_on_date"] > 0
    assert body["counts"]["pending"] > 0
    assert all(item["status"] == "pending" for item in body["pending"])


# S6: direct API hardening.
@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"unknown": True}, 400),
        (valid_conditions(venue="abc"), 400),
        (valid_conditions(fast="yes"), 400),
        ({"bet": {"type": "tansho", "first": 1}, "padding": "x" * 1_000_000}, 400),
        (["not", "an", "object"], 400),
    ],
)
def test_s6_search_malformed_payload_status(page, payload, expected):
    response = post_json(page, "/api/search", payload)
    assert response.status == expected, response.text()


@pytest.mark.parametrize(
    "payload",
    [None, [], {}, {"name": "x"}, {"name": "x", "conditions": []}],
)
def test_s6_strategy_malformed_payload_is_400(page, payload):
    response = post_json(page, "/api/strategies", payload)
    assert response.status == 400, response.text()


def test_s6_strategy_rejects_non_object_backtest(page):
    response = post_json(page, "/api/strategies", {"name": "broken-backtest", "conditions": valid_conditions(), "backtest": "not-an-object"})
    assert response.status == 400, response.text()


@pytest.mark.parametrize("date", ["bad", "2025-02-30", "", "<script>"])
def test_s6_invalid_match_date_is_400(page, date):
    response = get_url(page, "/api/matches", params={"date": date})
    assert response.status == 400, response.text()


def test_s6_healthz(page):
    response = get_url(page, "/healthz")
    assert response.status == 200
    assert response.json() == {"status": "ok"}


# S7: responsive layout and multi-action safety.
def test_s7_mobile_has_no_document_horizontal_overflow(browser, kachisuji_server):
    mobile = browser.new_page(viewport={"width": 390, "height": 844})
    errors = []
    mobile.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
    mobile.goto(kachisuji_server, wait_until="networkidle")
    dimensions = mobile.evaluate("({scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth})")
    mobile.close()
    assert dimensions["scroll"] <= dimensions["client"]
    assert not errors


def test_s7_rapid_double_search_sends_one_request(page):
    requests = []
    page.on("request", lambda request: requests.append(request) if request.url.endswith("/api/search") else None)
    page.locator("#fast").check()
    page.locator("#btnSearch").evaluate("button => { button.click(); button.click(); }")
    expect(page.locator(".kpis")).to_be_visible(timeout=30_000)
    assert len(requests) == 1
