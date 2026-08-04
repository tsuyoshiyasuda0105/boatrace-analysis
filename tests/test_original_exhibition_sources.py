import sqlite3
from datetime import date, timedelta

from src.collectors import original_exhibition
from src.collectors.original_exhibition import SOURCE_PATTERNS
from src.parsers.original_exhibition import parse_original_exhibition


def daterange(start: date, end: date, *, newest_first: bool = False):
    days = [start + timedelta(days=offset) for offset in range((end - start).days + 1)]
    if newest_first:
        days.reverse()
    yield from days


def test_omura_confirmed_source_is_first():
    source_name, pattern = SOURCE_PATTERNS[24][0]

    assert source_name == "omura_syussou"
    assert pattern.format(date="20260723", rno=7) == (
        "https://www.omurakyotei.jp/yosou/sp/syussou/?day=20260723&race=07"
    )


def test_daterange_can_process_newest_first():
    values = list(daterange(date(2026, 7, 21), date(2026, 7, 23), newest_first=True))

    assert values == [date(2026, 7, 23), date(2026, 7, 22), date(2026, 7, 21)]


def test_amagasaki_confirmed_source_and_parallel_columns():
    source_name, pattern = SOURCE_PATTERNS[13][0]
    html = """
    <div class="com-rname">Racer 1</div><div class="com-rname">Racer 2</div>
    <div class="com-rname">Racer 3</div><div class="com-rname">Racer 4</div>
    <div class="com-rname">Racer 5</div><div class="com-rname">Racer 6</div>
    <div class="col6">Original</div><div class="col6">Lap</div><div class="col6">Turn</div>
    <div class="col6">37.01</div><div class="col6">37.02</div><div class="col6">37.03</div>
    <div class="col6">37.04</div><div class="col6">37.05</div><div class="col6">37.06</div>
    <div class="col7">11.01</div><div class="col7">11.02</div><div class="col7">11.03</div>
    <div class="col7">11.04</div><div class="col7">11.05</div><div class="col7">11.06</div>
    """

    rows = parse_original_exhibition(html)

    assert source_name == "amagasaki_cyokuzen"
    assert "group-cyokuzen.php" in pattern
    assert len(rows) == 6
    assert rows[0]["lap_time"] == 37.01
    assert rows[5]["turn_time"] == 11.06


def test_parallel_columns_include_straight_and_reject_half_lap():
    names = "".join(f'<div class="com-rname">Racer {n}</div>' for n in range(1, 7))
    html = names + "".join(
        [
            '<div class="col6">Original</div>',
            *[f'<div class="col6">{18 + n / 10:.2f}</div>' for n in range(1, 7)],
            '<div class="col7">Turn</div>',
            *[f'<div class="col7">{4 + n / 10:.2f}</div>' for n in range(1, 7)],
            '<div class="col8">Straight</div>',
            *[f'<div class="col8">{7 + n / 10:.2f}</div>' for n in range(1, 7)],
        ]
    )

    rows = parse_original_exhibition(html)

    assert len(rows) == 6
    assert "lap_time" not in rows[0]
    assert rows[0]["turn_time"] == 4.1
    assert rows[5]["straight_time"] == 7.6


def test_tokuyama_linear_mobile_page():
    values = " ".join(
        f"展示：7.0{n} 一周：38.0{n} まわり足：11.8{n}"
        for n in range(1, 7)
    )

    rows = parse_original_exhibition(f"<html><body>{values}</body></html>")

    assert len(rows) == 6
    assert rows[0]["lap_time"] == 38.01
    assert rows[5]["turn_time"] == 11.86


def test_miyajima_original_exhibition_table():
    source_name, pattern = SOURCE_PATTERNS[17][0]
    header = """
      <tr>
        <th>枠</th><th>選手名</th><th>体重</th><th>チルト</th>
        <th>展示</th><th>一周</th><th>まわり足</th><th>直線</th><th>調整</th>
      </tr>
    """
    body = "".join(
        f"<tr><td>{boat}</td><td>選手{boat}</td><td>52.0</td><td>0.0</td>"
        f"<td>6.7{boat}</td><td>37.4{boat}</td><td>5.9{boat}</td>"
        f"<td>7.0{boat}</td><td>0.0</td></tr>"
        for boat in range(1, 7)
    )

    rows = parse_original_exhibition(f"<table>{header}{body}</table>")

    assert source_name == "miyajima_kaisai_reload"
    assert "kaisai_reload.php" in pattern
    assert len(rows) == 6
    assert rows[0]["lap_time"] == 37.41
    assert rows[0]["turn_time"] == 5.91
    assert rows[0]["straight_time"] == 7.01


def test_fukuoka_original_exhibition_parallel_columns():
    source_name, pattern = SOURCE_PATTERNS[22][0]
    names = "".join(f'<div class="com-rname">Racer {n}</div>' for n in range(1, 7))
    html = names + "".join(
        [
            '<div class="col6">Exhibition</div>',
            *[f'<div class="col6">{6.7 + n / 100:.2f}</div>' for n in range(1, 7)],
            '<div class="col7">Lap</div>',
            *[f'<div class="col7">{37 + n / 100:.2f}</div>' for n in range(1, 7)],
            '<div class="col8">Turn</div>',
            *[f'<div class="col8">{5 + n / 100:.2f}</div>' for n in range(1, 7)],
            '<div class="col9">Straight</div>',
            *[f'<div class="col9">{7 + n / 100:.2f}</div>' for n in range(1, 7)],
        ]
    )

    rows = parse_original_exhibition(html)

    assert source_name == "fukuoka_tenji_info"
    assert "tenji_info.php" in pattern
    assert len(rows) == 6
    assert rows[0]["lap_time"] == 37.01
    assert rows[0]["turn_time"] == 5.01
    assert rows[0]["straight_time"] == 7.01


def test_tamagawa_original_exhibition_source_and_parallel_columns():
    source_name, pattern = SOURCE_PATTERNS[5][0]
    names = "".join(f'<div class="com-rname">Racer {n}</div>' for n in range(1, 7))
    html = names + "".join(
        [
            '<div class="col6">Exhibition</div>',
            *[f'<div class="col6">{6.7 + n / 100:.2f}</div>' for n in range(1, 7)],
            '<div class="col7">Lap</div>',
            *[f'<div class="col7">{37 + n / 100:.2f}</div>' for n in range(1, 7)],
            '<div class="col8">Turn</div>',
            *[f'<div class="col8">{5 + n / 100:.2f}</div>' for n in range(1, 7)],
            '<div class="col9">Straight</div>',
            *[f'<div class="col9">{7 + n / 100:.2f}</div>' for n in range(1, 7)],
        ]
    )

    rows = parse_original_exhibition(html)

    assert source_name == "tamagawa_oriten"
    assert "oriten.php" in pattern
    assert len(rows) == 6
    assert rows[0]["lap_time"] == 37.01
    assert rows[0]["turn_time"] == 5.01
    assert rows[0]["straight_time"] == 7.01


def test_shimonoseki_confirmed_source_and_parallel_columns():
    source_name, pattern = SOURCE_PATTERNS[19][0]
    names = "".join(f'<div class="com-rname">Racer {n}</div>' for n in range(1, 7))
    html = names + "".join(
        [
            '<div class="col6">Lap</div>',
            *[f'<div class="col6">{36.6 + n / 100:.2f}</div>' for n in range(1, 7)],
            '<div class="col7">Turn</div>',
            *[f'<div class="col7">{5.2 + n / 100:.2f}</div>' for n in range(1, 7)],
            '<div class="col8">Straight</div>',
            *[f'<div class="col8">{7.3 + n / 100:.2f}</div>' for n in range(1, 7)],
        ]
    )

    rows = parse_original_exhibition(html)

    assert source_name == "shimonoseki_group_cyokuzen"
    assert pattern.format(date="20260729", rno=11) == (
        "https://www.boatrace-shimonoseki.jp/modules/yosou/"
        "group-cyokuzen.php?day=20260729&race=11&kind=2"
    )
    assert len(rows) == 6
    assert rows[0]["lap_time"] == 36.61
    assert rows[0]["turn_time"] == 5.21
    assert rows[0]["straight_time"] == 7.31


def test_mikuni_confirmed_source():
    source_name, pattern = SOURCE_PATTERNS[10][0]

    assert source_name == "mikuni_cyokuzen"
    assert pattern.format(date="20260723", rno=7) == (
        "https://www.boatrace-mikuni.jp/modules/yosou/"
        "group-cyokuzen.php?day=20260723&race=7&kind=2"
    )


def test_kojima_confirmed_source_and_control_groups():
    source_name, pattern = SOURCE_PATTERNS[16][0]
    names = "".join(f'<div class="ren-name">Racer {n}</div>' for n in range(1, 7))
    controls = []
    for boat in range(1, 7):
        controls.extend(
            [
                f'<div class="control">{6.6 + boat / 100:.2f}</div>',
                f'<div class="control">{0.1 + boat / 100:.2f}</div>',
                f'<div class="control">{37 + boat / 100:.2f}</div>',
                f'<div class="control">{5 + boat / 100:.2f}</div>',
                f'<div class="control">{7 + boat / 100:.2f}</div>',
            ]
        )

    rows = parse_original_exhibition(names + "".join(controls))

    assert source_name == "kojima_hjpc"
    assert pattern.format(date="20260723", rno=7) == (
        "https://hj.kojima-yosou.com/hjpc/index/20260723/07"
    )
    assert len(rows) == 6
    assert rows[0]["lap_time"] == 37.01
    assert rows[0]["turn_time"] == 5.01
    assert rows[0]["straight_time"] == 7.01


def test_original_exhibition_filter_retries_partial_metric_rows():
    conn = sqlite3.connect(":memory:")
    original_exhibition.ensure_schema(conn)
    target = [("20260804-05-01", 5, 1)]

    conn.execute(
        """
        INSERT INTO race_original_exhibitions (
            race_id, boat_number, source_name, stadium_number, race_date,
            race_number, turn_time, source_url, collected_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("20260804-05-01", 1, "tamagawa_oriten", 5, "2026-08-04", 1, 5.21, "https://example.test", "now"),
    )
    assert original_exhibition._filter_missing(conn, target, force=False) == target

    for boat in range(2, 7):
        conn.execute(
            """
            INSERT INTO race_original_exhibitions (
                race_id, boat_number, source_name, stadium_number, race_date,
                race_number, turn_time, source_url, collected_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "20260804-05-01",
                boat,
                "tamagawa_oriten",
                5,
                "2026-08-04",
                1,
                5.2 + boat / 100,
                "https://example.test",
                "now",
            ),
        )

    assert original_exhibition._filter_missing(conn, target, force=False) == []


def test_original_exhibition_filter_retries_rows_with_no_usable_times():
    conn = sqlite3.connect(":memory:")
    original_exhibition.ensure_schema(conn)
    target = [("20260804-05-02", 5, 2)]

    for boat in range(1, 7):
        conn.execute(
            """
            INSERT INTO race_original_exhibitions (
                race_id, boat_number, source_name, stadium_number, race_date,
                race_number, raw_text, source_url, collected_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "20260804-05-02",
                boat,
                "tamagawa_oriten",
                5,
                "2026-08-04",
                2,
                "no metric values yet",
                "https://example.test",
                "now",
            ),
        )

    assert original_exhibition._filter_missing(conn, target, force=False) == target
