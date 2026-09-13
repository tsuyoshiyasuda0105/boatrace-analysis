"""「差され注意」気づきタグの回帰テスト。

買い目の印ではなく「本命(1号艇)が手堅く見えても危ない」の注意喚起。
率はコース役割スナップショット (夜間に事前計算・前日までの直近365日) から読む:
  差し負け率 = 「1コースで走って1着を逃し、そのレースの勝者の決まり手が
               『差し』だった」回数 ÷ 1コース出走数

しきい値 (2025年以降の as-of 検証で決定):
  - 差し負け率 >= 25%   (該当1号艇の1着率 35.0% / 全体 54.9%)
  - 1コース出走 >= 20   (逃げ・壁と同じ COURSE_ROLE_MIN_STARTS)

当日にレース単位で集計する方式は本番の statement timeout 8 秒に当たって
不安定だったので採らない (2026-09-13)。その退行を防ぐテストも置く。
"""
from __future__ import annotations

import inspect
from pathlib import Path

from src.web import app as web_app


ROOT = Path(__file__).resolve().parents[1]
RACE_ID = "20260911-01-01"
RACE_DATE = "2026-09-11"


def _stats(starts: int, sashi: int, **extra) -> dict:
    return {
        "course1_starts": starts,
        "course1_wins": 0,
        "course1_win_rate": None,
        "course2_starts": 0,
        "course2_nigashi_count": 0,
        "course2_nigashi_rate": None,
        "course1_sashinuke_count": sashi,
        "course1_sashinuke_rate": sashi / starts if starts else None,
        **extra,
    }


# ---------------------------------------------------------------------------
# しきい値 (_course_role_sashinuke_tag)
# ---------------------------------------------------------------------------


def test_tag_fires_at_exact_threshold():
    # 5/20 = 25.0% ちょうど、出走 20 ちょうど。
    assert web_app._course_role_sashinuke_tag(_stats(20, 5)) == {
        "label": "差され注意",
        "rate": 25.0,
        "sashi": 5,
        "starts": 20,
    }


def test_tag_is_hidden_just_below_rate_threshold():
    # 24/97 = 24.7%
    assert web_app._course_role_sashinuke_tag(_stats(97, 24)) is None


def test_tag_requires_min_starts_even_with_high_rate():
    assert web_app._course_role_sashinuke_tag(_stats(19, 10)) is None


def test_boundary_rate_survives_float4_rounding():
    # 保存値が 0.2499999 に丸められていても、整数の分母分子から判定する。
    stats = _stats(40, 10, course1_sashinuke_rate=0.24999999)
    assert web_app._course_role_sashinuke_tag(stats)["rate"] == 25.0


def test_tag_is_none_for_missing_stats_or_missing_column():
    assert web_app._course_role_sashinuke_tag(None) is None
    assert web_app._course_role_sashinuke_tag({}) is None
    # 列追加前の本番表から読んだ行 (差し負け列なし) には付けない。
    legacy = _stats(60, 30)
    legacy.pop("course1_sashinuke_count")
    legacy.pop("course1_sashinuke_rate")
    assert web_app._course_role_sashinuke_tag(legacy) is None


def test_thresholds_are_the_agreed_values():
    assert web_app.SASHINUKE_WATCH_RATE_MIN == 0.25
    assert web_app.COURSE_ROLE_MIN_STARTS == 20
    assert "SASHINUKE_WATCH_MIN_STARTS" not in vars(web_app)


def test_label_is_neutral_not_a_buy_signal():
    tag = web_app._course_role_sashinuke_tag(_stats(100, 30))
    assert "注意" in tag["label"]
    assert "買い" not in tag["label"]


# ---------------------------------------------------------------------------
# スナップショット読み出し (_load_course_role_snapshot_stats)
# ---------------------------------------------------------------------------


class _Conn:
    def __init__(self, rows=None, fail_first=False):
        self.rows = rows or []
        self.fail_first = fail_first
        self.sql = []

    def execute(self, sql, *_args, **_kwargs):
        self.sql.append(sql)
        if self.fail_first and len(self.sql) == 1:
            raise RuntimeError('column "course1_sashinuke_count" does not exist')
        return self

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_loader_reads_sashinuke_columns(monkeypatch):
    conn = _Conn([(1001, 40, 20, 0.5, 0, 0, None, 12, 0.3)])
    monkeypatch.setattr(web_app, "db_connect", lambda: conn)

    got = web_app._load_course_role_snapshot_stats(RACE_DATE, [1001])

    assert len(conn.sql) == 1
    assert "course1_sashinuke_count" in conn.sql[0]
    assert got[1001]["course1_sashinuke_count"] == 12
    assert got[1001]["course1_sashinuke_rate"] == 0.3


def test_loader_falls_back_to_old_columns_so_escape_and_wall_survive(monkeypatch):
    """列追加前の本番表でも、逃げ・壁の元データは読めること。"""
    conn = _Conn([(1001, 25, 19, 0.76, 24, 17, 0.7083)], fail_first=True)
    monkeypatch.setattr(web_app, "db_connect", lambda: conn)

    got = web_app._load_course_role_snapshot_stats(RACE_DATE, [1001])

    assert len(conn.sql) == 2
    assert "course1_sashinuke_count" not in conn.sql[1]
    assert got[1001]["course1_wins"] == 19
    assert "course1_sashinuke_count" not in got[1001]
    assert web_app._course_role_escape_tag(got[1001]) is not None
    assert web_app._course_role_sashinuke_tag(got[1001]) is None


# ---------------------------------------------------------------------------
# レース詳細タグ (_build_race_detail_tag_snapshot)
# ---------------------------------------------------------------------------


def _build_snapshot(monkeypatch, *, entries, course_roles, entry_change=None, makuri=None):
    info = {"race_id": RACE_ID, "race_date": RACE_DATE, "stadium_number": 1}
    monkeypatch.setattr(web_app, "_race_basic_info", lambda _rid: info)
    monkeypatch.setattr(web_app, "_accident_watch_map", lambda *_args: {})
    monkeypatch.setattr(web_app, "_ace_motor_threshold", lambda *_args: None)
    monkeypatch.setattr(web_app, "_load_course_role_snapshot_stats", lambda *_args: course_roles)
    monkeypatch.setattr(web_app, "_boat1_monthly_escape_profile", lambda *_args: None)
    monkeypatch.setattr(
        web_app, "_load_entry_change_snapshot_stats", lambda *_args, **_kwargs: entry_change or {}
    )
    monkeypatch.setattr(web_app, "_boat4_makuri_rate_for_race", lambda *_args: makuri)

    class _EntriesConn:
        def execute(self, *_args, **_kwargs):
            return self

        def fetchall(self):
            return entries

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(web_app, "db_connect", lambda: _EntriesConn())
    return web_app._build_race_detail_tag_snapshot(RACE_ID)


FOUR_BOATS = [
    (1, 1001, None, None),
    (2, 1002, None, None),
    (3, 1003, None, 0.25),
    (4, 1004, None, None),
]


def test_tag_is_attached_to_boat1_only(monkeypatch):
    snapshot = _build_snapshot(
        monkeypatch,
        entries=FOUR_BOATS,
        # 1002 も差し負け率が高いが 2号艇なので付けない。
        course_roles={1001: _stats(60, 15), 1002: _stats(60, 30)},
    )

    assert snapshot["boats"]["1"]["sashinuke_watch_tag"] == {
        "label": "差され注意",
        "rate": 25.0,
        "sashi": 15,
        "starts": 60,
    }
    assert "sashinuke_watch_tag" not in snapshot["boats"]["2"]


def test_tag_absent_below_threshold(monkeypatch):
    snapshot = _build_snapshot(
        monkeypatch, entries=FOUR_BOATS, course_roles={1001: _stats(60, 6)}
    )
    assert "sashinuke_watch_tag" not in snapshot["boats"]["1"]


def test_tag_coexists_with_escape_makuri_slow_start_and_entry_change(monkeypatch):
    """組み合わせ: 逃げ(1号)と差され(1号)が同居、4まくり・3スロー・進入注意も壊れない。"""
    stats = _stats(60, 15, course1_wins=45)  # 逃げ 75% かつ 差し負け 25%
    entry_change = {
        1002: {
            "starts": 120, "change_count": 30, "change_rate": 0.25,
            "inner_count": 20, "inner_rate": 0.167,
            "outer_count": 10, "outer_rate": 0.083, "level": "high",
        }
    }
    snapshot = _build_snapshot(
        monkeypatch,
        entries=FOUR_BOATS,
        course_roles={1001: stats},
        entry_change=entry_change,
        makuri={"starts": 40, "wins": 6},
    )

    boat1 = snapshot["boats"]["1"]
    assert boat1["escape_tag"]["rate"] == 75.0
    assert boat1["sashinuke_watch_tag"]["rate"] == 25.0
    assert snapshot["boats"]["4"]["makuri_watch_tag"]["rate"] == 15.0
    assert snapshot["boats"]["3"]["slow_start_tag"]["avg_start_timing"] == 0.25
    assert snapshot["boats"]["2"]["entry_change_tag"]["label"] == "進入注意"


def test_no_live_per_race_sashinuke_query_remains():
    """退行防止: 当日にレース単位で集計する経路を戻さない (timeout の原因)。"""
    assert not hasattr(web_app, "_boat1_sashinuke_rate_for_race")
    assert not hasattr(web_app, "_boat1_sashinuke_rates_by_race")
    source = inspect.getsource(web_app._build_race_detail_tag_snapshot)
    assert "_course_role_sashinuke_tag" in source
    assert "sashinuke_by_race" not in source


# ---------------------------------------------------------------------------
# レース一覧バッジ (_hydrate_market_race_badges)
# ---------------------------------------------------------------------------


class _RowsConnection:
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


def _hydrate(monkeypatch, *, course_roles=None, detail_boats=None):
    rows = [
        (RACE_ID, None, 1, 1001, 1, None, None, None, None),
        (RACE_ID, None, 2, 1002, 1, None, None, None, None),
    ]
    monkeypatch.setattr(web_app, "db_connect", lambda: _RowsConnection(rows))
    monkeypatch.setattr(web_app, "_accident_period_start_for_date", lambda _date: "2026-05-01")
    monkeypatch.setattr(web_app, "_preferred_accident_source", lambda *_args: ("reconstructed", None))
    monkeypatch.setattr(web_app, "_load_course_role_snapshot_stats", lambda *_args: course_roles or {})
    monkeypatch.setattr(web_app, "_load_entry_change_snapshot_stats", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(web_app, "_load_legacy_escape_by_race", lambda _date: {})
    monkeypatch.setattr(web_app, "_ace_motor_thresholds", lambda *_args: {})
    monkeypatch.setattr(
        web_app,
        "_read_json_caches_stale",
        lambda keys: {keys[0]: {"boats": detail_boats}} if keys and detail_boats else {},
    )
    payload = web_app._hydrate_market_race_badges(
        {"date": RACE_DATE, "signals": {}, "race_badges": {}},
        RACE_DATE,
    )
    return payload.get("race_badges", {}).get(RACE_ID, {})


def test_badge_is_built_directly_from_course_role_snapshot(monkeypatch):
    badges = _hydrate(monkeypatch, course_roles={1001: _stats(40, 12)})

    assert badges["sashinuke_watch"] == {
        "items": [{"boat": 1, "label": "差され注意", "rate": 30.0, "sashi": 12, "starts": 40}],
        "boats": [1],
        "max_rate": 30.0,
        "label": "1号:差され注意 30.0%",
    }


def test_badge_not_given_to_boat2_racer_with_high_sashinuke(monkeypatch):
    badges = _hydrate(monkeypatch, course_roles={1002: _stats(40, 20)})
    assert "sashinuke_watch" not in badges


def test_badge_is_also_built_from_detail_tag_cache(monkeypatch):
    badges = _hydrate(
        monkeypatch,
        detail_boats={
            "1": {"sashinuke_watch_tag": {"label": "差され注意", "rate": 25.0, "sashi": 15, "starts": 60}}
        },
    )
    assert badges["sashinuke_watch"]["label"] == "1号:差され注意 25.0%"


def test_escape_wall_and_sashinuke_badges_coexist(monkeypatch):
    course_roles = {
        1001: _stats(40, 10, course1_wins=30),  # 逃げ 75% + 差し負け 25%
        1002: {**_stats(0, 0), "course2_starts": 40, "course2_nigashi_count": 28},  # 壁 70%
    }
    badges = _hydrate(monkeypatch, course_roles=course_roles)

    assert badges["escape"]["items"][0]["rate"] == 75.0
    assert badges["nigashi"]["items"][0]["rate"] == 70.0
    assert badges["sashinuke_watch"]["max_rate"] == 25.0


def test_no_badge_when_snapshot_missing(monkeypatch):
    assert "sashinuke_watch" not in _hydrate(monkeypatch)


# ---------------------------------------------------------------------------
# 配線・版数・画面
# ---------------------------------------------------------------------------


def test_badge_is_guest_safe():
    assert "sashinuke_watch" in web_app._GUEST_SAFE_BADGE_KEYS


def test_cache_versions_bumped_for_sashinuke():
    assert web_app.RACE_DETAIL_TAG_CACHE_VERSION == "v9"
    assert web_app.RACE_BADGE_SCHEMA_VERSION == "v4"


def test_race_html_renders_tag_with_neutral_wording():
    template = (ROOT / "src" / "web" / "templates" / "race.html").read_text(encoding="utf-8")
    assert "p.sashinuke_watch_tag is defined" in template
    assert "差され注意" in template


def test_index_html_wires_badge_in_both_render_paths():
    template = (ROOT / "src" / "web" / "templates" / "index.html").read_text(encoding="utf-8")
    assert template.count("sashinuke-watch-badge") >= 2
    assert "badges?.sashinuke_watch" in template


def test_style_css_defines_distinct_color():
    css = (ROOT / "src" / "web" / "static" / "style.css").read_text(encoding="utf-8")
    assert ".sashinuke-watch-badge" in css
    assert ".racer-watch-tag--sashinuke" in css


def test_snapshot_builder_stores_sashinuke_rate_as_double_precision():
    source = (ROOT / "scripts" / "build_racer_course_role_stats.py").read_text(encoding="utf-8")
    assert "course1_sashinuke_rate  DOUBLE PRECISION" in source
    assert "kimarite = '差し'" in source
