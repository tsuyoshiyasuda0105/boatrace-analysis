"""「4まくり」「3スロー」気づきタグの回帰テスト。

買い目の印ではなく荒れ注意の目安。前日までに分かる情報のみを使う:
  - 4号艇: コース役割スナップショット (朝に事前計算・前日までの直近3年) の
    course4_starts / course4_makuri_wins
    (= course_number=4 の出走数 / そのうち1着かつ kimarite='まくり')
  - 3号艇: race_entries.avg_start_timing (事前平均ST)

しきい値は「強め」で確定済み:
  - 4号艇まくり率 >= 11.5%
  - 3号艇 avg_start_timing >= 0.191

2026-09-13: 4まくりはタグ生成時にその場で全期間を数える方式 (本番で約8秒・
statement timeout ぎりぎり) をやめ、スナップショットを読むだけにした。
"""
from __future__ import annotations

import inspect
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
# スナップショットからの判定 (_course_role_makuri_tag)
# ---------------------------------------------------------------------------


def _course4(starts: int, wins: int) -> dict:
    return {"course4_starts": starts, "course4_makuri_wins": wins}


def test_course_role_makuri_tag_uses_the_same_thresholds():
    assert web_app._course_role_makuri_tag(_course4(1000, 115)) == {
        "label": "4まくり注意",
        "rate": 11.5,
        "wins": 115,
        "starts": 1000,
    }
    assert web_app._course_role_makuri_tag(_course4(1000, 114)) is None
    assert web_app._course_role_makuri_tag(_course4(9, 6)) is None
    assert web_app._course_role_makuri_tag(_course4(10, 2)) is None


def test_course_role_makuri_tag_is_none_without_course4_columns():
    # 列追加前の本番表から読んだ行 (4コース列なし) には付けない。
    assert web_app._course_role_makuri_tag(None) is None
    assert web_app._course_role_makuri_tag({}) is None
    assert web_app._course_role_makuri_tag({"course1_starts": 50, "course1_wins": 40}) is None


class _LoaderConn:
    def __init__(self, rows):
        self.rows = rows
        self.sql = []

    def execute(self, sql, *_args, **_kwargs):
        self.sql.append(sql)
        return self

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_loader_reads_course4_columns(monkeypatch):
    conn = _LoaderConn([(1004, 0, 0, None, 0, 0, None, 0, None, 40, 6)])
    monkeypatch.setattr(web_app, "db_connect", lambda: conn)

    got = web_app._load_course_role_snapshot_stats(RACE_DATE, [1004])

    assert "course4_starts" in conn.sql[0]
    assert "course4_makuri_wins" in conn.sql[0]
    assert got[1004]["course4_starts"] == 40
    assert got[1004]["course4_makuri_wins"] == 6
    assert web_app._course_role_makuri_tag(got[1004])["rate"] == 15.0


# ---------------------------------------------------------------------------
# Snapshot build tests (_build_race_detail_tag_snapshot)
# ---------------------------------------------------------------------------


def _base_snapshot_mocks(monkeypatch, *, entries, makuri_stats=None, entry_change=None):
    info = {
        "race_id": RACE_ID,
        "race_date": RACE_DATE,
        "stadium_number": 1,
    }
    course_roles = (
        {1004: _course4(makuri_stats["starts"], makuri_stats["wins"])}
        if makuri_stats
        else {}
    )
    monkeypatch.setattr(web_app, "_race_basic_info", lambda _rid: info)
    monkeypatch.setattr(web_app, "_accident_watch_map", lambda *_args: {})
    monkeypatch.setattr(web_app, "_ace_motor_threshold", lambda *_args: None)
    monkeypatch.setattr(web_app, "_load_course_role_snapshot_stats", lambda *_args: course_roles)
    monkeypatch.setattr(web_app, "_boat1_monthly_escape_profile", lambda *_args: None)
    monkeypatch.setattr(
        web_app, "_load_entry_change_snapshot_stats", lambda *_args, **_kwargs: entry_change or {}
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


def test_makuri_watch_tag_goes_only_to_boat4_even_if_other_racers_qualify(monkeypatch):
    entries = [(2, 1002, None, None), (4, 1004, None, None)]
    _base_snapshot_mocks(monkeypatch, entries=entries, makuri_stats={"starts": 40, "wins": 4})
    # 2号艇の選手も4コースまくり率が高いが、判定するのは4号艇だけ。
    roles = {1002: _course4(40, 20), 1004: _course4(40, 4)}
    monkeypatch.setattr(web_app, "_load_course_role_snapshot_stats", lambda *_args: roles)

    snapshot = web_app._build_race_detail_tag_snapshot(RACE_ID)

    assert "makuri_watch_tag" not in snapshot["boats"]["2"]
    assert "makuri_watch_tag" not in snapshot["boats"]["4"]


def test_makuri_watch_tag_is_absent_when_rate_below_threshold(monkeypatch):
    entries = [(4, 1004, None, None)]
    _base_snapshot_mocks(
        monkeypatch,
        entries=entries,
        makuri_stats={"starts": 40, "wins": 4},  # 10.0% < 11.5%
    )

    snapshot = web_app._build_race_detail_tag_snapshot(RACE_ID)

    assert "makuri_watch_tag" not in snapshot["boats"]["4"]


def test_makuri_watch_tag_is_absent_when_snapshot_missing(monkeypatch):
    entries = [(4, 1004, None, None)]
    _base_snapshot_mocks(monkeypatch, entries=entries, makuri_stats=None)

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


def test_no_live_makuri_query_remains():
    """退行防止: タグ生成時にその場で4コース成績を数える経路を戻さない。"""
    assert not hasattr(web_app, "_boat4_makuri_rate_for_race")
    assert not hasattr(web_app, "_boat4_makuri_rates_by_race")
    source = inspect.getsource(web_app._build_race_detail_tag_snapshot)
    assert "_course_role_makuri_tag" in source
    assert "makuri_by_race" not in source
    prefetch = inspect.getsource(web_app._prefetch_race_detail_tag_inputs)
    assert "makuri" not in prefetch


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


def _hydrate_with_detail_tags(monkeypatch, boats_payload, *, course_roles=None, boat_rows=None):
    # accident-badge の元クエリが返す行。少なくとも 1 行あれば by_race[RACE_ID]
    # が作られ、detail-tag スナップショットの読み出し対象になる。
    rows = boat_rows or [(RACE_ID, None, 1, 1001, 1, None, None, None, None)]
    conn = _RowsConnection(rows)
    monkeypatch.setattr(web_app, "db_connect", lambda: conn)
    monkeypatch.setattr(web_app, "_accident_period_start_for_date", lambda _date: "2026-05-01")
    monkeypatch.setattr(web_app, "_preferred_accident_source", lambda *_args: ("reconstructed", None))
    monkeypatch.setattr(web_app, "_load_course_role_snapshot_stats", lambda *_args: course_roles or {})
    monkeypatch.setattr(web_app, "_load_entry_change_snapshot_stats", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(web_app, "_load_legacy_escape_by_race", lambda _date: {})
    monkeypatch.setattr(web_app, "_ace_motor_thresholds", lambda *_args: {})
    monkeypatch.setattr(
        web_app,
        "_read_json_caches_stale",
        lambda keys: {keys[0]: {"boats": boats_payload}} if keys and boats_payload else {},
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


def test_makuri_watch_badge_is_built_directly_from_course_role_snapshot(monkeypatch):
    """詳細タグのキャッシュが無くても、一覧はスナップショットから同じバッジを作る。"""
    rows = [(RACE_ID, None, 4, 1004, 1, None, None, None, None)]
    badges = _hydrate_with_detail_tags(
        monkeypatch,
        {},
        course_roles={1004: _course4(40, 6)},
        boat_rows=rows,
    )

    assert badges["makuri_watch"] == {
        "items": [{"boat": 4, "label": "4まくり注意", "rate": 15.0, "wins": 6, "starts": 40}],
        "boats": [4],
        "max_rate": 15.0,
        "label": "4号:4まくり注意 15.0%",
    }


def test_makuri_watch_badge_not_given_to_non_boat4_racer(monkeypatch):
    rows = [(RACE_ID, None, 2, 1002, 1, None, None, None, None)]
    badges = _hydrate_with_detail_tags(
        monkeypatch,
        {},
        course_roles={1002: _course4(40, 20)},
        boat_rows=rows,
    )
    assert "makuri_watch" not in badges


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
    # v9: 2026-09-13 に「差され注意」タグ追加でさらに 1 つ進んだ。
    # 4まくりの事前計算化 (同日) はタグの中身の形を変えないので据え置き。
    assert web_app.RACE_DETAIL_TAG_CACHE_VERSION == "v9"


def test_race_badge_schema_version_bumped_for_new_watch_badges():
    # v4: 2026-09-13 に「差され注意」バッジ追加でさらに 1 つ進んだ。
    assert web_app.RACE_BADGE_SCHEMA_VERSION == "v4"


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


def test_snapshot_builder_keeps_the_makuri_definition():
    source = (ROOT / "scripts" / "build_racer_course_role_stats.py").read_text(encoding="utf-8")
    assert "rr.course_number = 4" in source
    assert "rr.kimarite = 'まくり'" in source
    assert "MAKURI_WINDOW_DAYS = 1095" in source
