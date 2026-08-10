from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import Page, Request, sync_playwright


def _navigation_metrics(page: Page) -> dict[str, object]:
    return page.evaluate(
        """
        () => {
          const nav = performance.getEntriesByType('navigation')[0];
          const paints = Object.fromEntries(
            performance.getEntriesByType('paint').map((entry) => [entry.name, Math.round(entry.startTime)])
          );
          const panel = document.getElementById('todays-picks-panel');
          return {
            dom_content_loaded_ms: Math.round(nav?.domContentLoadedEventEnd || 0),
            load_ms: Math.round(nav?.loadEventEnd || 0),
            ttfb_ms: Math.round((nav?.responseStart || 0) - (nav?.requestStart || 0)),
            response_ms: Math.round((nav?.responseEnd || 0) - (nav?.requestStart || 0)),
            transfer_size: nav?.transferSize || 0,
            encoded_body_size: nav?.encodedBodySize || 0,
            decoded_body_size: nav?.decodedBodySize || 0,
            first_paint_ms: paints['first-paint'] || null,
            first_contentful_paint_ms: paints['first-contentful-paint'] || null,
            race_count: document.querySelectorAll('.race-item[data-race-id]').length,
            stadium_count: document.querySelectorAll('.stadium-card').length,
            todays_picks_panel_exists: Boolean(panel),
            todays_pick_rows: document.querySelectorAll('.todays-pick-row').length,
            market_signals_requests: performance.getEntriesByType('resource')
              .filter((entry) => entry.name.includes('/api/market-signals')).length,
          };
        }
        """
    )


def _measure(page: Page, url: str, phase: str, run: int) -> dict[str, object]:
    console_messages: list[dict[str, str]] = []
    page_errors: list[str] = []
    requests: list[dict[str, object]] = []
    started: dict[Request, float] = {}

    def on_console(message) -> None:
        if message.type in {"error", "warning"}:
            console_messages.append({"type": message.type, "text": message.text})

    def on_request(request: Request) -> None:
        started[request] = time.perf_counter()

    def on_finished(request: Request) -> None:
        response = request.response()
        elapsed_ms = round((time.perf_counter() - started.pop(request, time.perf_counter())) * 1000, 1)
        headers = response.headers if response else {}
        requests.append(
            {
                "url": request.url,
                "method": request.method,
                "resource_type": request.resource_type,
                "status": response.status if response else None,
                "duration_ms": elapsed_ms,
                "server_timing": headers.get("server-timing", ""),
                "content_length": headers.get("content-length", ""),
                "cache_control": headers.get("cache-control", ""),
            }
        )

    page.on("console", on_console)
    def on_page_error(exc) -> None:
        page_errors.append(str(exc))

    page.on("pageerror", on_page_error)
    page.on("request", on_request)
    page.on("requestfinished", on_finished)
    started_at = time.perf_counter()
    response = page.goto(url, wait_until="load") if phase == "initial" else page.reload(wait_until="load")
    page.wait_for_timeout(1800)
    wall_ms = round((time.perf_counter() - started_at) * 1000, 1)
    metrics = _navigation_metrics(page)
    if "/login" in page.url or int(metrics["race_count"]) <= 0:
        raise RuntimeError(f"TOP profile did not reach race data: {page.url}")
    response_headers = response.headers if response else {}
    page.remove_listener("console", on_console)
    page.remove_listener("pageerror", on_page_error)
    page.remove_listener("request", on_request)
    page.remove_listener("requestfinished", on_finished)
    slow_requests = sorted(requests, key=lambda row: float(row["duration_ms"]), reverse=True)[:10]
    app_errors = [
        row for row in console_messages
        if "favicon.ico" not in row["text"] and "Failed to load resource" not in row["text"]
    ]
    render_result = (
        "rendered"
        if metrics["todays_picks_panel_exists"] and metrics["todays_pick_rows"]
        else "early_return_no_panel"
        if not metrics["todays_picks_panel_exists"]
        else "panel_present_no_rows"
    )
    return {
        "run": run,
        "phase": phase,
        "url": page.url,
        "http_status": response.status if response else None,
        "wall_ms_including_settle": wall_ms,
        "server_timing": response_headers.get("server-timing", ""),
        "navigation": metrics,
        "renderTodaysPicks": {
            "result": render_result,
            "reason": "TOP disables the ROI picks panel and market-signals fetch",
        },
        "console": console_messages,
        "application_console_errors": app_errors,
        "page_errors": page_errors,
        "slow_requests": slow_requests,
        "all_requests": requests,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Profile TOP initial/reload performance with Playwright")
    parser.add_argument("--base-url", default="https://boatrace-web.onrender.com")
    parser.add_argument("--date", required=True)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--out", required=True)
    parser.add_argument("--storage-state", default="")
    args = parser.parse_args()

    url = urljoin(args.base_url.rstrip("/") + "/", f"races?date={args.date}")
    report: dict[str, object] = {"url": url, "runs": []}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for run in range(1, args.runs + 1):
            context_args: dict[str, object] = {"viewport": {"width": 1600, "height": 1000}}
            if args.storage_state:
                context_args["storage_state"] = args.storage_state
            context = browser.new_context(**context_args)
            page = context.new_page()
            report["runs"].append(_measure(page, url, "initial", run))
            report["runs"].append(_measure(page, url, "reload", run))
            context.close()
        browser.close()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
