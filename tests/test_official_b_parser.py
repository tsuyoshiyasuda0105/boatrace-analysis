from src.parsers.official_b import _parse_boat_row


def test_parses_three_digit_boat_number_without_column_space():
    row = "1 3898平田忠則49福岡52A1 7.30 58.20 6.59 52.63 16 49.51123 47.37"

    parsed = _parse_boat_row(row)

    assert parsed is not None
    assert parsed["assigned_motor_number"] == 16
    assert parsed["assigned_motor_top_2_percent"] == 49.51
    assert parsed["assigned_boat_number"] == 123
    assert parsed["assigned_boat_top_2_percent"] == 47.37


def test_parses_motor_number_touching_one_hundred_percent_rate():
    row = "2 4637中田友也36埼玉57A2 5.40 36.76 5.86 37.29  8100.00 15 28.57"

    parsed = _parse_boat_row(row)

    assert parsed is not None
    assert parsed["assigned_motor_number"] == 8
    assert parsed["assigned_motor_top_2_percent"] == 100.00
    assert parsed["assigned_boat_number"] == 15
    assert parsed["assigned_boat_top_2_percent"] == 28.57


def test_parses_local_rates_touching_national_rate():
    # 2026-10-23 浜名湖1R: 当地勝率 10.00 と当地2率 100.00 が隙間なしでくっつく。
    # 読めずにレースごと落ち、番組表の確認が毎朝止まっていた。
    row = "3 4472尾嶋一広39兵庫56A1 6.60 50.4110.00100.00 21 34.74 15 44.44 6151         8"

    parsed = _parse_boat_row(row)

    assert parsed is not None
    assert parsed["national_top_2_percent"] == 50.41
    assert parsed["local_top_1_percent"] == 10.00
    assert parsed["local_top_2_percent"] == 100.00
    assert parsed["assigned_motor_number"] == 21
    assert parsed["assigned_boat_number"] == 15
