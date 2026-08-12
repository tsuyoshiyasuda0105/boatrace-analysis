import json

from scripts.check_external_accident_snapshot import (
    ExternalAccidentRow,
    build_and_compare,
    compare_rows,
    main,
    parse_js_rows,
    parse_period,
    status_from_summary,
)


class _ReadOnlyConnection:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_dry_run_compares_without_any_write_or_calibration(monkeypatch):
    from scripts import check_external_accident_snapshot as checker

    external = {
        3246: ExternalAccidentRow(3246, 3, 20, 6.67, "F", "profile")
    }
    monkeypatch.setattr(
        checker,
        "fetch_external_data",
        lambda: ("2026-05-01", "2026-08-12", external),
    )
    monkeypatch.setattr(checker, "db_connect", lambda: _ReadOnlyConnection())
    monkeypatch.setattr(
        checker,
        "load_internal_rows",
        lambda *_args: {
            3246: {
                "starts_count": 3,
                "accident_events": 1,
                "accident_points": 20,
                "accident_rate": 6.67,
                "period_end": "2026-08-12",
            }
        },
    )
    for name in (
        "save_external_snapshot",
        "mirror_external_period_stats",
        "calibrate_reconstructed_period_stats",
    ):
        monkeypatch.setattr(
            checker,
            name,
            lambda *_args, name=name, **_kwargs: (_ for _ in ()).throw(
                AssertionError(f"dry-run called writer: {name}")
            ),
        )

    summary = build_and_compare("2026-08-13", dry_run=True)

    assert summary["dry_run"] is True
    assert summary["writes_performed"] is False
    assert summary["compared_rows"] == 1
    assert summary["mismatch_rows"] == 0
    assert summary["calibration_source_kind"] is None
    assert json.loads(json.dumps(summary))["writes_performed"] is False


def test_empty_external_payload_fails_before_db_access(monkeypatch):
    from scripts import check_external_accident_snapshot as checker

    monkeypatch.setattr(checker, "fetch_text", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(
        checker,
        "db_connect",
        lambda: (_ for _ in ()).throw(AssertionError("DB must not be opened")),
    )

    try:
        checker.fetch_external_data()
    except RuntimeError as exc:
        assert "period" in str(exc) or "zero racer rows" in str(exc)
    else:
        raise AssertionError("empty source must fail")


def test_dry_run_main_skips_production_guard_and_status_write(monkeypatch):
    from scripts import check_external_accident_snapshot as checker

    monkeypatch.setattr(checker.sys, "argv", ["check", "--date", "2026-08-13", "--dry-run"])
    monkeypatch.setattr(
        checker,
        "assert_safe_production_write",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("write guard called")),
    )
    monkeypatch.setattr(
        checker,
        "build_and_compare",
        lambda *_args, **_kwargs: {
            "compared_rows": 1,
            "mismatch_rows": 0,
            "point_mismatch_rows": 0,
            "nonzero_point_coverage": 1.0,
            "dry_run": True,
            "writes_performed": False,
        },
    )
    monkeypatch.setattr(
        checker,
        "upsert_status",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("status write called")),
    )

    assert main() == 0


def test_dry_run_main_classifies_timeout_without_writes(monkeypatch, capsys):
    from scripts import check_external_accident_snapshot as checker

    monkeypatch.setattr(checker.sys, "argv", ["check", "--date", "2026-08-13", "--dry-run"])
    monkeypatch.setattr(
        checker,
        "build_and_compare",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("upstream timed out")),
    )
    monkeypatch.setattr(
        checker,
        "db_connect",
        lambda: (_ for _ in ()).throw(AssertionError("failure path must not open DB")),
    )

    assert main() == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "error"
    assert payload["error_type"] == "TimeoutError"
    assert payload["writes_performed"] is False


def _raw_row(starts_hex: str, codes: str) -> str:
    chars = ["0"] * 37
    chars[21:23] = list(starts_hex)
    return "".join(chars) + codes


def test_parse_period_extracts_current_window():
    html = "<h1>2027年前期級別審査</h1><div>2026年5月1日～ 2026年8月4日</div>"
    tensu_js = "var YcurY=2026, YcurM=8, YcurD=4;"
    assert parse_period(html, tensu_js) == ("2026-05-01", "2026-08-04")


def test_parse_js_rows_decodes_starts_points_rate():
    tensu_js = "\n".join(
        [
            "var yp=new Array(0);",
            f"yp[3246]='{_raw_row('03', 'F')}';",
            f"yp[3081]='{_raw_row('0f', 'rxF')}';",
        ]
    )
    plain_js = "\n".join(
        [
            "xp[3246]='兵庫369443321D星野政彦';",
            "xp[3081]='山口487332321I岡本慎治';",
        ]
    )

    rows = parse_js_rows(tensu_js, plain_js)

    assert rows[3246].starts_count == 3
    assert rows[3246].accident_points == 20
    assert rows[3246].accident_rate == 6.67
    assert rows[3246].accident_codes_raw == "F"
    assert rows[3081].starts_count == 15
    assert rows[3081].accident_points == 37
    assert rows[3081].accident_rate == 2.47


def test_compare_rows_flags_point_and_rate_drift():
    external = parse_js_rows(
        "var yp=new Array(0);\n"
        f"yp[3246]='{_raw_row('03', 'F')}';\n"
        f"yp[3081]='{_raw_row('0f', 'rxF')}';\n",
        "xp[3246]='兵庫';\n"
        "xp[3081]='山口';\n",
    )
    internal = {
        3246: {"starts_count": 3, "accident_points": 0, "accident_rate": 0.0, "period_end": "2026-08-04"},
        3081: {"starts_count": 14, "accident_points": 0, "accident_rate": 0.0, "period_end": "2026-08-04"},
    }

    summary = compare_rows(external, internal)

    assert summary["compared_rows"] == 2
    assert summary["mismatch_rows"] == 2
    assert summary["point_mismatch_rows"] == 2
    assert summary["top_mismatches"][0]["racer_number"] == 3081


def test_status_from_summary_marks_large_point_drift_as_warning():
    status, message = status_from_summary(
        {
            "compared_rows": 100,
            "mismatch_rows": 12,
            "point_mismatch_rows": 7,
            "nonzero_point_coverage": 0.72,
        }
    )

    assert status == "warning"
    assert "監査差分あり" in message
