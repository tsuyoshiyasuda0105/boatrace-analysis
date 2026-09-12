"""「4まくり」「3スロー」気づきタグの回帰テスト。

買い目の印ではなく荒れ注意の目安。前日までに分かる情報のみを使う:
  - 4号艇: race_results.kimarite ('まくり') の course_number=4 1着率
    (race_date < 当該レース日)
  - 3号艇: race_entries.avg_start_timing (事前平均ST)

しきい値は「強め」で確定済み:
  - 4号艇まくり率 >= 11.5%
  - 3号艇 avg_start_timing >= 0.191
"""
from __future__ import annotations

from pathlib import Path

from src.web import app as web_app


ROOT = Path(__file__).resolve().parents[1]
RACE_ID = "20260911-01-01"
RACE_DATE = "2026-09-11"


# ---------------------------------------------------------------------------
# Pure threshold tests (_makuri_watch_tag_payload / _slow_start_tag_payload)
# ---------------------------------------------------------------------------


def test_makuri_watch_tag_fires_at_exact_threshold():
    # 115/1000 = 11.5% ちょうど。
    tag = web_app._makuri_watch_tag_payload({"starts": 1000, "wins": 115})
    assert tag == {
        "label": "4まくり注意",
        "rate": 11.5,
        "wins": 115,
        "starts": 1000,
    }


def test_makuri_watch_tag_is_hidden_just_below_threshold():
    # 114/1000 = 11.4% (しきい値未満)。
    assert web_app._makuri_watch_tag_payload({"starts": 1000, "wins": 114}) is None


def test_makuri_watch_tag_requires_min_starts_even_with_high_rate():
    # 6/9 = 66.7% だが starts が最低本数 (10) 未満。
    assert (
        web_app._makuri_watch_tag_payload(
            {"starts": web_app.MAKURI_WATCH_MIN_STARTS - 1, "wins": 6}
        )
        is None
    )


def test_makuri_watch_tag_requires_min_wins_even_with_high_rate():
    # 2/10 = 20% だが最低勝ち数 (3) 未満。サンプルが薄い偶然を弾く。
    assert (
        web_app._makuri_watch_tag_payload(
            {"starts": 10, "wins": web_app.MAKURI_WATCH_MIN_WINS - 1}
        )
        is None
    )


def test_makuri_watch_tag_is_none_for_missing_stats():
    assert web_app._makuri_watch_tag_payload(None) is None
    assert web_app._makuri_watch_tag_payload({}) is None


def test_slow_start_tag_fires_at_exact_threshold():
    tag = web_app._slow_start_tag_payload(0.191)
    assert tag == {"label": "3スロー注意", "avg_start_timing": 0.191}


def test_slow_start_tag_is_hidden_just_below_threshold():
    assert web_app._slow_start_tag_payload(0.1909) is None


def test_slow_start_tag_is_none_for_missing_value():
    assert web_app._slow_start_tag_payload(None) is None


# ---------------------------------------------------------------------------
# Snapshot build tests (_build_race_detail_tag_snapshot)
# ---------------------------------------------------------------------------


def _base_snapshot_mocks(monkeypatch, *, entries, makuri_stats=None, entry_change=None):
    info = {
        "race_id": RACE_ID,
        "race_date": RACE_DATE,
        "stadium_number": 1,
    }
    monkeypatch.setattr(web_app, "_race_basic_info", lambda _rid: info)
    monkeypatch.setattr(web_app, "_accident_watch_map", lambda *_args: {})
    monkeypatch.setattr(web_app, "_ace_motor_threshold", lambda *_args: None)
    monkeypatch.setattr(web_app, "_load_course_role_snapshot_stats", lambda *_args: {})
    monkeypatch.setattr(web_app, "_boat1_monthly_escape_profile", lambda *_args: None)
    monkeypatch.setattr(
        web_app, "_load_entry_change_snapshot_stats", lambda *_args, **_kwargs: entry_change or {}
    )
    monkeypatch.setattr(
        web_app, "_boat4_makuri_rate_for_race", lambda *_args: makuri_stats
    )

    class _NullConn:
        def execute(self, *_args, **_kwargs):
            return self

        def fetchall(self):
            return entries

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(web_app, "db_connect", lambda: _NullConn())


def test_makuri_watch_tag_is_attached_to_boat4_in_snapshot(monkeypatch):
    entries = [
        (1, 1001, None, None),
        (2, 1002, None, None),
        (3, 1003, None, 0.15),
        (4, 1004, None, None),
    ]
    _base_snapshot_mocks(
        monkeypatch,
        entries=entries,
        makuri_stats={"starts": 40, "wins": 6},  # 15.0%
    )

    snapshot = web_app._build_race_detail_tag_snapshot(RACE_ID)

    assert snapshot["boats"]["4"]["makuri_watch_tag"] == {
        "label": "4まくり注意",
        "rate": 15.0,
        "wins": 6,
        "starts": 40,
    }
    # 他コースには付かない。
    assert "makuri_watch_tag" not in snapshot["boats"]["1"]
    assert "makuri_watch_tag" not in snapshot["boats"]["3"]


def test_makuri_watch_tag_is_absent_when_rate_below_threshold(monkeypatch):
    entries = [(4, 1004, None, None)]
    _base_snapshot_mocks(
        monkeypatch,
        entries=entries,
        makuri_stats={"starts": 40, "wins": 4},  # 10.0% < 11.5%
    )

    snapshot = web_app._build_race_detail_tag_snapshot(RACE_ID)

    assert "makuri_watch_tag" not in snapshot["boats"]["4"]


def test_slow_start_tag_is_attached_to_boat3_in_snapshot(monkeypatch):
    entries = [
        (1, 1001, None, 0.14),
        (2, 1002, None, 0.16),
        (3, 1003, None, 0.20),
        (4, 1004, None, 0.15),
    ]
    _base_snapshot_mocks(monkeypatch, entries=entries, makuri_stats=None)

    snapshot = web_app._build_race_detail_tag_snapshot(RACE_ID)

    assert snapshot["boats"]["3"]["slow_start_tag"] == {
        "label": "3スロー注意",
        "avg_start_timing": 0.2,
    }
    # 他コースには付かない。
    assert "slow_start_tag" not in snapshot["boats"]["1"]
    assert "slow_start_tag" not in snapshot["boats"]["4"]


def test_slow_start_tag_is_absent_when_below_threshold(monkeypatch):
    entries = [(3, 1003, None, 0.18)]
    _base_snapshot_mocks(monkeypatch, entries=entries, makuri_stats=None)

    snapshot = web_app._build_race_detail_tag_snapshot(RACE_ID)

    assert "slow_start_tag" not in snapshot["boats"]["3"]


def test_makuri_and_slow_start_can_both_fire_in_the_same_race(monkeypatch):
    """4号艇まくり + 3号艇スローが同時に立つケース (荒れの強シグナル)。"""
    entries = [
        (1, 1001, None, None),
        (2, 1002, None, None),
        (3, 1003, None, 0.25),
        (4, 1004, None, None),
    ]
    _base_snapshot_mocks(
        monkeypatch,
        entries=entries,
        makuri_stats={"starts": 50, "wins": 8},  # 16.0%
    )

    snapshot = web_app._build_race_detail_tag_snapshot(RACE_ID)

    assert snapshot["boats"]["4"]["makuri_watch_tag"]["rate"] == 16.0
    assert snapshot["boats"]["3"]["slow_start_tag"]["avg_start_timing"] == 0.25


def test_existing_tags_survive_alongside_the_new_watch_tags(monkeypatch):
    """新タグ追加で既存タグ (進入注意) が壊れないこと。"""
    entry_change_stats = {
        1002: {
            "starts": 120,
            "change_count": 30,
            "change_rate": 0.25,
            "inner_count": 20,
            "inner_rate": 0.167,
            "outer_count": 10,
            "outer_rate": 0.083,
            "level": "high",
        }
    }
    entries = [
        (1, 1001, None, None),
        (2, 1002, None, None),
        (3, 1003, None, 0.30),
        (4, 1004, None, None),
    ]
    _base_snapshot_mocks(
        monkeypatch,
        entries=entries,
        makuri_stats={"starts": 40, "wins": 10},  # 25.0%
        entry_change=entry_change_stats,
    )

    snapshot = web_app._build_race_detail_tag_snapshot(RACE_ID)

    assert snapshot["boats"]["2"]["entry_change_tag"]["label"] == "進入注意"
    assert snapshot["boats"]["4"]["makuri_watch_tag"]["rate"] == 25.0
    assert snapshot["boats"]["3"]["slow_start_tag"]["avg_start_timing"] == 0.3


# ---------------------------------------------------------------------------
# Race-grid badge construction (_hydrate_market_race_badges)
# ---------------------------------------------------------------------------


class _RowsConnection:
    """test_nigashi_tag_ui.py と同じ最小の偽 conn (rows を execute 無視で返す)。"""

    def __init__(self, rows):
        self.rows = rows

    def execute(self, *_args, **_kwargs):
        return self

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _hydrate_with_detail_tags(monkeypatch, boats_payload):
    # accident-badge の元クエリが返す行。少なくとも 1 行あれば by_race[RACE_ID]
    # が作られ、detail-tag スナップショットの読み出し対象になる。
    rows = [(RACE_ID, None, 1, 1001, 1, None, None, None, None)]
    conn = _RowsConnection(rows)
    monkeypatch.setattr(web_app, "db_connect", lambda: conn)
    monkeypatch.setattr(web_app, "_accident_period_start_for_date", lambda _date: "2026-05-01")
    monkeypatch.setattr(web_app, "_preferred_accident_source", lambda *_args: ("reconstructed", None))
    monkeypatch.setattr(web_app, "_load_course_role_snapshot_stats", lambda *_args: {})
    monkeypatch.setattr(web_app, "_load_entry_change_snapshot_stats", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(web_app, "_load_legacy_escape_by_race", lambda _date: {})
    monkeypatch.setattr(web_app, "_ace_motor_thresholds", lambda *_args: {})
    monkeypatch.setattr(
        web_app,
        "_read_json_caches_stale",
        lambda keys: {keys[0]: {"boats": boats_payload}} if keys else {},
    )
    payload = web_app._hydrate_market_race_badges(
        {"date": RACE_DATE, "signals": {}, "race_badges": {}},
        RACE_DATE,
    )
    return payload.get("race_badges", {}).get(RACE_ID, {})


def test_makuri_watch_badge_is_built_from_detail_tag_snapshot(monkeypatch):
    badges = _hydrate_with_detail_tags(
        monkeypatch,
        {
            "4": {
                "makuri_watch_tag": {
                    "label": "4まくり注意",
                    "rate": 15.0,
                    "wins": 6,
                    "starts": 40,
                }
            }
        },
    )

    assert badges["makuri_watch"]["boats"] == [4]
    assert badges["makuri_watch"]["max_rate"] == 15.0
    assert badges["makuri_watch"]["label"] == "4号:4まくり注意 15.0%"


def test_slow_start_badge_is_built_from_detail_tag_snapshot(monkeypatch):
    badges = _hydrate_with_detail_tags(
        monkeypatch,
        {
            "3": {
                "slow_start_tag": {
                    "label": "3スロー注意",
                    "avg_start_timing": 0.2,
                }
            }
        },
    )

    assert badges["slow_start"]["boats"] == [3]
    assert badges["slow_start"]["label"] == "3号:3スロー注意 0.20"


def test_no_watch_badges_when_no_tags_present(monkeypatch):
    badges = _hydrate_with_detail_tags(monkeypatch, {})
    assert "makuri_watch" not in badges
    assert "slow_start" not in badges


def test_existing_badge_families_survive_alongside_new_watch_badges(monkeypatch):
    badges = _hydrate_with_detail_tags(
        monkeypatch,
        {
            "1": {"escape_tag": {"label": "逃げ", "rate": 75.0, "wins": 30, "starts": 40}},
            "2": {"nigashi_tag": {"label": "壁", "rate": 68.0, "wins": 34, "starts": 50}},
            "4": {
                "makuri_watch_tag": {
                    "label": "4まくり注意",
                    "rate": 12.0,
                    "wins": 6,
                    "starts": 50,
                }
            },
        },
    )

    assert badges["escape"]["items"][0]["rate"] == 75.0
    assert badges["nigashi"]["items"][0]["rate"] == 68.0
    assert badges["makuri_watch"]["max_rate"] == 12.0


# ---------------------------------------------------------------------------
# Wiring / guardrails
# ---------------------------------------------------------------------------


def test_new_watch_badges_are_guest_safe():
    assert "makuri_watch" in web_app._GUEST_SAFE_BADGE_KEYS
    assert "slow_start" in web_app._GUEST_SAFE_BADGE_KEYS


def test_thresholds_are_the_agreed_strong_values():
    assert web_app.MAKURI_WATCH_RATE_MIN == 11.5
    assert web_app.SLOW_START_AVG_ST_MIN == 0.191


def test_race_detail_tag_cache_version_bumped_for_new_watch_tags():
    assert web_app.RACE_DETAIL_TAG_CACHE_VERSION == "v8"


def test_race_badge_schema_version_bumped_for_new_watch_badges():
    assert web_app.RACE_BADGE_SCHEMA_VERSION == "v3"


def test_watch_tag_labels_are_neutral_not_a_buy_signal():
    """買い目の印と誤読させないため、ラベルに「注意」を含める。"""
    tag = web_app._makuri_watch_tag_payload({"starts": 100, "wins": 20})
    assert "注意" in tag["label"]
    assert "買い" not in tag["label"]

    tag2 = web_app._slow_start_tag_payload(0.25)
    assert "注意" in tag2["label"]
    assert "買い" not in tag2["label"]


def test_race_html_renders_watch_tags_with_neutral_wording():
    template = (ROOT / "src" / "web" / "templates" / "race.html").read_text(encoding="utf-8")
    assert "p.makuri_watch_tag is defined" in template
    assert "p.slow_start_tag is defined" in template
    assert "買い目の推奨ではありません" in template


def test_index_html_wires_up_both_new_badges_in_both_render_paths():
    template = (ROOT / "src" / "web" / "templates" / "index.html").read_text(encoding="utf-8")
    assert template.count("makuri-watch-badge") >= 2
    assert template.count("slow-start-watch-badge") >= 2
    assert "badges?.makuri_watch" in template
    assert "badges?.slow_start" in template


def test_style_css_defines_neutral_colors_for_watch_badges():
    css = (ROOT / "src" / "web" / "static" / "style.css").read_text(encoding="utf-8")
    assert ".makuri-watch-badge" in css
    assert ".slow-start-watch-badge" in css
    assert ".racer-watch-tag" in css


# ---------------------------------------------------------------------------
# バッチ集計 (_boat4_makuri_rates_by_race) — 実 DB。
# 2026-09-12: IN (100件超の placeholders) 版が本番 Postgres で statement
# timeout になり握りつぶして {} を返し、prewarm が「4まくり」を 1 件も
# 作れなかった。race_date で束ねる形に直した。ここでは集計の正しさと、
# 対象 race_id だけ返すこと・当日以降を数えないことを固定する。
# ---------------------------------------------------------------------------


def _makuri_batch_db():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE races (race_id TEXT PRIMARY KEY, race_date TEXT);
        CREATE TABLE race_entries (race_id TEXT, boat_number INT, racer_number INT);
        CREATE TABLE race_results (
            race_id TEXT, boat_number INT, finishing_position INT,
            course_number INT, kimarite TEXT
        );
        """
    )
    # 当日 (2026-09-11): a と b は 4 号艇に別選手。c は対象外の同日レース。
    conn.executemany(
        "INSERT INTO races VALUES (?, ?)",
        [("a", "2026-09-11"), ("b", "2026-09-11"), ("c", "2026-09-11")],
    )
    conn.executemany(
        "INSERT INTO race_entries VALUES (?, ?, ?)",
        [("a", 4, 100), ("b", 4, 200), ("c", 4, 300)],
    )
    # 選手 100 の履歴: 4 コース 4 走・まくり 2 勝 (逃げは数えない)。
    hist = [
        ("h1", "2026-09-01", 100), ("h2", "2026-09-02", 100),
        ("h3", "2026-09-03", 100), ("h4", "2026-09-04", 100),
        # 当日 (2026-09-11) の履歴は数えてはいけない (リーク防止)。
        ("h5", "2026-09-11", 100),
        # 選手 200: 4 コース 2 走・まくり 1 勝。
        ("h6", "2026-09-05", 200), ("h7", "2026-09-06", 200),
        # 選手 300: 4 コース 1 走 (対象外なので出力に出ない)。
        ("h8", "2026-09-07", 300),
    ]
    conn.executemany("INSERT INTO races VALUES (?, ?)", [(r, d) for r, d, _ in hist])
    conn.executemany(
        "INSERT INTO race_entries VALUES (?, 4, ?)", [(r, n) for r, _, n in hist]
    )
    conn.executemany(
        "INSERT INTO race_results VALUES (?, 4, ?, 4, ?)",
        [
            ("h1", 1, "まくり"), ("h2", 1, "まくり"),
            ("h3", 1, "逃げ"), ("h4", 2, "まくり"),
            ("h5", 1, "まくり"),  # 当日 — 数えない
            ("h6", 1, "まくり"), ("h7", 3, "差し"),
            ("h8", 1, "まくり"),
        ],
    )
    conn.commit()
    return conn


def test_batch_aggregates_by_date_and_filters_to_wanted_races():
    conn = _makuri_batch_db()
    try:
        got = web_app._boat4_makuri_rates_by_race(["a", "b"], "2026-09-11", conn)
    finally:
        conn.close()
    # a: 選手100 は当日を除くと 4 走 2 まくり勝ち。b: 選手200 は 2 走 1 勝。
    assert got == {"a": {"starts": 4, "wins": 2}, "b": {"starts": 2, "wins": 1}}
    # c は wanted に無いので出力されない。
    assert "c" not in got


def test_batch_returns_empty_without_raising_on_broken_db():
    class _Broken:
        def execute(self, *_a, **_k):
            raise RuntimeError("boom")

    assert web_app._boat4_makuri_rates_by_race(["a"], "2026-09-11", _Broken()) == {}


def test_batch_no_longer_builds_an_in_clause_over_race_ids():
    """再発防止: race_id を IN (...) で束ねない (それが timeout の原因だった)。"""
    import inspect

    src = inspect.getsource(web_app._boat4_makuri_rates_by_race)
    assert "IN ({placeholders})" not in src
    assert "race_date = ?" in src
