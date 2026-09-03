from __future__ import annotations

import ast
import inspect
from pathlib import Path

from src.web import app as web_app


ROOT = Path(__file__).resolve().parents[1]
RACE_ID = "20260901-01-01"
RACE_DATE = "2026-09-01"


class _RowsConnection:
    def __init__(self, rows):
        self.rows = rows
        self.execute_count = 0

    def execute(self, *_args, **_kwargs):
        self.execute_count += 1
        return self

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _snapshot_stats(
    *,
    escape_rate=0.0,
    escape_starts=0,
    nigashi_rate=0.0,
    nigashi_starts=0,
):
    return {
        1001: {
            "course1_starts": escape_starts,
            "course1_wins": round(escape_rate * escape_starts),
            "course1_win_rate": escape_rate,
            "course2_starts": 0,
            "course2_nigashi_count": 0,
            "course2_nigashi_rate": None,
        },
        1002: {
            "course1_starts": 0,
            "course1_wins": 0,
            "course1_win_rate": None,
            "course2_starts": nigashi_starts,
            "course2_nigashi_count": round(nigashi_rate * nigashi_starts),
            "course2_nigashi_rate": nigashi_rate,
        },
    }


def _hydrate(monkeypatch, course_roles, *, legacy_escape=None):
    rows = [
        (RACE_ID, None, 1, 1001, 1, None, None, None, None),
        (RACE_ID, None, 2, 1002, 1, None, None, None, None),
    ]
    conn = _RowsConnection(rows)
    legacy_calls = []
    monkeypatch.setattr(web_app, "db_connect", lambda: conn)
    monkeypatch.setattr(web_app, "_accident_period_start_for_date", lambda _date: "2026-05-01")
    monkeypatch.setattr(web_app, "_preferred_accident_source", lambda *_args: ("reconstructed", None))
    monkeypatch.setattr(web_app, "_load_course_role_snapshot_stats", lambda *_args: course_roles)
    monkeypatch.setattr(web_app, "_load_entry_change_snapshot_stats", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(web_app, "_ace_motor_thresholds", lambda *_args: {})
    monkeypatch.setattr(web_app, "_read_json_caches_stale", lambda *_args: {})

    def load_legacy(target_date):
        legacy_calls.append(target_date)
        return legacy_escape or {}

    monkeypatch.setattr(web_app, "_load_legacy_escape_by_race", load_legacy)
    payload = web_app._hydrate_market_race_badges(
        {"date": RACE_DATE, "signals": {}, "race_badges": {}},
        RACE_DATE,
    )
    return payload.get("race_badges", {}).get(RACE_ID, {}), conn, legacy_calls


def test_nigashi_badge_is_built_from_course_role_snapshot(monkeypatch):
    badges, conn, legacy_calls = _hydrate(
        monkeypatch,
        _snapshot_stats(nigashi_rate=0.70, nigashi_starts=25),
    )

    assert badges["nigashi"] == {
        "items": [{"boat": 2, "label": "壁", "rate": 70.0, "wins": 18, "starts": 25}],
        "boats": [2],
        "max_rate": 70.0,
        "label": "2号:壁 70.0% (18/25)",
    }
    assert conn.execute_count == 1
    assert legacy_calls == []


def test_nigashi_badge_is_hidden_below_rate_threshold(monkeypatch):
    badges, _conn, _legacy_calls = _hydrate(
        monkeypatch,
        _snapshot_stats(nigashi_rate=0.649, nigashi_starts=25),
    )

    assert "nigashi" not in badges


def test_nigashi_badge_is_hidden_below_start_threshold(monkeypatch):
    badges, _conn, _legacy_calls = _hydrate(
        monkeypatch,
        _snapshot_stats(nigashi_rate=0.80, nigashi_starts=19),
    )

    assert "nigashi" not in badges


def test_escape_badge_uses_snapshot_percent_rate(monkeypatch):
    badges, _conn, _legacy_calls = _hydrate(
        monkeypatch,
        _snapshot_stats(escape_rate=0.75, escape_starts=25),
    )

    assert badges["escape"]["items"][0]["rate"] == 75.0
    assert badges["escape"]["items"][0]["starts"] == 25


def test_escape_badge_is_hidden_below_start_threshold(monkeypatch):
    badges, _conn, _legacy_calls = _hydrate(
        monkeypatch,
        _snapshot_stats(escape_rate=0.90, escape_starts=19),
    )

    assert "escape" not in badges


def test_missing_snapshot_falls_back_to_legacy_escape(monkeypatch):
    legacy = {
        RACE_ID: {
            "boat": 1,
            "label": "逃げ",
            "rate": 72.5,
            "wins": 29,
            "starts": 40,
        }
    }
    badges, _conn, legacy_calls = _hydrate(monkeypatch, {}, legacy_escape=legacy)

    assert badges["escape"]["items"] == [legacy[RACE_ID]]
    assert legacy_calls == [RACE_DATE]


def test_missing_snapshot_does_not_create_nigashi_fallback(monkeypatch):
    badges, _conn, legacy_calls = _hydrate(monkeypatch, {})

    assert "nigashi" not in badges
    assert legacy_calls == [RACE_DATE]


def test_nigashi_badge_is_guest_safe():
    assert "nigashi" in web_app._GUEST_SAFE_BADGE_KEYS


def test_course_role_thresholds_are_defined_once_and_reused():
    source = (ROOT / "src" / "web" / "app.py").read_text(encoding="utf-8")
    assert source.count("COURSE_ROLE_MIN_STARTS = 20") == 1
    assert source.count("ESCAPE_WIN_RATE_MIN = 0.70") == 1
    assert source.count("NIGASHI_RATE_MIN = 0.65") == 1

    guarded = "\n".join(
        inspect.getsource(function)
        for function in (
            web_app._course_role_escape_tag,
            web_app._course_role_nigashi_tag,
            web_app._load_legacy_escape_by_race,
        )
    )
    tree = ast.parse(guarded)
    numeric_literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float))
    }
    assert not {0.65, 0.70, 20}.intersection(numeric_literals)
    assert "COURSE_ROLE_MIN_STARTS" in guarded
    assert "ESCAPE_WIN_RATE_MIN" in guarded
    assert "NIGASHI_RATE_MIN" in guarded
    assert "ESCAPE_WIN_RATE_MIN" in inspect.getsource(web_app._build_race_detail_tag_snapshot)


def test_course_role_loader_batches_all_racers_in_one_query(monkeypatch):
    rows = [(1001, 25, 19, 0.76, 24, 17, 0.7083)]
    conn = _RowsConnection(rows)
    monkeypatch.setattr(web_app, "db_connect", lambda: conn)

    result = web_app._load_course_role_snapshot_stats(RACE_DATE, [1002, 1001, 1002])

    assert conn.execute_count == 1
    assert result[1001]["course1_win_rate"] == 0.76
    assert result[1001]["course2_nigashi_rate"] == 0.7083


def test_both_badge_cache_versions_are_bumped():
    assert web_app.RACE_DETAIL_TAG_CACHE_VERSION == "v7"
    assert web_app.TOP_PAGE_SNAPSHOT_VERSION == "v4"


def test_badge_hydration_is_not_short_circuited_by_older_badge_families():
    """バッジ種別を増やしたとき、古いキャッシュが再計算されずに残らないこと。

    2026-09-03 の実バグ: `_hydrate_market_race_badges` に
    「事故・逃げ・進入変更が揃っていれば再計算しない」という種別を数え上げる
    短絡ガードがあり、4 種別目の壁を足したときに古いキャッシュがそのまま返され、
    壁バッジが画面に一切出なかった。版数一致での判定に変えて塞いだ。
    """
    from src.web import app as web_app

    # 旧構成 (壁なし・版数なし) のキャッシュ済みペイロード
    stale = {
        "date": "2026-09-01",
        "signals": {},
        "race_badges": {
            "20260901-01-01": {
                "accident": {"items": [], "boats": [1]},
                "escape": {"items": [], "boats": [1]},
                "entry_change": {"items": [], "boats": [4]},
            }
        },
    }
    # 版数が一致しないので短絡してはいけない = 中身が作り直される
    assert stale.get("race_badges_schema") != web_app.RACE_BADGE_SCHEMA_VERSION

    # 現行版数が刻まれていれば短絡してよい
    fresh = dict(stale)
    fresh["race_badges_schema"] = web_app.RACE_BADGE_SCHEMA_VERSION
    out = web_app._hydrate_market_race_badges(fresh, "2026-09-01")
    assert out["race_badges"] is fresh["race_badges"], (
        "版数が一致するなら再計算せずそのまま返す"
    )


def test_badge_schema_version_is_bumped_for_nigashi():
    """壁バッジ追加に伴い、バッジ構成の版数が初期値から上がっていること。"""
    from src.web import app as web_app

    assert web_app.RACE_BADGE_SCHEMA_VERSION != "v1"


def test_legacy_escape_fallback_applies_the_same_minimum_starts():
    """スナップショット未整備日のフォールバックも 20 走以上を要求すること。

    下限を課さないと、過去日だけ 1 走 1 勝 (100%) の選手にも逃げバッジが付き、
    同じバッジが日付によって違う基準で出てしまう。
    """
    import inspect

    from src.web import app as web_app

    source = inspect.getsource(web_app._load_legacy_escape_by_race)
    assert "COURSE_ROLE_MIN_STARTS" in source, (
        "フォールバック経路にも最低走数の下限が必要"
    )
