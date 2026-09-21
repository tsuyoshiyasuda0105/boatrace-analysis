"""二連複・三連複の締切前オッズ (2026-09-21 追加)。

fixture は 2026-09-17 平和島 1R の確定後ページ。確定後なので、当たり目の
オッズ × 100 が払戻と一致する (二連単 5-3 = 1,960円 / 二連複 3=5 = 840円 /
三連複 3=5=6 = 2,870円、race_payouts と照合済み)。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from src.parsers.odds import parse_exacta_odds, parse_quinella_odds, parse_trio_odds

FIX = Path(__file__).parent / "fixtures" / "parsers" / "odds"
ODDS2TF = FIX / "odds2tf_20260917_04_01.html"
ODDS3F = FIX / "odds3f_20260917_04_01.html"


def test_quinella_real_fixture_golden_values():
    odds = parse_quinella_odds(ODDS2TF.read_text(encoding="utf-8"))
    assert len(odds) == 15
    assert set(odds) == {f"{a}-{b}" for a in range(1, 7) for b in range(a + 1, 7)}
    assert odds["1-2"] == 3.1
    assert odds["1-3"] == 4.1
    assert odds["2-3"] == 6.3
    assert odds["3-5"] == 8.4      # 当たり目 (払戻 840円)
    assert odds["5-6"] == 26.0


def test_trio_real_fixture_golden_values():
    odds = parse_trio_odds(ODDS3F.read_text(encoding="utf-8"))
    assert len(odds) == 20
    assert set(odds) == {
        f"{a}-{b}-{c}" for a in range(1, 7) for b in range(a + 1, 7) for c in range(b + 1, 7)
    }
    assert odds["1-2-3"] == 4.2
    assert odds["1-2-4"] == 19.4
    assert odds["2-3-4"] == 19.1
    assert odds["3-5-6"] == 28.7   # 当たり目 (払戻 2,870円)
    assert odds["4-5-6"] == 54.9


def test_exacta_page_still_reads_exacta_and_winner_matches_payout():
    odds = parse_exacta_odds(ODDS2TF.read_text(encoding="utf-8"))
    assert len(odds) == 30 and odds["5-3"] == 19.6   # 当たり目 (払戻 1,960円)


def test_parsers_do_not_misread_the_other_pages():
    h2 = ODDS2TF.read_text(encoding="utf-8")
    h3 = ODDS3F.read_text(encoding="utf-8")
    h3t = (FIX / "odds3t_20260506_01_01.html").read_text(encoding="utf-8")
    assert parse_quinella_odds(h3) == {}
    assert parse_trio_odds(h2) == {}
    # 三連単のページ (18 列・20 行) も二連複・三連複として読まない
    assert parse_quinella_odds(h3t) == {}
    assert parse_trio_odds(h3t) == {}
    assert parse_quinella_odds("") == {} and parse_trio_odds("") == {}


def _db(tmp_path):
    db = tmp_path / "odds.db"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE race_results (race_id TEXT, boat_number INTEGER)")
    return db


def test_exacta_page_saves_exacta_and_quinella_in_one_fetch(tmp_path, monkeypatch):
    from src.collectors import odds as oc
    monkeypatch.setattr(oc.config, "ensure_dirs", lambda: None)
    calls = []
    monkeypatch.setattr(oc, "fetch_html", lambda url: calls.append(url) or ODDS2TF.read_text(encoding="utf-8"))
    db = _db(tmp_path)
    r = oc.collect_one_race_exacta("20260917-04-01", "T-5min", db_path=str(db))
    assert len(calls) == 1 and "odds2tf" in calls[0]
    assert r["odds_inserted"] == 30 and r["quinella_inserted"] == 15 and "error" not in r
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT COUNT(*) FROM odds_exacta").fetchone()[0] == 30
        assert c.execute("SELECT odds FROM odds_quinella WHERE combination='3-5'").fetchone()[0] == 8.4


def test_trio_page_saves_20(tmp_path, monkeypatch):
    from src.collectors import odds as oc
    monkeypatch.setattr(oc.config, "ensure_dirs", lambda: None)
    calls = []
    monkeypatch.setattr(oc, "fetch_html", lambda url: calls.append(url) or ODDS3F.read_text(encoding="utf-8"))
    db = _db(tmp_path)
    r = oc.collect_one_race_trio("20260917-04-01", "T-5min", db_path=str(db))
    assert len(calls) == 1 and "odds3f" in calls[0] and "jcd=04" in calls[0] and "rno=1" in calls[0]
    assert r["odds_inserted"] == 20 and "error" not in r
    with sqlite3.connect(db) as c:
        rows = c.execute("SELECT combination, odds, snapshot_label FROM odds_trio ORDER BY combination").fetchall()
    assert len(rows) == 20 and ("3-5-6", 28.7, "T-5min") in rows


def test_wrong_page_reports_error_and_saves_nothing(tmp_path, monkeypatch):
    from src.collectors import odds as oc
    monkeypatch.setattr(oc.config, "ensure_dirs", lambda: None)
    db = _db(tmp_path)
    r = oc.collect_one_race_trio("20260917-04-01", "T-5min", db_path=str(db),
                                 html=ODDS2TF.read_text(encoding="utf-8"))
    assert r["odds_inserted"] == 0 and r["error"] == "trio parsed 0/20"
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT name FROM sqlite_master WHERE name='odds_trio'").fetchone() is None


def test_scheduler_flags(monkeypatch):
    from scripts import odds_scheduler as sch
    for name in ("BOATRACE_ODDS_EXACTA", "BOATRACE_ODDS_TRIO", "BOATRACE_ODDS_ALL_RACES"):
        monkeypatch.delenv(name, raising=False)
    assert sch.exacta_enabled() and sch.trio_enabled()
    assert sch.all_races_enabled() is False          # 既定は従来どおり L4 候補に絞る
    monkeypatch.setenv("BOATRACE_ODDS_ALL_RACES", "1")
    assert sch.all_races_enabled() is True
    monkeypatch.setenv("BOATRACE_ODDS_TRIO", "0")
    assert sch.trio_enabled() is False


def test_run_one_pass_fetches_trio_after_exacta(monkeypatch):
    from scripts import odds_scheduler as sch
    order = []
    monkeypatch.setattr(sch, "find_due_snapshots", lambda now, lookahead_min=30: [("20260919-01-01", "T-5min")])
    monkeypatch.setattr(sch, "collect_one_race", lambda rid, snapshot_label: order.append("tri") or {"odds_inserted": 120})
    monkeypatch.setattr(sch, "collect_one_race_exacta", lambda rid, snapshot_label: order.append("exa") or {"odds_inserted": 30})
    monkeypatch.setattr(sch, "collect_one_race_trio", lambda rid, snapshot_label: order.append("trio") or {"odds_inserted": 20})
    monkeypatch.setattr(sch, "_auto_paper_trade", lambda rid, verbose=False: 0)
    rec = []
    monkeypatch.setattr(sch.odds_fetch_status, "record", lambda rows: rec.extend(rows) or len(rows))
    for name in ("BOATRACE_ODDS_EXACTA", "BOATRACE_ODDS_TRIO"):
        monkeypatch.delenv(name, raising=False)

    s = sch.run_one_pass()
    assert order == ["tri", "exa", "trio"]
    assert s["n_exacta"] == 1 and s["n_trio"] == 1
    assert {r[1] for r in rec} == {"T-5min", "T-5min/exacta", "T-5min/trio"}

    order.clear()
    monkeypatch.setenv("BOATRACE_ODDS_TRIO", "0")
    s = sch.run_one_pass()
    assert order == ["tri", "exa"] and "n_trio" not in s


def test_trio_failure_does_not_touch_trifecta_result(monkeypatch):
    from scripts import odds_scheduler as sch
    monkeypatch.setattr(sch, "find_due_snapshots", lambda now, lookahead_min=30: [("20260919-01-01", "T-5min")])
    monkeypatch.setattr(sch, "collect_one_race", lambda rid, snapshot_label: {"odds_inserted": 120})
    monkeypatch.setattr(sch, "collect_one_race_exacta", lambda rid, snapshot_label: {"odds_inserted": 30})

    def boom(rid, snapshot_label):
        raise RuntimeError("3連複のページが読めません")

    monkeypatch.setattr(sch, "collect_one_race_trio", boom)
    monkeypatch.setattr(sch, "_auto_paper_trade", lambda rid, verbose=False: 0)
    rec = []
    monkeypatch.setattr(sch.odds_fetch_status, "record", lambda rows: rec.extend(rows) or len(rows))
    s = sch.run_one_pass()
    assert s["n_done"] == 1 and s["n_failed"] == 0
    states = {r[1]: r[2] for r in rec}
    assert states["T-5min"] == "ok" and states["T-5min/trio"] == "error"


def test_partial_tables_keep_what_was_readable():
    """欠場などで数字が欠けた組があっても、読めた組は捨てない。"""
    h2 = ODDS2TF.read_text(encoding="utf-8").replace(">3.1<", ">欠場<", 1)
    q = parse_quinella_odds(h2)
    assert len(q) == 14 and "1-2" not in q and q["1-3"] == 4.1
    h3 = ODDS3F.read_text(encoding="utf-8").replace(">4.2<", ">欠場<", 1)
    t = parse_trio_odds(h3)
    assert len(t) == 19 and "1-2-3" not in t and t["1-2-4"] == 19.4
