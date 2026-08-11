"""Read-only collector for the official BOAT RACE daily race manifest."""

from __future__ import annotations

from datetime import date, datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import requests

import config


INDEX_URL = "https://www.boatrace.jp/owpc/pc/race/index?hd={date}"
_BASE_URL = "https://www.boatrace.jp/"
_ALLOWED_HOSTS = {"boatrace.jp", "www.boatrace.jp"}
_MAX_TIMEOUT_SECONDS = 30.0


class ManifestParseError(ValueError):
    """Raised when relevant official links contain invalid or duplicate data."""


class ManifestEmptyError(ManifestParseError):
    """Raised when the page contains no races for the target date."""


class _LinkCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        for name, value in attrs:
            if name.lower() == "href" and value:
                self.hrefs.append(value)
                return


def _normalize_target_date(target_date: date | str) -> tuple[str, str]:
    if isinstance(target_date, datetime):
        value = target_date.date()
    elif isinstance(target_date, date):
        value = target_date
    elif isinstance(target_date, str):
        raw = target_date.strip()
        try:
            value = datetime.strptime(raw, "%Y%m%d" if "-" not in raw else "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValueError("target_date must be YYYY-MM-DD or YYYYMMDD") from exc
    else:
        raise TypeError("target_date must be a date or string")
    return value.isoformat(), value.strftime("%Y%m%d")


def _query_value(query: dict[str, list[str]], name: str) -> str | None:
    values = query.get(name)
    if values is None:
        return None
    if len(values) != 1 or not values[0]:
        raise ManifestParseError(f"official race link has an invalid {name} parameter")
    return values[0]


def _query_int(query: dict[str, list[str]], name: str) -> int:
    raw = _query_value(query, name)
    if raw is None or not raw.isdigit():
        raise ManifestParseError(f"official race link has an invalid {name} parameter")
    return int(raw)


def parse_official_race_manifest(html: str, target_date: date | str) -> dict[str, Any]:
    """Parse race-list links into the expected payload accepted by the audit CLI."""
    if not isinstance(html, str):
        raise TypeError("html must be a string")

    _, compact_date = _normalize_target_date(target_date)
    parser = _LinkCollector()
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:
        raise ManifestParseError("official race index HTML could not be parsed") from exc

    venues: set[int] = set()
    for href in parser.hrefs:
        parsed = urlparse(urljoin(_BASE_URL, href))
        if parsed.hostname not in _ALLOWED_HOSTS:
            continue
        if parsed.path.rstrip("/").rsplit("/", 1)[-1].lower() != "racelist":
            continue

        query = {key.lower(): values for key, values in parse_qs(parsed.query).items()}
        if "jcd" not in query and "rno" not in query:
            continue
        venue = _query_int(query, "jcd")
        race = _query_int(query, "rno")
        link_date = _query_value(query, "hd")
        if link_date is None:
            raise ManifestParseError("official race link is missing the hd parameter")
        if link_date != compact_date:
            continue
        if not 1 <= venue <= 24:
            raise ManifestParseError("official race link has an out-of-range venue")
        if not 1 <= race <= 12:
            raise ManifestParseError("official race link has an out-of-range race")

        if venue in venues:
            raise ManifestParseError(
                f"official race index contains duplicate venue {venue:02d}"
            )
        venues.add(venue)

    if not venues:
        raise ManifestEmptyError("official race index contains no target-date venues")

    return {
        "stadiums": [
            {"stadium_number": venue, "race_numbers": list(range(1, 13))}
            for venue in sorted(venues)
        ]
    }


def _bounded_timeout(timeout: float | None) -> float:
    configured = config.REQUEST_TIMEOUT_SECONDS if timeout is None else timeout
    try:
        value = float(configured)
    except (TypeError, ValueError) as exc:
        raise ValueError("timeout must be numeric") from exc
    return min(max(value, 1.0), _MAX_TIMEOUT_SECONDS)


def fetch_official_race_manifest(
    target_date: date | str,
    *,
    timeout: float | None = None,
    session: Any | None = None,
) -> dict[str, Any]:
    """Fetch and parse the official index without writing to DB, cache, or disk."""
    iso_date, compact_date = _normalize_target_date(target_date)
    url = INDEX_URL.format(date=compact_date)
    result: dict[str, Any] = {
        "status": "http_error",
        "target_date": iso_date,
        "source_url": url,
        "http_status": None,
        "expected_payload": None,
    }

    client = session if session is not None else requests
    try:
        response = client.get(
            url,
            timeout=_bounded_timeout(timeout),
            headers={"User-Agent": config.USER_AGENT},
        )
    except requests.Timeout:
        result["status"] = "timeout"
        return result
    except requests.RequestException:
        return result

    result["http_status"] = int(response.status_code)
    if response.status_code == 404:
        result["status"] = "not_published"
        return result
    if response.status_code < 200 or response.status_code >= 300:
        return result

    try:
        result["expected_payload"] = parse_official_race_manifest(
            response.text, iso_date
        )
    except ManifestEmptyError:
        result["status"] = "empty"
        return result
    except (ManifestParseError, TypeError, UnicodeError):
        result["status"] = "parse_error"
        return result

    result["status"] = "available"
    return result
