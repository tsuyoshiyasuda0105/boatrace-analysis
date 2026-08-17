#!/usr/bin/env python3
"""Safety-first staged load-test harness for the production Boatrace web app.

The measured workload is GET-only.  The sole non-GET request this program can
make is one POST to /login when authentication is explicitly enabled and the
password is supplied through BOATRACE_MEMBER_PASSWORD.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from typing import Sequence
from urllib.parse import urlparse

from bs4 import BeautifulSoup
import httpx


DEFAULT_BASE_URL = "https://boatrace-web.onrender.com"
DEFAULT_STAGES = "2,5,10,20,40"
HARD_MAX_CONCURRENCY = 100
HIGH_CONCURRENCY_GUARD = 20
JST = timezone(timedelta(hours=9))
RACE_ID_RE = re.compile(r"^\d{8}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class Endpoint:
    name: str
    path: str


@dataclass(frozen=True)
class Sample:
    endpoint: str
    latency_sec: float
    status: str
    success: bool


@dataclass
class StageAccumulator:
    samples: list[Sample] = field(default_factory=list)
    stop_reason: str | None = None

    def add(self, sample: Sample) -> None:
        self.samples.append(sample)


def parse_stages(value: str, max_concurrency: int) -> list[int]:
    """Parse a strictly increasing, positive ramp bounded by both caps."""
    if not 1 <= max_concurrency <= HARD_MAX_CONCURRENCY:
        raise ValueError(
            f"max_concurrency must be between 1 and {HARD_MAX_CONCURRENCY}"
        )
    try:
        stages = [int(part.strip()) for part in value.split(",") if part.strip()]
    except ValueError as exc:
        raise ValueError("stages must be comma-separated integers") from exc
    if not stages:
        raise ValueError("at least one stage is required")
    if any(stage <= 0 for stage in stages):
        raise ValueError("stages must contain only positive integers")
    if stages != sorted(set(stages)):
        raise ValueError("stages must be unique and strictly increasing")
    if any(stage > max_concurrency for stage in stages):
        raise ValueError(
            f"stage exceeds max_concurrency cap ({max_concurrency}): {stages}"
        )
    return stages


def percentile(values: Sequence[float], quantile: float) -> float | None:
    """Return an interpolated percentile, or None for an empty sequence."""
    if not values:
        return None
    if not 0 <= quantile <= 1:
        raise ValueError("quantile must be between 0 and 1")
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def latency_percentiles(samples: Sequence[Sample]) -> dict[str, float | None]:
    latencies = [sample.latency_sec for sample in samples]
    return {
        "p50_sec": percentile(latencies, 0.50),
        "p95_sec": percentile(latencies, 0.95),
        "p99_sec": percentile(latencies, 0.99),
    }


def evaluate_stop(
    samples: Sequence[Sample], error_rate_stop: float, p95_stop_sec: float
) -> str | None:
    """Evaluate circuit-breaker thresholds against current stage samples."""
    if not samples:
        return None
    failures = sum(not sample.success for sample in samples)
    error_rate = failures / len(samples)
    if error_rate > error_rate_stop:
        return (
            f"error_rate {error_rate:.1%} exceeded threshold "
            f"{error_rate_stop:.1%}"
        )
    p95 = percentile([sample.latency_sec for sample in samples], 0.95)
    if p95 is not None and p95 > p95_stop_sec:
        return f"p95 {p95:.3f}s exceeded threshold {p95_stop_sec:.3f}s"
    return None


def summarize_stage(
    concurrency: int,
    samples: Sequence[Sample],
    elapsed_sec: float,
    stop_reason: str | None,
) -> dict[str, object]:
    successes = sum(sample.success for sample in samples)
    failures = len(samples) - successes
    endpoints: Counter[str] = Counter(sample.endpoint for sample in samples)
    statuses: Counter[str] = Counter(sample.status for sample in samples)
    return {
        "concurrency": concurrency,
        "elapsed_sec": round(elapsed_sec, 6),
        "requests": len(samples),
        "successes": successes,
        "failures": failures,
        "error_rate": failures / len(samples) if samples else 0.0,
        **latency_percentiles(samples),
        "throughput_rps": len(samples) / elapsed_sec if elapsed_sec > 0 else 0.0,
        "status_breakdown": dict(sorted(statuses.items())),
        "endpoint_breakdown": dict(sorted(endpoints.items())),
        "stopped": stop_reason is not None,
        "stop_reason": stop_reason,
    }


def _today_jst() -> str:
    return datetime.now(JST).date().isoformat()


def _extract_csrf(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    field = soup.select_one('input[name="csrf_token"]')
    token = field.get("value") if field else None
    if not token:
        raise RuntimeError("login page did not contain a CSRF token")
    return str(token)


def extract_today_race_ids(html: str, today_iso: str) -> list[str]:
    """Extract only canonical same-day race IDs proven by the races page."""
    compact_date = today_iso.replace("-", "")
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[str] = []
    for node in soup.select("[data-race-id]"):
        candidates.append(str(node.get("data-race-id") or ""))
    for anchor in soup.select('a[href*="/race/"]'):
        path = urlparse(str(anchor.get("href") or "")).path
        if "/race/" in path:
            candidates.append(path.split("/race/", 1)[1].strip("/"))
    valid = {
        candidate
        for candidate in candidates
        if RACE_ID_RE.fullmatch(candidate) and candidate.startswith(compact_date + "-")
    }
    return sorted(valid)


async def authenticate_once(client: httpx.AsyncClient, password: str) -> None:
    login_page = await client.get("/login")
    login_page.raise_for_status()
    csrf_token = _extract_csrf(login_page.text)
    response = await client.post(
        "/login",
        data={"password": password, "csrf_token": csrf_token, "next": "/races"},
        follow_redirects=False,
    )
    if response.status_code not in {302, 303}:
        raise RuntimeError(f"one-time login failed with HTTP {response.status_code}")
    if not client.cookies:
        raise RuntimeError("one-time login succeeded without a session cookie")


async def discover_race_ids(
    client: httpx.AsyncClient, today_iso: str, limit: int = 3
) -> list[str]:
    response = await client.get("/races", params={"date": today_iso})
    if response.status_code != 200:
        raise RuntimeError(
            f"race-ID discovery failed with HTTP {response.status_code}; refusing fake IDs"
        )
    race_ids = extract_today_race_ids(response.text, today_iso)
    if not race_ids:
        raise RuntimeError(
            "no real same-day race_id found on production /races; refusing detail load"
        )
    return race_ids[:limit]


def build_endpoints(authenticated: bool, today_iso: str, race_ids: Sequence[str]) -> list[Endpoint]:
    # Repetition is intentional weighting: health remains at least half the mix.
    endpoints = [Endpoint("healthz", "/healthz") for _ in range(6)]
    if not authenticated:
        return endpoints
    endpoints.extend(
        [
            Endpoint("races", f"/races?date={today_iso}"),
            Endpoint("races", f"/races?date={today_iso}"),
            Endpoint("market_signals", f"/api/market-signals?date={today_iso}"),
        ]
    )
    endpoints.extend(Endpoint("race_detail", f"/race/{race_id}") for race_id in race_ids)
    return endpoints


async def _worker(
    worker_number: int,
    client: httpx.AsyncClient,
    endpoints: Sequence[Endpoint],
    accumulator: StageAccumulator,
    stop_event: asyncio.Event,
    error_rate_stop: float,
    p95_stop_sec: float,
    request_interval_sec: float,
) -> None:
    request_number = worker_number
    while not stop_event.is_set():
        endpoint = endpoints[request_number % len(endpoints)]
        request_number += 1
        started = time.perf_counter()
        try:
            response = await client.get(endpoint.path, follow_redirects=False)
            latency = time.perf_counter() - started
            status = str(response.status_code)
            success = 200 <= response.status_code < 300
        except asyncio.CancelledError:
            raise
        except httpx.HTTPError as exc:
            latency = time.perf_counter() - started
            status = f"EXC:{type(exc).__name__}"
            success = False
        accumulator.add(Sample(endpoint.name, latency, status, success))
        reason = evaluate_stop(accumulator.samples, error_rate_stop, p95_stop_sec)
        if reason:
            accumulator.stop_reason = reason
            stop_event.set()
            return
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=request_interval_sec)
        except TimeoutError:
            pass


async def run_stage(
    client: httpx.AsyncClient,
    endpoints: Sequence[Endpoint],
    concurrency: int,
    stage_seconds: float,
    error_rate_stop: float,
    p95_stop_sec: float,
    request_interval_sec: float,
) -> dict[str, object]:
    accumulator = StageAccumulator()
    stop_event = asyncio.Event()
    workers = [
        asyncio.create_task(
            _worker(
                worker_number,
                client,
                endpoints,
                accumulator,
                stop_event,
                error_rate_stop,
                p95_stop_sec,
                request_interval_sec,
            )
        )
        for worker_number in range(concurrency)
    ]
    started = time.perf_counter()
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=stage_seconds)
    except TimeoutError:
        pass
    finally:
        stop_event.set()
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
    elapsed = time.perf_counter() - started
    return summarize_stage(
        concurrency, accumulator.samples, elapsed, accumulator.stop_reason
    )


def bottleneck_hypothesis(stage: dict[str, object] | None) -> str:
    if not stage:
        return "No knee was observed; bottleneck is not identifiable from this run."
    statuses = stage.get("status_breakdown") or {}
    if any(str(code) in {"429", "502", "503", "504"} for code in statuses):
        return (
            "HTTP 429/502/503/504 increased near the knee; request-slot, upstream, "
            "or DB-pool saturation is the leading hypothesis. Correlate Render and DB metrics."
        )
    if float(stage.get("error_rate") or 0) > 0:
        return (
            "Errors increased near the knee; inspect the status breakdown and correlate "
            "application/DB logs before attributing the cause."
        )
    return (
        "Latency crossed the guard without an error spike; queueing behind the 8 Gunicorn "
        "thread slots or DB-pool contention is the leading hypothesis. Confirm with monitoring."
    )


def build_report(
    args: argparse.Namespace,
    authenticated: bool,
    race_ids: Sequence[str],
    stage_results: list[dict[str, object]],
    started_at: datetime,
) -> dict[str, object]:
    stopped_stage = next((stage for stage in stage_results if stage["stopped"]), None)
    safe_stages = [stage for stage in stage_results if not stage["stopped"]]
    return {
        "schema_version": 1,
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "base_url": args.base_url,
        "authentication": "on" if authenticated else "off",
        "race_ids": list(race_ids),
        "configuration": {
            "stages": args.parsed_stages,
            "stage_seconds": args.stage_seconds,
            "max_concurrency": args.max_concurrency,
            "error_rate_stop": args.error_rate_stop,
            "p95_stop_sec": args.p95_stop_sec,
            "request_interval_sec": args.request_interval_sec,
            "request_method": "GET",
        },
        "stages": stage_results,
        "safe_max_concurrency": max(
            (int(stage["concurrency"]) for stage in safe_stages), default=None
        ),
        "knee_concurrency": (
            int(stopped_stage["concurrency"]) if stopped_stage else None
        ),
        "bottleneck_hypothesis": bottleneck_hypothesis(stopped_stage),
    }


def _fmt_seconds(value: object) -> str:
    return "-" if value is None else f"{float(value):.3f}"


def print_summary(report: dict[str, object], output_path: Path) -> None:
    print("\nBoatrace load-test summary (measured workload: GET only)")
    print("conc  req  err%    p50s    p95s    p99s    req/s  stopped")
    for stage in report["stages"]:
        print(
            f"{stage['concurrency']:>4} {stage['requests']:>5} "
            f"{stage['error_rate'] * 100:>5.1f} "
            f"{_fmt_seconds(stage['p50_sec']):>7} "
            f"{_fmt_seconds(stage['p95_sec']):>7} "
            f"{_fmt_seconds(stage['p99_sec']):>7} "
            f"{stage['throughput_rps']:>8.2f}  "
            f"{stage['stop_reason'] or '-'}"
        )
        print(f"     statuses={stage['status_breakdown']}")
    print(f"Safe maximum concurrency: {report['safe_max_concurrency']}")
    print(f"Knee concurrency: {report['knee_concurrency']}")
    print(f"Bottleneck hypothesis: {report['bottleneck_hypothesis']}")
    print(f"JSON result: {output_path}")


def _validate_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    try:
        args.parsed_stages = parse_stages(args.stages, args.max_concurrency)
    except ValueError as exc:
        parser.error(str(exc))
    parsed_url = urlparse(args.base_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        parser.error("base-url must be an absolute http(s) URL")
    if args.stage_seconds <= 0:
        parser.error("stage-seconds must be positive")
    if not 0 <= args.error_rate_stop <= 1:
        parser.error("error-rate-stop must be between 0 and 1")
    if args.p95_stop_sec <= 0:
        parser.error("p95-stop-sec must be positive")
    if args.request_interval_sec <= 0:
        parser.error("request-interval-sec must be positive")
    if (
        max(args.parsed_stages) >= HIGH_CONCURRENCY_GUARD
        and not args.allow_high_concurrency
    ):
        parser.error(
            f"stages >= {HIGH_CONCURRENCY_GUARD} require --allow-high-concurrency "
            "after operator safety review and live monitoring are ready"
        )
    args.base_url = args.base_url.rstrip("/")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--stages", default=DEFAULT_STAGES)
    parser.add_argument("--stage-seconds", type=float, default=25.0)
    parser.add_argument("--max-concurrency", type=int, default=HARD_MAX_CONCURRENCY)
    parser.add_argument("--error-rate-stop", type=float, default=0.10)
    parser.add_argument("--p95-stop-sec", type=float, default=15.0)
    parser.add_argument("--request-interval-sec", type=float, default=1.0)
    parser.add_argument("--auth", choices=("on", "off"), default="on")
    parser.add_argument(
        "--allow-high-concurrency",
        action="store_true",
        help=f"required for any stage >= {HIGH_CONCURRENCY_GUARD}",
    )
    _validate_args(args := parser.parse_args(argv), parser)
    return args


async def async_main(args: argparse.Namespace) -> tuple[dict[str, object], Path]:
    started_at = datetime.now(timezone.utc)
    today_iso = _today_jst()
    password = os.environ.get("BOATRACE_MEMBER_PASSWORD", "")
    authenticated = args.auth == "on" and bool(password)
    if args.auth == "on" and not password:
        print(
            "BOATRACE_MEMBER_PASSWORD is unset; falling back to auth off and measuring "
            "public /healthz only.",
            file=sys.stderr,
        )
    timeout = httpx.Timeout(args.p95_stop_sec, connect=min(10.0, args.p95_stop_sec))
    headers = {"User-Agent": "boatrace-safety-load-harness/1.0"}
    async with httpx.AsyncClient(
        base_url=args.base_url, timeout=timeout, headers=headers
    ) as client:
        race_ids: list[str] = []
        if authenticated:
            await authenticate_once(client, password)
            # Do not retain an extra password reference after the one allowed login.
            password = ""
            race_ids = await discover_race_ids(client, today_iso)
            print(f"Authenticated once; reusing session cookie. Real race IDs: {race_ids}")
        endpoints = build_endpoints(authenticated, today_iso, race_ids)
        stage_results: list[dict[str, object]] = []
        for concurrency in args.parsed_stages:
            print(
                f"Starting stage concurrency={concurrency} duration={args.stage_seconds:g}s "
                f"(GET only, interval={args.request_interval_sec:g}s)"
            )
            result = await run_stage(
                client,
                endpoints,
                concurrency,
                args.stage_seconds,
                args.error_rate_stop,
                args.p95_stop_sec,
                args.request_interval_sec,
            )
            stage_results.append(result)
            if result["stopped"]:
                print(f"Circuit breaker opened: {result['stop_reason']}", file=sys.stderr)
                break
    report = build_report(
        args, authenticated, race_ids, stage_results, started_at
    )
    reports_dir = Path(__file__).resolve().parents[1] / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    timestamp = started_at.strftime("%Y%m%dT%H%M%SZ")
    output_path = reports_dir / f"load_test_result_{timestamp}.json"
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report, output_path


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report, output_path = asyncio.run(async_main(args))
    except KeyboardInterrupt:
        print("Interrupted by operator; no higher stage will be started.", file=sys.stderr)
        return 130
    except (httpx.HTTPError, RuntimeError) as exc:
        print(f"Load test aborted safely: {exc}", file=sys.stderr)
        return 1
    print_summary(report, output_path)
    return 2 if report["knee_concurrency"] is not None else 0


if __name__ == "__main__":
    raise SystemExit(main())
