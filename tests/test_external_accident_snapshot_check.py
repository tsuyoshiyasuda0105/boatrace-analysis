from scripts.check_external_accident_snapshot import (
    compare_rows,
    parse_js_rows,
    parse_period,
    status_from_summary,
)


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
