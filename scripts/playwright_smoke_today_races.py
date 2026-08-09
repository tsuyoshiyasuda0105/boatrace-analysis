from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Playwright smoke test for member today-races page")
    parser.add_argument("--base-url", default="http://127.0.0.1:5010")
    parser.add_argument("--date", default="2026-08-09")
    parser.add_argument("--role", default="admin", choices=["guest", "free_member", "paid_member", "admin"])
    parser.add_argument("--screenshot", default="playwright_today_races.png")
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "Playwright is not installed. Run `pip install playwright` and `playwright install chromium` before using this script.",
            file=sys.stderr,
        )
        return 2

    login_url = (
        f"{args.base_url}/test/login-as/{args.role}"
        f"?next=/member/today-races?date={args.date}"
    )
    page_url = f"{args.base_url}/member/today-races?date={args.date}"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1200})
        page.goto(login_url)
        page.wait_for_load_state("networkidle")
        page.goto(page_url)
        page.wait_for_load_state("networkidle")
        page.screenshot(path=args.screenshot, full_page=True)

        title = page.title()
        count_note = ""
        try:
            count_note = page.locator("#todays-picks-count-note").inner_text(timeout=3000)
        except Exception:
            count_note = ""

        print(f"title={title}")
        if count_note:
            print(f"count_note={count_note}")
        print(f"screenshot={args.screenshot}")
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
