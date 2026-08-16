from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import sqlite3
import time
from urllib.parse import urljoin

import pytest
from playwright.sync_api import expect


SEARCH_DB = Path(__file__).resolve().parents[2] / "data" / "kachisuji_search.db"


def _search_db_rows(sql: str, parameters=()):
    connection = sqlite3.connect(SEARCH_DB.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only = ON")
        return connection.execute(sql, parameters).fetchall()
    finally:
        connection.close()


def _date_after_latest_race() -> str:
    rows = _search_db_rows("SELECT MAX(race_date) AS race_date FROM asof_race_features")
    if not rows or rows[0]["race_date"] is None:
        pytest.skip("kachisuji search DB has no race dates")
    return (date.fromisoformat(rows[0]["race_date"]) + timedelta(days=1)).isoformat()


def _confirmed_match_case():
    rows = _search_db_rows(
        """
        SELECT race_date, jcd, COUNT(*) AS races
        FROM asof_race_features
        GROUP BY race_date, jcd
        HAVING COUNT(*) > 0
           AND SUM(
               result_sanrentan IS NULL
               OR payout_sanrentan IS NULL
               OR (schema_version >= 4 AND result_sanrentan_json IS NULL)
               OR (schema_version >= 4 AND payout_sanrentan_json IS NULL)
           ) = 0
        ORDER BY race_date DESC, jcd
        LIMIT 1
        """
    )
    if not rows:
        pytest.skip("kachisuji search DB has no venue/date with fully confirmed results")
    return rows[0]


def _pending_weather_case():
    rows = _search_db_rows(
        """
        SELECT race_date, jcd, b1_age, COUNT(*) AS pending_races
        FROM asof_race_features
        WHERE b1_age IS NOT NULL
          AND weather IS NULL
        GROUP BY race_date, jcd, b1_age
        HAVING COUNT(*) > 0
        ORDER BY race_date DESC, jcd, b1_age
        LIMIT 1
        """
    )
    if not rows:
        pytest.skip(
            "kachisuji search DB has no race with resolved prior-day b1_age "
            "and missing same-day weather"
        )
    return rows[0]


def valid_conditions(**extra):
    conditions = {"bet": {"type": "sanrentan", "first": 1, "second": 2, "third": 3}, "fast": True}
    conditions.update(extra)
    return conditions


def post_json(page, path: str, payload):
    return page.request.post(urljoin(page.url, path), data=payload, headers={"Content-Type": "application/json"})


def get_url(page, path: str, **kwargs):
    return page.request.get(urljoin(page.url, path), **kwargs)


def mock_search_result(page):
    payloads = []
    result = {
        "roi": 100.0,
        "roi_ci_low": 90.0,
        "roi_ci_high": 110.0,
        "hit_rate": 20.0,
        "hits": 2,
        "n": 10,
        "effective_date_range": ["2026-01-01", "2026-08-16"],
        "excluded": {"result_missing": 0, "condition_null": 0},
        "warnings": [],
        "yearly": [],
        "monthly": [],
    }

    def handle(route, request):
        payloads.append(request.post_data_json)
        route.fulfill(status=200, json=result)

    page.route("**/api/search", handle)
    return payloads


# S1: basic rendering, search and bet-type UI.
def test_s1_top_sections_and_default_state(page):
    expect(page).to_have_title("勝ち筋サーチ")
    expect(page.locator("#conditions-title")).to_be_visible()
    expect(page.locator("#results-title")).to_be_visible()
    expect(page.locator("#myStrategies")).to_contain_text("保存した手法はまだありません")
    expect(page.locator("#pos2wrap")).to_be_visible()
    expect(page.locator("#pos3wrap")).to_be_visible()


def test_s12_odds_filter_controls_and_labels_are_absent(page):
    for selector in (
        "#oddsEnabled",
        "#oddsMin",
        "#oddsMax",
        "#favoriteOddsEnabled",
        "#favoriteOddsMin",
        "#favoriteOddsMax",
        ".odds-fieldset",
        ".favorite-fieldset",
    ):
        expect(page.locator(selector)).to_have_count(0)
    expect(page.get_by_text("3連単オッズ", exact=False)).to_have_count(0)
    expect(page.get_by_text("人気帯", exact=False)).to_have_count(0)


def test_s11_multi_venue_and_wind_controls(page):
    venue = page.locator("#venue")
    venue.select_option(["12", "15", "24"])
    expect(page.locator("#venueCount")).to_have_text("3会場")
    expect(page.locator("#venueAll")).not_to_be_checked()
    page.locator("#venueAll").check()
    expect(page.locator("#venueCount")).to_have_text("全会場")
    expect(page.locator("#venue option:checked")).to_have_count(0)

    expect(page.locator("#windDirection")).to_be_enabled()
    expect(page.locator("#windDirection option[value='追い風']")).to_have_count(1)


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


def test_s16_result_summary_shows_exact_configured_conditions(page):
    payloads = mock_search_result(page)
    page.locator("#venue").select_option("3")
    page.locator("#betType").select_option("nirentan")
    page.locator("#pos2").select_option("3")
    page.locator("#boat2 summary").click()
    page.locator("#b2AccidentPeriodCmp").select_option("min")
    page.locator("#b2AccidentPeriod").fill("0.5")
    page.locator("#b2AvgStCmp").select_option("min")
    page.locator("#b2AvgSt").fill("0.15")
    page.locator("#boat3 summary").click()
    page.locator("#b3AvgStCmp").select_option("max")
    page.locator("#b3AvgSt").fill("0.16")
    page.locator("#btnAddCompare").click()
    comparison = page.locator(".compare-row")
    comparison.locator(".compare-boat").select_option("2")
    comparison.locator(".compare-metric").select_option("avg_st")
    comparison.locator(".compare-other").select_option("3")
    comparison.locator(".compare-op").select_option("ge")
    comparison.locator(".compare-margin").fill("0.02")
    page.locator("#dateFrom").fill("2016-06-01")
    page.locator("#dateTo").fill("2026-08-16")

    page.locator("#btnSearch").click()

    summary = page.locator(".condition-summary")
    expect(summary).to_be_visible()
    expect(summary.locator("h2")).to_have_text("検索条件（8項目）")
    expect(summary).to_contain_text("[レース] 会場: 江戸川 ／ 買い目: 2連単 1-3")
    expect(summary).to_contain_text("2号艇: 平均ST0.15秒以上・事故率（審査期・検証用）0.5%以上")
    expect(summary).to_contain_text("3号艇: 平均ST0.16秒以下")
    expect(summary).to_contain_text("[比較] 2号艇の平均ST ≥ 3号艇 +0.02秒")
    expect(summary).to_contain_text("[期間] 2016-06-01 〜 2026-08-16")
    expect(summary).not_to_contain_text("天候:")
    assert payloads == [{
        "venue": [3],
        "bet": {"type": "nirentan", "first": 1, "second": 3},
        "date_from": "2016-06-01",
        "date_to": "2026-08-16",
        "boats": {
            "2": {"avg_st": {"min": 0.15}, "accident_rate_period": {"min": 0.5}},
            "3": {"avg_st": {"max": 0.16}},
        },
        "compare": [{"metric": "avg_st", "boat": 2, "op": "ge", "other": 3, "margin": 0.02}],
    }]


def test_s16_result_summary_shows_no_conditions_for_default_search(page):
    payloads = mock_search_result(page)
    page.locator("#btnSearch").click()

    expect(page.locator(".condition-summary h2")).to_have_text("検索条件: 指定なし（全レース）")
    assert payloads == [{}]


@pytest.mark.parametrize(
    ("kind", "positions", "expected"),
    [
        ("tansho", (4,), "買い目: 単勝 4"),
        ("nirentan", (2, 5), "買い目: 2連単 2-5"),
        ("sanrentan", (3, 1, 6), "買い目: 3連単 3-1-6"),
    ],
)
def test_s16_result_summary_formats_all_bet_types(page, kind, positions, expected):
    mock_search_result(page)
    page.locator("#betType").select_option(kind)
    for selector, value in zip(("#pos1", "#pos2", "#pos3"), positions):
        page.locator(selector).select_option(str(value))
    page.locator("#btnSearch").click()
    expect(page.locator(".condition-summary")).to_contain_text(expected)


@pytest.mark.parametrize(
    ("operation", "symbol", "sign"),
    [("ge", "≥", "+"), ("le", "≤", "−")],
)
def test_s16_result_summary_formats_comparison_direction_and_margin(page, operation, symbol, sign):
    mock_search_result(page)
    page.locator("#btnAddCompare").click()
    comparison = page.locator(".compare-row")
    comparison.locator(".compare-boat").select_option("2")
    comparison.locator(".compare-metric").select_option("motor_rate2")
    comparison.locator(".compare-other").select_option("5")
    comparison.locator(".compare-op").select_option(operation)
    comparison.locator(".compare-margin").fill("3.5")
    page.locator("#btnSearch").click()
    expect(page.locator(".condition-summary")).to_contain_text(
        f"2号艇のモーター2連対率 {symbol} 5号艇 {sign}3.5%"
    )


def test_s16_result_summary_does_not_overflow_mobile_viewport(page):
    mock_search_result(page)
    page.set_viewport_size({"width": 390, "height": 800})
    page.locator("#venue").select_option(["1", "2", "3", "4", "5", "6"])
    page.locator("#weatherChips .wchip").nth(0).click()
    page.locator("#weatherChips .wchip").nth(1).click()
    page.locator("#windDirection").select_option(["追い風", "向かい風", "横風(右)"])
    page.locator("#btnSearch").click()
    expect(page.locator(".condition-summary")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")


def test_s9_year_row_toggles_monthly_breakdown_accessibly(page):
    page.locator("#fast").check()
    page.locator("#venue").select_option("1")
    page.locator("#btnSearch").click()
    toggle = page.locator(".month-toggle").first
    expect(toggle).to_be_visible(timeout=30_000)
    controlled_id = toggle.get_attribute("aria-controls")
    assert controlled_id
    rows = page.locator(f"#{controlled_id}")
    expect(toggle).to_have_attribute("aria-expanded", "false")
    expect(rows).to_be_hidden()

    toggle.click()
    expect(toggle).to_have_attribute("aria-expanded", "true")
    expect(rows).to_be_visible()
    expect(rows.locator(".month-row").first).to_be_visible()

    toggle.click()
    expect(toggle).to_have_attribute("aria-expanded", "false")
    expect(rows).to_be_hidden()


@pytest.mark.parametrize("width", [1280, 390])
def test_s7_sticky_action_bar_keeps_search_visible_at_page_bottom(page, width):
    page.set_viewport_size({"width": width, "height": 800})
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")

    expect(page.locator("#btnSearch")).to_be_in_viewport()
    expect(page.locator("#btnSearch")).to_be_enabled()


def test_s7_mobile_search_scrolls_results_into_view_and_mini_kpi_matches(page):
    page.set_viewport_size({"width": 390, "height": 800})
    page.locator("#fast").check()
    page.locator("#venue").select_option("1")
    page.locator("#btnSearch").click()
    expect(page.locator(".kpis")).to_be_visible(timeout=30_000)
    expect(page.locator("#resultsPanel")).to_be_in_viewport()

    roi = page.locator(".kpi").nth(0).locator(".v").inner_text()
    n = page.locator(".kpi").nth(2).locator(".v").inner_text()
    expect(page.locator("#miniKpi")).to_have_text(f"回収率 {roi} / N {n}")


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
    empty_date = _date_after_latest_race()
    response = post_json(page, "/api/search", valid_conditions(date_from=empty_date, date_to=empty_date))
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


def test_s9_strategy_card_loads_three_scores_verdict_and_sparkline(page):
    response = post_json(
        page,
        "/api/strategies",
        {"name": "step9-forward", "conditions": valid_conditions(venue=1), "backtest": {"roi": 101.2, "n": 12}},
    )
    assert response.status == 200, response.text()
    strategy_id = response.json()["id"]
    page.reload(wait_until="networkidle")
    card = page.locator(f'.strategy-performance[data-strategy-id="{strategy_id}"]')
    expect(card).to_contain_text("step9-forward")
    card.locator(".load-performance").click()

    card = page.locator(f'.strategy-performance[data-strategy-id="{strategy_id}"]')
    expect(card.locator(".performance-grid")).to_contain_text("探索時", timeout=30_000)
    expect(card.locator(".performance-grid")).to_contain_text("全期間")
    expect(card.locator(".performance-forward")).to_contain_text("フォワード")
    expect(card.locator(".verdict")).to_be_visible()
    expect(card.locator("svg.forward-sparkline")).to_be_visible()
    expect(card).to_contain_text("保存日の翌日以降")

    deleted = page.request.delete(urljoin(page.url, f"/api/strategies/{strategy_id}"))
    assert deleted.status == 200


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
    page.locator("#matchDate").fill(_date_after_latest_race())
    page.locator("#btnMatch").click()
    expect(page.locator("#matchResults .match-group").first).to_be_visible(timeout=30_000)
    expect(page.locator("#matchResults")).to_contain_text("確定 0 / 未確定 0")


def test_s5_confirmed_match_rows_are_returned(page):
    case = _confirmed_match_case()
    response = post_json(page, "/api/strategies", {"name": "confirmed-baseline", "conditions": valid_conditions(venue=case["jcd"]), "backtest": {"roi": 100, "n": 1}})
    assert response.status == 200, response.text()
    strategy_id = response.json()["id"]
    matched = get_url(page, f"/api/strategies/{strategy_id}/matches", params={"date": case["race_date"]})
    assert matched.status == 200, matched.text()
    body = matched.json()
    assert body["counts"]["races_on_date"] > 0
    assert body["counts"]["matched"] > 0
    assert body["counts"]["pending"] == 0
    assert all(item["status"] == "confirmed" for item in body["matched"])


def test_s5_missing_same_day_values_are_pending(page):
    case = _pending_weather_case()
    conditions = valid_conditions(
        venue=case["jcd"],
        weather=["晴"],
        boats={"1": {"age": {"min": case["b1_age"], "max": case["b1_age"]}}},
    )
    response = post_json(page, "/api/strategies", {"name": "pending-weather", "conditions": conditions, "backtest": {"roi": 100, "n": 1}})
    assert response.status == 200, response.text()
    strategy_id = response.json()["id"]
    matched = get_url(page, f"/api/strategies/{strategy_id}/matches", params={"date": case["race_date"]})
    assert matched.status == 200, matched.text()
    body = matched.json()
    assert body["counts"]["races_on_date"] > 0
    assert body["counts"]["pending"] > 0
    assert all(item["status"] == "pending" for item in body["pending"])
    assert all(item["undetermined_columns"] == ["weather"] for item in body["pending"])


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
