import json

import pytest

from scripts.audit_program_source_consistency import (
    AuditInputError,
    audit_program_source_consistency,
    classify_program_source_gate,
    main,
)


def _official_race(stadium=1, race=1, deadline="2026-08-12 10:30:00"):
    return {
        "race_date": "2026-08-12",
        "stadium_number": stadium,
        "race_number": race,
        "race_closed_at": deadline,
        "boats": [
            {
                "boat_number": boat,
                "racer_number": 4000 + boat,
                "assigned_motor_number": 10 + boat,
            }
            for boat in range(1, 7)
        ],
    }


def _openapi_race(stadium=1, race=1, deadline="2026-08-12T10:30:00"):
    return {
        "race_date": "2026-08-12",
        "race_stadium_number": stadium,
        "race_number": race,
        "race_closed_at": deadline,
        "boats": [
            {
                "racer_boat_number": boat,
                "racer_number": 4000 + boat,
                "racer_assigned_motor_number": 10 + boat,
            }
            for boat in range(1, 7)
        ],
    }


def test_consistent_parser_and_openapi_payload():
    report = audit_program_source_consistency(
        [_official_race()], {"today": {"programs": [_openapi_race()]}}
    )

    assert report["status"] == "consistent"
    assert report["summary"]["issue_count"] == 0
    assert all(not items for items in report["issues"].values())


def test_reports_stadium_and_race_missing_from_each_source():
    official = [_official_race(stadium=1), _official_race(stadium=2)]
    openapi = {
        "programs": [
            _openapi_race(stadium=1),
            _openapi_race(stadium=3),
        ]
    }

    report = audit_program_source_consistency(official, openapi)

    assert report["issues"]["stadium_missing"] == [
        {"stadium_number": 2, "missing_from": "openapi", "present_in": "official_b"},
        {"stadium_number": 3, "missing_from": "official_b", "present_in": "openapi"},
    ]
    assert {(item["stadium_number"], item["missing_from"]) for item in report["issues"]["race_missing"]} == {
        (2, "openapi"),
        (3, "official_b"),
    }


def test_reports_missing_race_within_a_shared_stadium():
    official = [_official_race(race=1), _official_race(race=2)]
    openapi = {"data": {"programs": [_openapi_race(race=1)]}}

    report = audit_program_source_consistency(official, openapi)

    assert report["issues"]["stadium_missing"] == []
    assert report["issues"]["race_missing"] == [
        {
            "race_id": "20260812-01-02",
            "stadium_number": 1,
            "race_number": 2,
            "missing_from": "openapi",
            "present_in": "official_b",
        }
    ]


def test_reports_incomplete_and_duplicate_boat_numbers():
    official = _official_race()
    official["boats"][-1]["boat_number"] = 5

    report = audit_program_source_consistency([official], {"programs": [_openapi_race()]})

    assert report["issues"]["incomplete_boats"] == [
        {
            "race_id": "20260812-01-01",
            "stadium_number": 1,
            "race_number": 1,
            "source": "official_b",
            "boat_count": 6,
            "unique_boat_count": 5,
            "missing_boat_numbers": [6],
            "unexpected_boat_numbers": [],
        }
    ]
    assert report["issues"]["duplicate_boat_numbers"][0]["duplicate_boat_numbers"] == [5]


def test_reports_racer_motor_and_deadline_mismatches():
    openapi = _openapi_race(deadline="2026-08-12T10:31:00")
    openapi["boats"][1]["racer_number"] = 9999
    openapi["boats"][2]["racer_assigned_motor_number"] = 99

    report = audit_program_source_consistency(
        [_official_race()], {"programs": [openapi]}
    )

    assert [(item.get("boat_number"), item["field"]) for item in report["issues"]["mismatches"]] == [
        (None, "race_closed_at"),
        (2, "racer_number"),
        (3, "motor_number"),
    ]


def test_cli_outputs_json_and_uses_exit_codes(tmp_path, capsys):
    official_path = tmp_path / "official.json"
    openapi_path = tmp_path / "openapi.json"
    official_path.write_text(json.dumps([_official_race()]), encoding="utf-8")
    openapi_path.write_text(
        json.dumps({"programs": [_openapi_race(deadline="2026-08-12 10:31:00")]}),
        encoding="utf-8",
    )

    exit_code = main(["--official", str(official_path), "--openapi", str(openapi_path)])
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert report["status"] == "inconsistent"
    assert report["summary"]["issue_counts"]["mismatches"] == 1


def test_cli_rejects_unsupported_fixture_shape(tmp_path, capsys):
    official_path = tmp_path / "official.json"
    openapi_path = tmp_path / "openapi.json"
    official_path.write_text("{}", encoding="utf-8")
    openapi_path.write_text(json.dumps({"programs": []}), encoding="utf-8")

    exit_code = main(["--official", str(official_path), "--openapi", str(openapi_path)])
    error = json.loads(capsys.readouterr().err)

    assert exit_code == 2
    assert error["status"] == "input_error"


def test_empty_sources_are_never_considered_consistent():
    report = audit_program_source_consistency([], {"programs": []})

    assert report["status"] == "inconsistent"
    assert report["issues"]["source_empty"] == [
        {"source": "official_b"},
        {"source": "openapi"},
    ]


def test_reports_target_date_and_required_field_gaps():
    openapi = _openapi_race()
    openapi["race_date"] = "2026-08-13"
    openapi["boats"][0]["racer_assigned_motor_number"] = None

    report = audit_program_source_consistency(
        [_official_race()],
        {"programs": [openapi]},
        target_date="2026-08-12",
    )

    assert report["issues"]["invalid_dates"][0]["source"] == "openapi"
    assert report["issues"]["missing_required_fields"][0]["field"] == "motor_number"
    assert any(item["field"] == "race_date" for item in report["issues"]["mismatches"])


@pytest.mark.parametrize(
    ("stadium", "race"),
    [(0, 1), (25, 1), (1, 0), (1, 13)],
)
def test_rejects_out_of_range_race_keys(stadium, race):
    with pytest.raises(AuditInputError, match="out-of-range"):
        audit_program_source_consistency(
            [_official_race(stadium=stadium, race=race)],
            {"programs": [_openapi_race()]},
        )


def test_rejects_race_id_that_disagrees_with_natural_key():
    official = _official_race()
    official["race_id"] = "20260812-02-01"

    with pytest.raises(AuditInputError, match="race_id does not match"):
        audit_program_source_consistency(
            [official],
            {"programs": [_openapi_race()]},
        )


def test_expected_manifest_detects_correlated_missing_race():
    expected = {
        "stadiums": [
            {"stadium_number": 1, "race_numbers": [1, 2]},
        ]
    }

    report = audit_program_source_consistency(
        [_official_race(race=1)],
        {"programs": [_openapi_race(race=1)]},
        target_date="2026-08-12",
        expected_payload=expected,
    )

    assert report["status"] == "inconsistent"
    assert report["issues"]["expected_missing"] == [
        {
            "race_id": "20260812-01-02",
            "stadium_number": 1,
            "race_number": 2,
            "missing_from": ["official_b", "openapi"],
        }
    ]
    assert report["summary"]["expected_race_count"] == 2


@pytest.mark.parametrize("wrapper", ["programs", "races"])
def test_expected_race_array_is_consistent_when_both_sources_have_race(wrapper):
    report = audit_program_source_consistency(
        [_official_race()],
        {"programs": [_openapi_race()]},
        expected_payload={
            wrapper: [
                {
                    "race_date": "2026-08-12",
                    "stadium_number": 1,
                    "race_number": 1,
                }
            ]
        },
    )

    assert report["status"] == "consistent"
    assert report["issues"]["expected_missing"] == []


@pytest.mark.parametrize(
    "expected",
    [
        {"stadium_number": 0, "race_numbers": [1]},
        {"stadium_number": 1, "race_numbers": [0]},
        {"stadium_number": 1, "race_numbers": [1, 1]},
        {
            "stadiums": [
                {"stadium_number": 1, "race_numbers": [1]},
                {"stadium_number": 1, "race_numbers": [2]},
            ]
        },
        {
            "races": [
                {"stadium_number": 1, "race_number": 1},
                {"stadium_number": 1, "race_number": 1},
            ]
        },
    ],
)
def test_rejects_invalid_expected_manifest(expected):
    with pytest.raises(AuditInputError, match="out-of-range|duplicate"):
        audit_program_source_consistency(
            [_official_race()],
            {"programs": [_openapi_race()]},
            expected_payload=expected,
        )


def test_cli_expected_manifest_reports_correlated_missing(tmp_path, capsys):
    official_path = tmp_path / "official.json"
    openapi_path = tmp_path / "openapi.json"
    expected_path = tmp_path / "expected.json"
    official_path.write_text(json.dumps([_official_race(race=1)]), encoding="utf-8")
    openapi_path.write_text(
        json.dumps({"programs": [_openapi_race(race=1)]}), encoding="utf-8"
    )
    expected_path.write_text(
        json.dumps({"stadium_number": 1, "race_numbers": [1, 2]}),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "--official",
            str(official_path),
            "--openapi",
            str(openapi_path),
            "--expected",
            str(expected_path),
            "--date",
            "2026-08-12",
        ]
    )
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert report["summary"]["issue_counts"]["expected_missing"] == 1


def test_gate_waits_when_openapi_is_not_published():
    report = audit_program_source_consistency(
        [_official_race()],
        {"programs": []},
        expected_payload={"stadium_number": 1, "race_numbers": [1]},
    )

    assert classify_program_source_gate(report, openapi_state="not_published") == "retry_wait"


def test_gate_blocks_incomplete_official_source_even_when_openapi_is_unavailable():
    report = audit_program_source_consistency(
        [],
        {"programs": []},
        expected_payload={"stadium_number": 1, "race_numbers": [1]},
    )

    assert classify_program_source_gate(report, openapi_state="timeout") == "blocked"


def test_gate_allows_deadline_revision_as_warning():
    report = audit_program_source_consistency(
        [_official_race(deadline="2026-08-12 10:30:00")],
        {"programs": [_openapi_race(deadline="2026-08-12T10:31:00")]},
        expected_payload={"stadium_number": 1, "race_numbers": [1]},
    )

    assert classify_program_source_gate(report) == "ready_with_warning"


def test_gate_requires_independent_expected_manifest():
    report = audit_program_source_consistency(
        [_official_race()],
        {"programs": [_openapi_race()]},
    )

    assert classify_program_source_gate(report) == "retry_wait"
