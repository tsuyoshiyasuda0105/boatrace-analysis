"""Fixture: first cp932 record from data/raw/fan/fan2604.txt (April 2026).

The 418-byte record, including its original CRLF, was copied without decoding
or re-encoding from the official fan handbook text file.
"""

import hashlib
from pathlib import Path

from src.parsers.official_f import parse_fan_file


FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "parsers"
    / "official_f"
    / "fan2604_first_record.txt"
)


def test_parse_official_f_real_fixture_golden_values() -> None:
    raw = FIXTURE.read_bytes()
    assert len(raw) == 418
    assert hashlib.sha256(raw).hexdigest() == (
        "2a88fd901f31efb3e05ecea901cb98b44e357492bcdb838f5cfd9844523c7a16"
    )

    rows = parse_fan_file(FIXTURE)

    assert rows == [
        {
            "racer_number": 2538,
            "name": "高橋二朗",
            "name_kana": "ﾀｶﾊｼ ｼﾞﾛｳ",
            "branch_text": "東京",
            "class_number": 3,
            "birth_date": "1949-04-26",
            "gender": 1,
        }
    ]


def test_parse_official_f_empty_file_is_safe(tmp_path: Path) -> None:
    empty_file = tmp_path / "empty_fan.txt"
    empty_file.write_bytes(b"")
    assert parse_fan_file(empty_file) == []
