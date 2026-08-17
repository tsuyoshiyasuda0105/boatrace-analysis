from __future__ import annotations

import os
import sys
import json
from datetime import date, datetime, timedelta
from pathlib import Path
import argparse
from urllib.parse import parse_qs, urlencode, urlparse
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

os.environ["BOATRACE_TASK_TRIGGER"] = "render-prewarm"

from src.web.app import (  # noqa: E402
    create_app,
    invalidate_cache,
    _market_signals_cache_key,
    _read_json_cache_stale,
)
from src.db.connection import connect as db_connect  # noqa: E402
from src.roi_contract import ROI_DAILY_CACHE_VERSION, strategy_definition_signature  # noqa: E402
from scripts.ensure_performance_indexes import ensure_performance_indexes  # noqa: E402


MODES = (
    "signals",
    "realtime",
    "morning-check",
    "daily-reconcile",
    "history",
    "nightly",
)
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


def _validate_member_strategy_cache(path: str) -> tuple[bool, str]:
    """Confirm recomputed ROI pages actually wrote finalized daily rows."""
    parsed = urlparse(path)
    if parsed.path != "/member/strategy" or "recompute=1" not in path:
        return True, "ok"

    qs = parse_qs(parsed.query)
    from_s = (qs.get("from") or [None])[0]
    to_s = (qs.get("to") or [None])[0]
    if not from_s or not to_s:
        return True, "ok"

    today_s = datetime.now(JST).date().isoformat()
    try:
        # Do not require today's row. It is still provisional while races are
        # running, and the dashboard should not fail because today is incomplete.
        if from_s >= today_s:
            return True, "no finalized dates"
        with db_connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    r.race_date,
                    COUNT(DISTINCT r.race_id) AS n_total,
                    COUNT(DISTINCT rr.race_id) AS n_result,
                    c.stats_json
                FROM races r
                LEFT JOIN race_results rr ON rr.race_id = r.race_id
                LEFT JOIN l4_daily_stats_cache c ON c.race_date = r.race_date
                WHERE r.race_date BETWEEN ? AND ?
                  AND r.race_date < ?
                GROUP BY r.race_date, c.stats_json
                ORDER BY r.race_date
                """,
                (from_s, to_s, today_s),
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        return False, f"roi daily cache validation failed: {exc}"

    missing: list[str] = []
    checked = 0
    for rdate, n_total, n_result, stats_json in rows:
        if int(n_total or 0) <= 0 or int(n_result or 0) <= 0:
            continue
        checked += 1
        if not stats_json:
            missing.append(f"{rdate}:missing")
            continue
        try:
            payload = json.loads(stats_json)
        except Exception:
            missing.append(f"{rdate}:invalid-json")
            continue
        version = payload.get("_adopted_daily_select_version")
        if version != ROI_DAILY_CACHE_VERSION:
            missing.append(f"{rdate}:version={version or '-'}")
        elif payload.get("_strategy_definition_signature") != strategy_definition_signature(REPO):
            missing.append(f"{rdate}:strategy-signature")

    if missing:
        return False, "roi daily cache missing/invalid " + ",".join(missing[:8])
    return True, f"roi daily cache verified dates={checked}"


def _hit(client, path: str) -> tuple[int, int, bool, str]:
    resp = client.get(path)
    body = resp.get_data() or b""
    if resp.status_code != 200:
        return resp.status_code, len(body), False, f"http={resp.status_code}"
    if path.startswith("/api/market-signals") and "recompute=1" in path:
        valid, detail = _validate_market_signal_response(resp, path)
        if valid:
            payload = resp.get_json(silent=True) or {}
            parsed = urlparse(path)
            qs = parse_qs(parsed.query)
            qs.pop("recompute", None)
            readback_query = urlencode(
                [(key, value) for key, values in qs.items() for value in values]
            )
            readback_path = parsed.path + (f"?{readback_query}" if readback_query else "")
            invalidate_cache()
            persisted_payload = _read_json_cache_stale(
                _market_signals_cache_key(str(payload.get("date") or ""))
            )
            readback_payload = persisted_payload
            if not isinstance(readback_payload, dict):
                return (
                    resp.status_code,
                    len(body),
                    False,
                    f"{detail}; persisted cache missing path={readback_path}",
                )
            if readback_payload.get("date") != payload.get("date"):
                return resp.status_code, len(body), False, f"{detail}; persisted readback date mismatch"
            if not isinstance(readback_payload.get("signals"), dict):
                return resp.status_code, len(body), False, f"{detail}; persisted signals missing"
            if len(readback_payload["signals"]) != len(payload.get("signals") or {}):
                return (
                    resp.status_code,
                    len(body),
                    False,
                    f"{detail}; persisted signal count mismatch "
                    f"{len(readback_payload['signals'])}!={len(payload.get('signals') or {})}",
                )
            detail = f"{detail}; persisted=db"
        return resp.status_code, len(body), valid, detail
    if path.startswith("/member/strategy") and "recompute=1" in path:
        valid, detail = _validate_member_strategy_cache(path)
        return resp.status_code, len(body), valid, detail
    return resp.status_code, len(body), True, "ok"


def _prepare_internal_session(client) -> None:
    """Grant the in-process maintenance client the same role as scheduled ROI jobs."""
    with client.session_transaction() as sess:
        sess["is_member"] = True
        sess["role"] = "admin"
        sess["auth_provider"] = "internal_prewarm"


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

    if mode == "history":
        # Keep the regular Render cron below the Starter 512MiB memory cap.
        # Recomputing 3y/1y/monthly ROI pages in one Flask process repeatedly
        # OOMs on Render, so the scheduled history slot only refreshes the
        # short finalized window that changes day to day. Long-range pages are
        # served from persisted ROI history/cache and can be rebuilt manually
        # with a larger one-off job when strategy definitions change.
        return [
            f"/member/strategy?from={d30}&to={today_s}&recompute=1",
            f"/member/strategy?from={d30}&to={today_s}",
        ]

    if mode == "nightly":
        # Heavy historical refresh. This is intentionally reserved for the
        # end-of-day Render scheduler so normal app clicks never trigger it.
        # Rebuild yesterday first: after results/payouts arrive, the ROI cache
        # must overlay the same high-ROI signal payload that users saw.
        return [
            f"/api/market-signals?date={yesterday_s}&recompute=1",
            f"/api/market-signals?date={today_s}&recompute=1",
            *build_targets("history", today),
        ]

    if mode == "morning-check":
        # Reconcile visible pages after the morning race/prediction load.
        return [
            f"/api/market-signals?date={today_s}&recompute=1",
            f"/member/strategy?from={d30}&to={today_s}&recompute=1",
            f"/member/strategy?from={d30}&to={today_s}",
        ]

    if mode == "daily-reconcile":
        # Repair only yesterday's finalized ROI row. This is deliberately
        # small enough for the regular Render cron self-heal path.
        return [
            f"/api/market-signals?date={yesterday_s}&recompute=1",
            f"/member/strategy?from={yesterday_s}&to={today_s}&recompute=1",
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


def _create_prewarm_app():
    """Use persisted predictions; strategy prewarm must never load ML models."""
    return create_app(cached_predictions_only=True)


def main() -> int:
    args = parse_args()
    today = date.fromisoformat(args.date) if args.date else datetime.now(JST).date()
    default_from = _days_ago(today, 30)
    yearly_from = _days_ago(today, 365)
    heavy_from = _days_ago(today, 1095)
    default_to = today.isoformat()
    monthly_from = "2024-06-01"
    monthly_to = today.isoformat()

    app = _create_prewarm_app()
    client = app.test_client()
    _prepare_internal_session(client)

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
