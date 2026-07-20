from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
import argparse
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

os.environ.setdefault("BOATRACE_TASK_TRIGGER", "render-prewarm")

from src.web.app import create_app  # noqa: E402
from scripts.ensure_performance_indexes import ensure_performance_indexes  # noqa: E402


MODES = ("signals", "realtime", "morning-check", "nightly")
JST = ZoneInfo("Asia/Tokyo")


def _validate_market_signal_response(resp, path: str) -> tuple[bool, str]:
    """Reject cache placeholders that happen to return HTTP 200."""
    payload = resp.get_json(silent=True)
    if not isinstance(payload, dict):
        return False, "response is not a JSON object"

    target_date = (parse_qs(urlparse(path).query).get("date") or [None])[0]
    if target_date and payload.get("date") != target_date:
        return False, f"date mismatch: expected={target_date} actual={payload.get('date')}"
    if resp.headers.get("X-Boatrace-Cache") != "recomputed":
        return False, f"cache_state={resp.headers.get('X-Boatrace-Cache') or 'missing'}"
    if not payload.get("computed_at"):
        return False, "computed_at is missing"
    if not isinstance(payload.get("signals"), dict):
        return False, "signals is not an object"
    if int(payload.get("n_races") or 0) != len(payload["signals"]):
        return False, "n_races does not match signals"

    data_status = payload.get("data_status") or {}
    if data_status.get("cache_only") or data_status.get("cache_miss"):
        return False, "cache placeholder returned"
    race_basic = data_status.get("race_basic") or {}
    total = int(race_basic.get("total") or 0)
    count = int(race_basic.get("count") or 0)
    if total > 0 and count == 0:
        return False, f"race source incomplete: predictions=0/{total}"

    return True, (
        f"computed_at={payload['computed_at']} signals={len(payload['signals'])} "
        f"source={count}/{total}"
    )


def _hit(client, path: str) -> tuple[int, int, bool, str]:
    resp = client.get(path)
    body = resp.get_data() or b""
    if resp.status_code != 200:
        return resp.status_code, len(body), False, f"http={resp.status_code}"
    if path.startswith("/api/market-signals") and "recompute=1" in path:
        valid, detail = _validate_market_signal_response(resp, path)
        return resp.status_code, len(body), valid, detail
    return resp.status_code, len(body), True, "ok"


def _days_ago(today: date, days: int) -> str:
    return (today - timedelta(days=days)).isoformat()


def build_targets(mode: str, today: date) -> list[str]:
    today_s = today.isoformat()
    yesterday_s = _days_ago(today, 1)
    d30 = _days_ago(today, 30)
    d365 = _days_ago(today, 365)
    d3y = _days_ago(today, 1095)

    if mode == "signals":
        # The five-minute Render scheduler is the only daytime writer for the
        # expensive signal snapshot. Browser requests only read this snapshot.
        return [f"/api/market-signals?date={today_s}&recompute=1"]

    if mode == "nightly":
        # Heavy historical refresh. This is intentionally reserved for the
        # end-of-day Render scheduler so normal app clicks never trigger it.
        # Rebuild yesterday first: after results/payouts arrive, the ROI cache
        # must overlay the same high-ROI signal payload that users saw.
        return [
            f"/api/market-signals?date={yesterday_s}&recompute=1",
            f"/api/market-signals?date={today_s}&recompute=1",
            f"/member/strategy?from={d3y}&to={today_s}&recompute=1",
            f"/member/strategy?from={d365}&to={today_s}&recompute=1",
            f"/member/strategy?from={d30}&to={today_s}&recompute=1",
            f"/member/strategy/monthly?recompute=1",
            f"/member/strategy?from={d3y}&to={today_s}",
            f"/member/strategy?from={d365}&to={today_s}",
            f"/member/strategy?from={d30}&to={today_s}",
            "/member/strategy/monthly",
        ]

    if mode == "morning-check":
        # Reconcile visible pages after the morning race/prediction load.
        return [
            f"/api/market-signals?date={today_s}&recompute=1",
            f"/member/strategy?from={d30}&to={today_s}&recompute=1",
            f"/member/strategy?from={d30}&to={today_s}",
        ]

    # The dedicated 30-minute prewarm cron must finish today's expensive scan
    # before the next run starts. Historical repairs belong to nightly mode;
    # processing older dates first can consume the whole cron window and leave
    # today's dashboard without a snapshot.
    return [
        f"/api/market-signals?date={today_s}&recompute=1",
        f"/member/strategy?from={d30}&to={today_s}",
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Warm boatrace strategy pages.")
    parser.add_argument(
        "--mode",
        choices=MODES,
        default="realtime",
        help="realtime repairs recent signal snapshots, morning-check reconciles daily pages, nightly does heavy history refresh.",
    )
    parser.add_argument(
        "--date",
        help="Target race date in YYYY-MM-DD. Defaults to the current date in Asia/Tokyo.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    today = date.fromisoformat(args.date) if args.date else datetime.now(JST).date()
    default_from = _days_ago(today, 30)
    yearly_from = _days_ago(today, 365)
    heavy_from = _days_ago(today, 1095)
    default_to = today.isoformat()
    monthly_from = "2024-06-01"
    monthly_to = today.isoformat()

    app = create_app()
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["is_member"] = True

    if args.mode in ("nightly", "morning-check"):
        ensure_performance_indexes()

    targets = build_targets(args.mode, today)

    ok = True
    for path in targets:
        status, size, valid, detail = _hit(client, path)
        print(
            f"{path} status={status} bytes={size} valid={valid} detail={detail}",
            flush=True,
        )
        if status != 200 or not valid:
            ok = False

    print(
        f"[prewarm] mode={args.mode} default_range={default_from}..{default_to} "
        f"year_range={yearly_from}..{default_to} heavy_range={heavy_from}..{default_to} "
        f"monthly_range={monthly_from}..{monthly_to}",
        flush=True,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
