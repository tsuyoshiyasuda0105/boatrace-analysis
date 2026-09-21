"""二連単の前向き記録 (src/evaluation/exacta_forward.py) と周辺の結合。"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pytest

from src.evaluation import exacta_forward as fx


JST = timezone(timedelta(hours=9))
FIXTURE = Path(__file__).parent / "fixtures" / "parsers" / "odds" / "odds2tf_20260917_04_01.html"


# ---------------------------------------------------------------- 確率

def test_exacta_probabilities_sum_to_one_and_follow_first_place():
    p1 = np.array([[0.70, 0.10, 0.08, 0.06, 0.04, 0.02]])
    p2 = np.array([[0.85, 0.40, 0.30, 0.22, 0.15, 0.08]])
    p3 = np.array([[0.92, 0.65, 0.55, 0.45, 0.28, 0.15]])
    pe = fx.exacta_probabilities(p1, p2, p3)
    assert pe.shape == (1, 30)
    assert abs(pe.sum() - 1.0) < 1e-6
    # 1 号艇が圧倒的なら本命は 1-2
    assert fx.EXACTA_LABELS[int(np.argmax(pe[0]))] == "1-2"
    # 1 着艇ごとの合計 = 1 着確率
    by_first = np.zeros(6)
    for k, (a, b) in enumerate(fx._EXACTA):
        by_first[a] += pe[0, k]
    assert np.allclose(by_first, p1[0] / p1[0].sum(), atol=1e-6)


def test_exacta_probabilities_uniform_when_boats_equal():
    p1 = np.full((1, 6), 1 / 6)
    p2 = np.full((1, 6), 2 / 6)
    p3 = np.full((1, 6), 3 / 6)
    pe = fx.exacta_probabilities(p1, p2, p3)
    assert np.allclose(pe, 1 / 30, atol=1e-3)


def test_inconsistent_calibrated_inputs_do_not_blow_up():
    # 1 着 85% の艇に「ちょうど 2 着 19%」が付く (ありえない) 入力でも発散しない
    p1 = np.array([[0.855, 0.0067, 0.0986, 0.0132, 0.0058, 0.0207]])
    p2 = np.array([[0.855 + 0.1933, 0.0494, 0.5507, 0.1295, 0.0556, 0.1666]])
    p3 = np.array([[0.999, 0.30, 0.80, 0.45, 0.25, 0.40]])
    pe = fx.exacta_probabilities(p1, p2, p3)
    assert np.isfinite(pe).all()
    assert abs(pe.sum() - 1.0) < 1e-6
    assert pe.max() < 0.9


# ---------------------------------------------------------------- 選別条件

@pytest.mark.parametrize(
    "jcd,female,rates,expected",
    [
        (1, 0, [1.0, 2.0, 3.0, 9.9, 0.0], True),
        (1, 0, [1.0, 2.0, 3.0, 10.1, 0.0], False),   # 10% 超え
        (1, 1, [1.0, 2.0, 3.0, 4.0, 0.0], False),    # 女性あり
        (24, 0, [1.0, 2.0, 3.0, 4.0, 0.0], False),   # 大村は除外 8 場
        (1, 0, [1.0, None, 3.0, 4.0, 0.0], False),   # 判定できない艇がある
        (None, 0, [1.0, 2.0, 3.0, 4.0, 0.0], False),
    ],
)
def test_passes_selection(jcd, female, rates, expected):
    ok, detail = fx.passes_selection(jcd, female, rates)
    assert ok is expected
    assert set(detail) == {"jcd", "female_present", "entry_change_max"}


# ---------------------------------------------------------------- 記録 → 確定 → 集計

def _race(rid, jcd=1, female=0, rates=(1, 2, 3, 4, 5), strong=0.7):
    rest = (1 - strong) / 5
    return {
        "race_id": rid, "race_date": rid[:4] + "-" + rid[4:6] + "-" + rid[6:8],
        "jcd": jcd, "female_present": female, "entry_change_rates": list(rates),
        "prob_first": [strong] + [rest] * 5,
        "prob_top_2": [min(0.95, strong + 0.15)] + [0.35, 0.3, 0.25, 0.2, 0.1],
        "prob_top_3": [0.98] + [0.6, 0.55, 0.45, 0.3, 0.2],
    }


def test_build_save_settle_and_summarize(tmp_path):
    races = [
        _race("20260919-01-01"),                        # 選別あり
        _race("20260919-24-02", jcd=24),                # 大村 → 選別なし
        _race("20260919-01-03", rates=(1, 2, 30, 4, 5)),  # 進入リスク高 → 選別なし
    ]
    now = datetime(2026, 9, 19, 1, 0, tzinfo=JST)
    picks = fx.build_picks(races, now=now)
    assert [p["selected"] for p in picks] == [1, 0, 0]
    assert all(p["combination"] == "1-2" for p in picks)
    d = json.loads(picks[0]["detail"])
    assert d["entry_change_max"] == 5 and d["top3"][0][0] == "1-2"

    conn = sqlite3.connect(tmp_path / "t.db")
    assert fx.save_picks(conn, picks) == 3
    # 同じレースをもう一度入れても前夜の判断は残る
    again = fx.build_picks([_race("20260919-01-01", strong=0.2)], now=now)
    fx.save_picks(conn, again)
    comb = conn.execute("SELECT combination FROM forward_exacta_picks WHERE race_id='20260919-01-01'").fetchone()[0]
    assert comb == "1-2"

    payouts = {("20260919-01-01", "1-2"): 450, ("20260919-24-02", "3-1"): 2300}  # 3 本目は未確定
    t5 = {("20260919-01-01", "1-2"): 4.3}
    n = fx.settle(conn, payouts, t5, until_date="2026-09-19", now=now)
    assert n == 2
    rows = conn.execute(
        "SELECT race_id, settled, hit, payout, t5_odds FROM forward_exacta_picks ORDER BY race_id"
    ).fetchall()
    assert rows == [
        ("20260919-01-01", 1, 1, 450, 4.3),
        ("20260919-01-03", 0, None, None, None),
        ("20260919-24-02", 1, 0, 0, None),
    ]
    summary = {s["selected"]: s for s in fx.summarize(conn)}
    assert summary[1]["races"] == 1 and summary[1]["hit_rate"] == 100.0 and summary[1]["roi"] == 450.0
    assert summary[0]["races"] == 1 and summary[0]["roi"] == 0.0


# ---------------------------------------------------------------- 取得 (collector)

def test_collect_one_race_exacta_writes_30_rows(tmp_path, monkeypatch):
    from src.collectors import odds as odds_collector

    db = tmp_path / "odds.db"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE race_results (race_id TEXT, boat_number INTEGER)")
    monkeypatch.setattr(odds_collector.config, "ensure_dirs", lambda: None)
    html = FIXTURE.read_text(encoding="utf-8")
    r = odds_collector.collect_one_race_exacta("20260917-04-01", "T-5min", db_path=str(db), html=html)
    assert r["odds_inserted"] == 30 and "error" not in r
    with sqlite3.connect(db) as c:
        rows = c.execute(
            "SELECT combination, odds, is_final, snapshot_label FROM odds_exacta ORDER BY combination"
        ).fetchall()
    assert len(rows) == 30
    assert ("1-2", 6.4, 0, "T-5min") in rows

    r2 = odds_collector.collect_one_race_exacta("20260917-04-01", "T-5min", db_path=str(db), html="<html></html>")
    assert r2["odds_inserted"] == 0 and r2["error"].startswith("exacta parsed 0/30")


# ---------------------------------------------------------------- scheduler

def test_scheduler_exacta_toggle_and_candidates(tmp_path, monkeypatch):
    from scripts import odds_scheduler as sch

    monkeypatch.delenv("BOATRACE_ODDS_EXACTA", raising=False)
    assert sch.exacta_enabled() is True
    monkeypatch.setenv("BOATRACE_ODDS_EXACTA", "0")
    assert sch.exacta_enabled() is False
    monkeypatch.delenv("BOATRACE_ODDS_EXACTA", raising=False)

    conn = sqlite3.connect(tmp_path / "c.db")
    # 表が無い → 空
    assert sch._forward_exacta_pick_race_ids(conn, ["2026-09-19"]) == set()
    fx.ensure_table(conn)
    conn.execute(
        "INSERT INTO forward_exacta_picks (race_id, race_date, strategy, combination, selected, created_at) "
        "VALUES ('20260919-01-01','2026-09-19','s','1-2',1,'x'), ('20260920-01-01','2026-09-20','s','1-2',1,'x')"
    )
    assert sch._forward_exacta_pick_race_ids(conn, ["2026-09-19"]) == {"20260919-01-01"}


def test_run_one_pass_collects_exacta_after_t5_success(monkeypatch):
    from scripts import odds_scheduler as sch

    calls = []
    monkeypatch.setattr(sch, "find_due_snapshots", lambda now, lookahead_min=30: [("20260919-01-01", "T-5min"), ("20260919-01-02", "T-1d")])
    monkeypatch.setattr(sch, "collect_one_race", lambda rid, snapshot_label: {"race_id": rid, "snapshot_label": snapshot_label, "odds_inserted": 120})
    monkeypatch.setattr(sch, "collect_one_race_exacta", lambda rid, snapshot_label: calls.append((rid, snapshot_label)) or {"race_id": rid, "snapshot_label": snapshot_label, "odds_inserted": 30})
    monkeypatch.setattr(sch, "collect_one_race_trio", lambda rid, snapshot_label: {"race_id": rid, "snapshot_label": snapshot_label, "odds_inserted": 20})
    monkeypatch.setattr(sch, "_auto_paper_trade", lambda rid, verbose=False: 0)
    recorded = []
    monkeypatch.setattr(sch.odds_fetch_status, "record", lambda rows: recorded.extend(rows) or len(rows))
    monkeypatch.delenv("BOATRACE_ODDS_EXACTA", raising=False)

    summary = sch.run_one_pass(verbose=False)
    assert calls == [("20260919-01-01", "T-5min")]           # T-1d では取らない
    assert summary["n_exacta"] == 1
    labels = [(r[0], r[1], r[2]) for r in recorded]
    assert ("20260919-01-01", "T-5min/exacta", "ok") in labels

    calls.clear(); recorded.clear()
    monkeypatch.setenv("BOATRACE_ODDS_EXACTA", "0")
    summary = sch.run_one_pass(verbose=False)
    assert calls == [] and "n_exacta" not in summary
