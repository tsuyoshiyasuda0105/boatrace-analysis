from src.web.app import _parse_market_signal_bets_for_roi


def test_parse_exacta_does_not_count_bet_type_number_as_win():
    assert _parse_market_signal_bets_for_roi({"bet": "2連単 1-3"}) == [
        ("exacta", "1-3")
    ]


def test_parse_multiple_combinations_keeps_each_ticket():
    assert _parse_market_signal_bets_for_roi({"bet": "3連単 1-2-3 / 1-3-2"}) == [
        ("trifecta", "1-2-3"),
        ("trifecta", "1-3-2"),
    ]


def test_parse_win_ticket():
    assert _parse_market_signal_bets_for_roi({"bet": "単勝 2号艇"}) == [
        ("win", "2")
    ]
