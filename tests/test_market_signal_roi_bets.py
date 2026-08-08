from src.web.app import (
    _market_signal_is_inside_escape,
    _parse_market_signal_bets_for_roi,
    _race_has_entry_change_caution_outside_in,
)


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


def test_inside_escape_detects_all_first_boat_one_patterns():
    assert _market_signal_is_inside_escape({"bet": "3連単 1-2-3 / 1-3-2"})
    assert _market_signal_is_inside_escape({"bet": "2連単 1-3"})
    assert _market_signal_is_inside_escape({"bet": "単勝 1号艇"})


def test_inside_escape_rejects_non_one_head_or_missing_bets():
    assert not _market_signal_is_inside_escape({"bet": "3連単 2-1-3"})
    assert not _market_signal_is_inside_escape({"bet": "単勝 2号艇"})
    assert not _market_signal_is_inside_escape({})


def test_entry_change_caution_ignores_boat_one(monkeypatch):
    monkeypatch.setattr(
        "src.web.app._race_detail_tag_snapshot",
        lambda race_id, recompute=False: {
            "boats": {
                "1": {"entry_change_tag": {"label": "注意"}},
                "2": {},
            }
        },
    )
    assert not _race_has_entry_change_caution_outside_in("20260808-19-10")


def test_entry_change_caution_detects_outer_boat_tag(monkeypatch):
    monkeypatch.setattr(
        "src.web.app._race_detail_tag_snapshot",
        lambda race_id, recompute=False: {
            "boats": {
                "1": {},
                "5": {"entry_change_tag": {"label": "注意"}},
            }
        },
    )
    assert _race_has_entry_change_caution_outside_in("20260808-19-10")
