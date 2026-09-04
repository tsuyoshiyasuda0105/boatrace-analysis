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
    # 率は必ず丸めた実数から作り直す。率と分母分子がずれた stats を渡すと
    # 「70.0% (18/25)」のように画面上で矛盾した表示になり、そのずれた値を
    # 期待値に焼き付けてしまう (2026-09-04 まで実際にそうなっていた)。
    escape_wins = round(escape_rate * escape_starts)
    nigashi_count = round(nigashi_rate * nigashi_starts)
    return {
        1001: {
            "course1_starts": escape_starts,
            "course1_wins": escape_wins,
            "course1_win_rate": escape_wins / escape_starts if escape_starts else None,
            "course2_starts": 0,
            "course2_nigashi_count": 0,
            "course2_nigashi_rate": None,
        },
        1002: {
            "course1_starts": 0,
            "course1_wins": 0,
            "course1_win_rate": None,
            "course2_starts": nigashi_starts,
            "course2_nigashi_count": nigashi_count,
            "course2_nigashi_rate": nigashi_count / nigashi_starts if nigashi_starts else None,
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

    # 18/25 = 72.0%。旧実装は保存された率 (0.70) をそのまま出していたため
    # 「70.0% (18/25)」と、率と分数が食い違う表示になっていた。
    assert badges["nigashi"] == {
        "items": [{"boat": 2, "label": "壁", "rate": 72.0, "wins": 18, "starts": 25}],
        "boats": [2],
        "max_rate": 72.0,
        "label": "2号:壁 72.0% (18/25)",
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

    # round(0.75 * 25) = 19 → 19/25 = 76.0%。率は分母分子と必ず一致する。
    assert badges["escape"]["items"][0]["rate"] == 76.0
    assert badges["escape"]["items"][0]["wins"] == 19
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


def test_boundary_rates_survive_float4_rounding():
    """ちょうど閾値の選手が、保存された率の丸めでバッジを失わないこと。

    本番の率カラムは real (4バイト) で、42/60 や 26/40 の「ちょうど 0.70 /
    0.65」が 0.6999999... として返る場合がある。判定を保存値ではなく整数の
    分母分子から出しているので、丸めた率が来ても該当し続ける。
    2026-09-04 に本番投入後の検算で発見 (逃げ 112→110 / 壁 71→69)。
    """
    escape = web_app._course_role_escape_tag(
        {
            "course1_starts": 60,
            "course1_wins": 42,
            "course1_win_rate": 0.699999988079071,
        }
    )
    assert escape is not None
    assert escape["rate"] == 70.0

    nigashi = web_app._course_role_nigashi_tag(
        {
            "course2_starts": 40,
            "course2_nigashi_count": 26,
            "course2_nigashi_rate": 0.6499999761581421,
        }
    )
    assert nigashi is not None
    assert nigashi["rate"] == 65.0


def test_rates_just_below_threshold_still_get_no_badge():
    """整数から出すようにしても、閾値未満は従来どおり落ちること。"""
    assert (
        web_app._course_role_escape_tag(
            {"course1_starts": 60, "course1_wins": 41, "course1_win_rate": 0.9}
        )
        is None
    )
    assert (
        web_app._course_role_nigashi_tag(
            {"course2_starts": 40, "course2_nigashi_count": 25, "course2_nigashi_rate": 0.9}
        )
        is None
    )


def test_course_role_rate_ignores_stored_value_when_counts_exist():
    """保存値が壊れていても分母分子が正なら実数で判定すること。"""
    assert web_app._course_role_rate(42, 60, 0.0) == 42 / 60
    # 分母が 0 のときだけ保存値に落ちる (率カラムしか無い経路の保険)。
    assert web_app._course_role_rate(0, 0, 0.71) == 0.71
    assert web_app._course_role_rate(0, 0, None) is None


def test_snapshot_table_stores_rates_as_double_precision():
    """REAL だと Postgres で 4 バイトになり SQL 側の集計が別の答えを出す。"""
    source = (ROOT / "scripts" / "build_racer_course_role_stats.py").read_text(encoding="utf-8")
    assert "course1_win_rate      DOUBLE PRECISION" in source
    assert "course2_nigashi_rate  DOUBLE PRECISION" in source
    assert "_rate      REAL" not in source
    assert "_rate  REAL" not in source


def test_fallback_badge_payload_keeps_the_schema_version():
    """市場シグナルのキャッシュが無い日でも版数を落とさないこと。

    _race_grid_badges_payload はバッジを返す分岐が 2 つある。キャッシュ経路は
    race_badges_schema を引き継いでいたが、フォールバック経路だけ落としていた。
    cron 停止明けのようにキャッシュが無い日はフォールバックを通るため、その日の
    スナップショットだけ版数 None になり、次の評価で「バッジを空にして作り直す」
    分岐に入る (バッジが消える経路)。2026-09-05 に本番で None を確認。
    """
    source = (ROOT / "src" / "web" / "app.py").read_text(encoding="utf-8")
    fn = source[source.index("def _race_grid_badges_payload") : source.index("def _lightweight_top_page_market_payload")]

    # バッジ入りで返る分岐の数だけ、版数の引き継ぎがあること。
    returns_with_badges = fn.count('"race_badges": filtered,')
    schema_propagations = fn.count('"race_badges_schema":')
    assert returns_with_badges >= 2, "バッジを返す分岐が想定より少ない"
    assert schema_propagations == returns_with_badges, (
        "バッジを返す分岐と版数を引き継ぐ箇所の数が一致しない"
    )
