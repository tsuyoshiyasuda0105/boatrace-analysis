from __future__ import annotations

import argparse
import json
from pathlib import Path


def _clean_text(value: str) -> str:
    return " ".join((value or "").split())


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit top/member/detail/admin pages with Playwright")
    parser.add_argument("--base-url", default="http://127.0.0.1:5010")
    parser.add_argument("--date", default="2026-08-09")
    parser.add_argument("--role", default="admin", choices=["guest", "free_member", "paid_member", "admin"])
    parser.add_argument("--out-dir", default="playwright_audit")
    parser.add_argument("--scan-all-races", action="store_true")
    parser.add_argument("--skip-login", action="store_true")
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright is not installed.", flush=True)
        return 2

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    report: dict[str, object] = {
        "base_url": args.base_url,
        "date": args.date,
        "role": args.role,
        "skip_login": args.skip_login,
        "pages": {},
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1600, "height": 1400})
        page = context.new_page()
        if args.skip_login:
            page.goto(f"{args.base_url}/races?date={args.date}", wait_until="domcontentloaded")
        else:
            login_url = (
                f"{args.base_url}/test/login-as/{args.role}"
                f"?next=/member/today-races?date={args.date}"
            )
            page.goto(login_url, wait_until="domcontentloaded")
        page.wait_for_selector("body")

        def audit_member_today() -> tuple[dict[str, object], str | None]:
            url = f"{args.base_url}/member/today-races?date={args.date}"
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_selector(".today-races-heading")
            shot = out_dir / "member_today_races.png"
            page.screenshot(path=str(shot), full_page=True)
            detail_link = None
            first_detail = page.locator(".pick-link a").first
            if first_detail.count() > 0:
                detail_link = first_detail.get_attribute("href")
            meta_text = _clean_text(page.locator(".today-races-heading .meta").inner_text())
            return ({
                "url": page.url,
                "title": page.title(),
                "meta": meta_text,
                "system_warning_visible": page.get_by_text("システム警告").count() > 0,
                "pick_row_count": page.locator(".todays-pick-row").count(),
                "first_detail_link": detail_link,
                "screenshot": str(shot),
            }, detail_link)

        def audit_top_page() -> tuple[dict[str, object], str | None, list[str]]:
            url = f"{args.base_url}/races?date={args.date}"
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_selector(".stadium-grid")
            page.wait_for_timeout(500)
            shot = out_dir / "top_races.png"
            page.screenshot(path=str(shot), full_page=True)
            count_note = ""
            count_note_loc = page.locator("#todays-picks-count-note")
            if count_note_loc.count() > 0:
                try:
                    count_note = _clean_text(count_note_loc.inner_text())
                except Exception:
                    count_note = ""
            first_race_link = None
            race_links: list[str] = []
            race_link_locs = page.locator('.race-item a[href^="/race/"]')
            race_count = race_link_locs.count()
            for index in range(race_count):
                href = race_link_locs.nth(index).get_attribute("href")
                if href:
                    race_links.append(href)
            first_race = page.locator('.race-item a[href^="/race/"]').first
            if first_race.count() > 0:
                first_race_link = first_race.get_attribute("href")
            env_count = page.locator(".stadium-env").count()
            first_env = ""
            if env_count > 0:
                first_env = _clean_text(page.locator(".stadium-env").first.inner_text())
            return ({
                "url": page.url,
                "title": page.title(),
                "count_note": count_note,
                "system_warning_visible": page.get_by_text("システム警告").count() > 0,
                "stadium_env_count": env_count,
                "first_stadium_env": first_env,
                "first_race_link": first_race_link,
                "race_link_count": len(race_links),
                "screenshot": str(shot),
            }, first_race_link, race_links)

        def audit_race_detail(detail_link: str | None) -> dict[str, object]:
            if not detail_link:
                return {"skipped": True, "reason": "no detail link found"}
            page.goto(f"{args.base_url}{detail_link}" if detail_link.startswith("/") else detail_link, wait_until="domcontentloaded")
            page.wait_for_selector(".race-header")
            page.wait_for_timeout(250)
            shot = out_dir / "race_detail.png"
            page.screenshot(path=str(shot), full_page=True)
            env_card_count = page.locator(".race-env-card").count()
            env_text = ""
            if env_card_count > 0:
                env_text = _clean_text(page.locator(".race-env-card").first.inner_text())
            entry_alert_count = page.locator(".racer-entry-change-alert").count()
            return {
                "url": page.url,
                "title": page.title(),
                "system_warning_visible": page.get_by_text("システム警告").count() > 0,
                "water_info_visible": env_card_count > 0,
                "water_info_text": env_text,
                "entry_change_alert_count": entry_alert_count,
                "screenshot": str(shot),
            }

        def audit_admin_data_status() -> dict[str, object]:
            url = f"{args.base_url}/admin/data-status?date={args.date}"
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_selector(".admin-data-cards")
            shot = out_dir / "admin_data_status.png"
            page.screenshot(path=str(shot), full_page=True)
            cards = page.locator(".admin-data-cards .health-strategy-card").count()
            hint_count = page.locator(".health-recommendation").count()
            return {
                "url": page.url,
                "title": page.title(),
                "admin_header_visible": page.get_by_text("ADMIN").count() > 0,
                "system_warning_visible": page.get_by_text("システム警告").count() > 0,
                "card_count": cards,
                "recommendation_count": hint_count,
                "screenshot": str(shot),
            }

        def scan_all_race_details(race_links: list[str]) -> dict[str, object]:
            summary = {
                "checked_races": 0,
                "system_warning_visible_count": 0,
                "water_info_visible_count": 0,
                "water_info_missing_values_count": 0,
                "entry_change_alert_races": 0,
                "sample_missing_value_races": [],
                "sample_entry_change_races": [],
            }
            sample_missing: list[dict[str, object]] = []
            sample_entry: list[dict[str, object]] = []
            for href in race_links:
                page.goto(f"{args.base_url}{href}" if href.startswith("/") else href, wait_until="domcontentloaded")
                page.wait_for_selector(".race-header")
                summary["checked_races"] += 1
                if page.get_by_text("システム警告").count() > 0:
                    summary["system_warning_visible_count"] += 1
                env_loc = page.locator(".race-env-card")
                if env_loc.count() > 0:
                    summary["water_info_visible_count"] += 1
                    env_text = _clean_text(env_loc.first.inner_text())
                    if any(token in env_text for token in ("天候-", "風-", "波-", "取得日時 -", "潮データなし")):
                        summary["water_info_missing_values_count"] += 1
                        if len(sample_missing) < 10:
                            sample_missing.append({"race": href, "water_info_text": env_text})
                entry_count = page.locator(".racer-entry-change-alert").count()
                if entry_count > 0:
                    summary["entry_change_alert_races"] += 1
                    if len(sample_entry) < 10:
                        sample_entry.append({"race": href, "entry_change_alert_count": entry_count})
            summary["sample_missing_value_races"] = sample_missing
            summary["sample_entry_change_races"] = sample_entry
            return summary

        top_result, top_detail_link, race_links = audit_top_page()
        report["pages"]["top_races"] = top_result
        detail_link = top_detail_link
        if args.skip_login:
            report["pages"]["member_today_races"] = {"skipped": True, "reason": "skip-login"}
            report["pages"]["admin_data_status"] = {"skipped": True, "reason": "skip-login"}
        else:
            member_result, member_detail_link = audit_member_today()
            report["pages"]["member_today_races"] = member_result
            detail_link = member_detail_link or top_detail_link
            report["pages"]["admin_data_status"] = audit_admin_data_status()
        report["pages"]["race_detail"] = audit_race_detail(detail_link)
        if args.scan_all_races:
            report["pages"]["all_race_details_scan"] = scan_all_race_details(race_links)

        browser.close()

    report_path = out_dir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"report_path={report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
