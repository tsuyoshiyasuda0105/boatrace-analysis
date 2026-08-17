from __future__ import annotations

from argparse import Namespace
import asyncio
from datetime import datetime, timezone

import httpx
import pytest

from scripts.load_test import (
    Sample,
    authenticate_once,
    build_endpoints,
    build_report,
    evaluate_stop,
    extract_today_race_ids,
    latency_percentiles,
    parse_args,
    parse_stages,
    percentile,
)


def _sample(latency: float, *, success: bool = True, status: str = "200") -> Sample:
    return Sample("healthz", latency, status, success)


def test_parse_stages_builds_strict_bounded_ramp() -> None:
    assert parse_stages("2, 5,10", 10) == [2, 5, 10]


@pytest.mark.parametrize(
    ("value", "cap", "message"),
    [
        ("", 100, "at least one"),
        ("2,0,5", 100, "positive"),
        ("2,5,5", 100, "strictly increasing"),
        ("5,2", 100, "strictly increasing"),
        ("2,20", 10, "exceeds"),
        ("2,nope", 100, "integers"),
        ("2", 101, "between 1 and 100"),
    ],
)
def test_parse_stages_rejects_unsafe_ramps(value: str, cap: int, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_stages(value, cap)


def test_percentile_and_latency_aggregation_are_interpolated() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert percentile(values, 0.50) == pytest.approx(3.0)
    assert percentile(values, 0.95) == pytest.approx(4.8)
    assert percentile(values, 0.99) == pytest.approx(4.96)
    assert latency_percentiles([_sample(value) for value in values]) == {
        "p50_sec": pytest.approx(3.0),
        "p95_sec": pytest.approx(4.8),
        "p99_sec": pytest.approx(4.96),
    }
    assert percentile([], 0.95) is None


def test_stop_opens_only_when_error_rate_strictly_exceeds_threshold() -> None:
    at_threshold = [_sample(0.1, success=False, status="503")] + [
        _sample(0.1) for _ in range(9)
    ]
    assert evaluate_stop(at_threshold, 0.10, 15.0) is None

    above_threshold = at_threshold + [_sample(0.1, success=False, status="503")]
    reason = evaluate_stop(above_threshold, 0.10, 15.0)
    assert reason is not None
    assert "error_rate" in reason


def test_stop_opens_when_p95_strictly_exceeds_threshold() -> None:
    safe = [_sample(14.0) for _ in range(20)]
    assert evaluate_stop(safe, 0.10, 15.0) is None

    slow = [_sample(16.0) for _ in range(20)]
    reason = evaluate_stop(slow, 0.10, 15.0)
    assert reason is not None
    assert "p95" in reason


def test_stop_does_nothing_without_measurements() -> None:
    assert evaluate_stop([], 0.10, 15.0) is None


def test_extract_today_race_ids_uses_only_real_canonical_same_day_values() -> None:
    html = """
      <li data-race-id="20260817-01-01"></li>
      <a href="/race/20260817-02-03?from=top">real</a>
      <a href="/race/20260816-02-03">yesterday</a>
      <a href="/race/not-real">invalid</a>
    """
    assert extract_today_race_ids(html, "2026-08-17") == [
        "20260817-01-01",
        "20260817-02-03",
    ]


def test_endpoint_mix_is_public_only_without_auth_and_get_paths_with_auth() -> None:
    public = build_endpoints(False, "2026-08-17", [])
    assert {endpoint.path for endpoint in public} == {"/healthz"}

    authenticated = build_endpoints(
        True, "2026-08-17", ["20260817-01-01", "20260817-02-03"]
    )
    assert all(endpoint.path.startswith("/") for endpoint in authenticated)
    assert "/races?date=2026-08-17" in {endpoint.path for endpoint in authenticated}
    assert "/race/20260817-01-01" in {endpoint.path for endpoint in authenticated}
    assert sum(endpoint.name == "healthz" for endpoint in authenticated) * 2 >= len(
        authenticated
    )


def test_high_concurrency_requires_explicit_operator_guard() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--stages", "2,20"])
    args = parse_args(["--stages", "2,20", "--allow-high-concurrency"])
    assert args.parsed_stages == [2, 20]


def test_authentication_performs_exactly_one_get_and_one_login_post_offline() -> None:
    requests: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(
                200,
                text='<input name="csrf_token" value="token-1">',
            )
        return httpx.Response(
            302,
            headers={"location": "/races", "set-cookie": "session=fake; Path=/"},
        )

    async def exercise() -> None:
        async with httpx.AsyncClient(
            base_url="https://example.test",
            transport=httpx.MockTransport(handler),
        ) as client:
            await authenticate_once(client, "env-only-test-value")
            assert client.cookies.get("session") == "fake"

    asyncio.run(exercise())
    assert requests == [("GET", "/login"), ("POST", "/login")]


def test_report_records_first_broken_stage_as_knee() -> None:
    args = Namespace(
        base_url="https://example.test",
        parsed_stages=[2, 5],
        stage_seconds=10.0,
        max_concurrency=100,
        error_rate_stop=0.10,
        p95_stop_sec=15.0,
        request_interval_sec=1.0,
    )
    stages = [
        {"concurrency": 2, "stopped": False},
        {
            "concurrency": 5,
            "stopped": True,
            "status_breakdown": {"503": 2},
            "error_rate": 0.2,
        },
    ]
    report = build_report(
        args,
        authenticated=False,
        race_ids=[],
        stage_results=stages,
        started_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
    )
    assert report["safe_max_concurrency"] == 2
    assert report["knee_concurrency"] == 5
    assert "DB-pool" in report["bottleneck_hypothesis"]


def test_harness_source_has_no_workload_mutation_or_embedded_password() -> None:
    source = (__import__("pathlib").Path(__file__).parents[1] / "scripts" / "load_test.py").read_text(
        encoding="utf-8"
    )
    assert "client.post(" in source  # exactly the one permitted login exchange
    assert source.count("client.post(") == 1
    assert "BOATRACE_MEMBER_PASSWORD" in source
    assert "password = os.environ.get" in source
    assert "client.delete(" not in source
    assert "client.put(" not in source
    assert "client.patch(" not in source
