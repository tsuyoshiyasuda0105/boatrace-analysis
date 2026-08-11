from datetime import date

import pytest
import requests

from src.collectors import official_manifest


def _link(venue: int, race: int, day: str = "20260812") -> str:
    return (
        "/owpc/pc/race/racelist?"
        f"rno={race}&hd={day}&jcd={venue:02d}"
    )


class _Response:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


class _Session:
    def __init__(self, response=None, error=None) -> None:
        self.response = response
        self.error = error
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return self.response


def test_parser_returns_deterministic_audit_expected_payload():
    html = "".join(
        [
            f'<a href="{_link(3, 1)}">venue 3</a>',
            f'<a href="{_link(1, 1)}">venue 1</a>',
        ]
    )

    assert official_manifest.parse_official_race_manifest(html, date(2026, 8, 12)) == {
        "stadiums": [
            {"stadium_number": 1, "race_numbers": list(range(1, 13))},
            {"stadium_number": 3, "race_numbers": list(range(1, 13))},
        ]
    }


def test_parser_uses_link_path_and_query_not_text_or_parameter_order():
    html = """
    <a href="https://www.boatrace.jp/owpc/pc/race/racelist?jcd=24&amp;rno=7&amp;hd=20260812#top">
      unrelated visible text
    </a>
    <a href="/owpc/pc/race/raceresult?jcd=01&rno=1&hd=20260812">ignore</a>
    <a href="https://example.com/owpc/pc/race/racelist?jcd=01&rno=1&hd=20260812">ignore</a>
    <a href="/owpc/pc/race/racelist?jcd=01&rno=1&hd=20260811">other day</a>
    """

    assert official_manifest.parse_official_race_manifest(html, "20260812") == {
        "stadiums": [
            {"stadium_number": 24, "race_numbers": list(range(1, 13))}
        ]
    }


def test_real_index_single_link_expands_venue_and_exposes_correlated_missing_race_12():
    expected = official_manifest.parse_official_race_manifest(
        f'<a href="{_link(5, 1)}">venue 5 race list</a>', "2026-08-12"
    )
    expected_keys = {
        (item["stadium_number"], race)
        for item in expected["stadiums"]
        for race in item["race_numbers"]
    }
    both_sources_missing_race_12 = {(5, race) for race in range(1, 12)}

    assert expected_keys - both_sources_missing_race_12 == {(5, 12)}


@pytest.mark.parametrize(
    "href",
    [
        _link(0, 1),
        _link(25, 1),
        _link(1, 0),
        _link(1, 13),
        "/owpc/pc/race/racelist?jcd=x&rno=1&hd=20260812",
        "/owpc/pc/race/racelist?jcd=1&rno=x&hd=20260812",
        "/owpc/pc/race/racelist?jcd=1&rno=1",
    ],
)
def test_parser_rejects_invalid_natural_keys(href):
    with pytest.raises(official_manifest.ManifestParseError):
        official_manifest.parse_official_race_manifest(
            f'<a href="{href}">race</a>', "2026-08-12"
        )


def test_parser_deduplicates_repeated_venue_links():
    first_href = _link(1, 1)
    repeated_href = _link(1, 2)

    assert official_manifest.parse_official_race_manifest(
        f'<a href="{first_href}">desktop</a>'
        f'<a href="{repeated_href}">mobile</a>',
        "2026-08-12",
    ) == {
        "stadiums": [
            {"stadium_number": 1, "race_numbers": list(range(1, 13))}
        ]
    }


def test_parser_rejects_empty_output():
    with pytest.raises(official_manifest.ManifestEmptyError):
        official_manifest.parse_official_race_manifest(
            "<html><body>No race links</body></html>", "2026-08-12"
        )


def test_fetch_available_uses_config_user_agent_and_bounded_timeout(monkeypatch):
    monkeypatch.setattr(official_manifest.config, "USER_AGENT", "safe-test-agent")
    monkeypatch.setattr(official_manifest.config, "REQUEST_TIMEOUT_SECONDS", 999)
    session = _Session(_Response(200, f'<a href="{_link(1, 1)}">race</a>'))

    result = official_manifest.fetch_official_race_manifest(
        "2026-08-12", session=session
    )

    assert result == {
        "status": "available",
        "target_date": "2026-08-12",
        "source_url": "https://www.boatrace.jp/owpc/pc/race/index?hd=20260812",
        "http_status": 200,
        "expected_payload": {
            "stadiums": [
                {"stadium_number": 1, "race_numbers": list(range(1, 13))}
            ]
        },
    }
    _, kwargs = session.calls[0]
    assert kwargs == {
        "timeout": 30.0,
        "headers": {"User-Agent": "safe-test-agent"},
    }


@pytest.mark.parametrize(
    ("status_code", "expected_status"),
    [(404, "not_published"), (500, "http_error"), (204, "empty")],
)
def test_fetch_classifies_http_and_empty_states(status_code, expected_status):
    result = official_manifest.fetch_official_race_manifest(
        "20260812", session=_Session(_Response(status_code))
    )

    assert result["status"] == expected_status
    assert result["http_status"] == status_code
    assert result["expected_payload"] is None


def test_fetch_classifies_timeout_without_exposing_exception():
    result = official_manifest.fetch_official_race_manifest(
        "2026-08-12",
        timeout=0,
        session=_Session(error=requests.Timeout("secret response detail")),
    )

    assert result["status"] == "timeout"
    assert result["http_status"] is None
    assert "secret" not in repr(result)


def test_fetch_classifies_request_error_without_exposing_exception():
    result = official_manifest.fetch_official_race_manifest(
        "2026-08-12",
        session=_Session(error=requests.ConnectionError("private endpoint detail")),
    )

    assert result["status"] == "http_error"
    assert "private" not in repr(result)


def test_fetch_classifies_parse_error():
    malformed = '<a href="/owpc/pc/race/racelist?rno=1&jcd=x&hd=20260812">bad</a>'

    result = official_manifest.fetch_official_race_manifest(
        "2026-08-12", session=_Session(_Response(200, malformed))
    )

    assert result["status"] == "parse_error"
    assert result["expected_payload"] is None
