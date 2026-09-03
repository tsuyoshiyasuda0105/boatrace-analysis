import json
from datetime import datetime
from pathlib import Path

from scripts import prewarm_race_detail_tags as tag_prewarm
from src.web import app as web_app


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "prewarm_race_detail_tags.py"
SCHEDULER = ROOT / "scripts" / "render_regular_scheduler.py"
PROGRAM_BOOTSTRAP = ROOT / "scripts" / "render_program_bootstrap_scheduler.py"
MAINTENANCE = ROOT / "scripts" / "render_maintenance_scheduler.py"


def test_daily_tag_prewarm_has_maintenance_owner_and_bounded_daytime_selfheal():
    source = SCHEDULER.read_text(encoding="utf-8")

    bootstrap = source.split("def run_lite_daytime_bootstrap", 1)[1].split("def tide_refresh_needed", 1)[0]
    maintenance = MAINTENANCE.read_text(encoding="utf-8")
    assert "def run_detail_pages_selfheal" in bootstrap
    assert '"scripts/prewarm_race_detail_tags.py"' in bootstrap
    assert '"--budget-sec", str(DETAIL_SELFHEAL_TAG_BUDGET_SEC)' in bootstrap
    assert '"render_detail_pages_selfheal"' in bootstrap
    assert '"--budget-sec", str(DETAIL_TAG_BUDGET_SEC)' in maintenance
    assert "def run_nightly(" not in source
    assert '"--missing-only"' in maintenance
    assert maintenance.index('"scripts/prewarm_race_detail_tags.py"') < maintenance.index('"scripts/prewarm_race_detail_pages.py"')
    assert "_at_or_after(now, 6, 30)" in PROGRAM_BOOTSTRAP.read_text(encoding="utf-8")


def test_entry_change_snapshot_records_task_runs():
    source = SCHEDULER.read_text(encoding="utf-8")
    block = source.split("def run_entry_change_snapshot", 1)[1].split("def task_success_exists", 1)[0]

    assert 'record_task("render_entry_change_snapshot", target_date, "success", detail="skip:no-races")' in block
    assert '"render_entry_change_snapshot"' in block
    assert '"success" if verified else "failure"' in block


def test_tag_prewarm_covers_every_race_and_forces_snapshot_refresh():
    source = SCRIPT.read_text(encoding="utf-8")

    assert "WHERE race_date = ?" in source
    assert "_race_detail_tag_snapshot(str(race_id), recompute=True)" in source


class _RaceRowsConnection:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, *_args, **_kwargs):
        return self

    def fetchall(self):
        return [("race-1",), ("race-2",), ("race-3",)]

    def close(self):
        return None


def test_tag_prewarm_budget_keeps_progress_and_next_run_resumes(monkeypatch):
    cached = set()
    clock = {"value": 0.0}

    monkeypatch.setattr(tag_prewarm, "db_connect", _RaceRowsConnection)
    monkeypatch.setattr(
        tag_prewarm,
        "_missing_cached_race_ids",
        lambda race_ids, **_kwargs: [race_id for race_id in race_ids if race_id not in cached],
    )
    monkeypatch.setattr(
        tag_prewarm,
        "_prefetch_race_detail_tag_inputs",
        lambda _race_ids, _conn: {},
    )
    monkeypatch.setattr(tag_prewarm.time, "perf_counter", lambda: clock["value"])

    def build(race_id, *, recompute):
        assert recompute is True
        cached.add(race_id)
        clock["value"] += 2.0
        return {"boats": {"1": {}}}

    monkeypatch.setattr(tag_prewarm, "_race_detail_tag_snapshot", build)

    first = tag_prewarm.prewarm("2026-08-16", budget_sec=1)
    assert first["cached"] == 1
    assert first["remaining"] == 2
    assert first["budget_exhausted"] is True
    assert cached == {"race-1"}

    second = tag_prewarm.prewarm("2026-08-16")
    assert second["skipped_existing"] == 1
    assert second["cached"] == 2
    assert second["remaining"] == 0
    assert second["budget_exhausted"] is False
    assert cached == {"race-1", "race-2", "race-3"}


def test_tag_prewarm_main_treats_budget_remaining_as_success(monkeypatch):
    monkeypatch.setattr(
        tag_prewarm,
        "prewarm",
        lambda *_args, **_kwargs: {"races": 3, "failed": 0, "remaining": 2},
    )
    monkeypatch.setattr(tag_prewarm.sys, "argv", ["prewarm", "--budget-sec", "1"])

    assert tag_prewarm.main() == 0


def test_prefetched_tag_build_is_byte_identical_to_individual_build(monkeypatch):
    race_id = "20260816-01-01"
    info = {
        "race_id": race_id,
        "race_date": "2026-08-16",
        "stadium_number": 1,
    }
    entries = [(1, 1001, 45.5), (2, 1002, None)]
    accidents = {
        1001: {
            "rate": 0.75,
            "points": 3,
            "starts": 4,
            "flying_count": 1,
            "late_count": 0,
        }
    }
    escape = {
        "starts": 10,
        "wins": 7,
        "rate": 70.0,
        "snapshot_month": "2026-08",
        "preferred_course": 1,
    }
    entry_change = {
        1002: {
            "starts": 120,
            "change_count": 25,
            "change_rate": 0.21,
            "inner_count": 15,
            "inner_rate": 0.125,
            "outer_count": 10,
            "outer_rate": 0.083,
            "level": "high",
        }
    }

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 16, 6, 30, 0, tzinfo=tz)

    class EntryConnection:
        def __init__(self):
            self.execute_count = 0

        def execute(self, *_args, **_kwargs):
            self.execute_count += 1
            return self

        def fetchall(self):
            return entries

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    legacy_conn = EntryConnection()
    monkeypatch.setattr(web_app, "datetime", FixedDateTime)
    monkeypatch.setattr(web_app, "_race_basic_info", lambda _rid: info)
    monkeypatch.setattr(web_app, "db_connect", lambda: legacy_conn)
    monkeypatch.setattr(web_app, "_accident_watch_map", lambda *_args: accidents)
    monkeypatch.setattr(web_app, "_ace_motor_threshold", lambda *_args: 40.0)
    monkeypatch.setattr(web_app, "_boat1_monthly_escape_profile", lambda *_args: escape)
    monkeypatch.setattr(web_app, "_load_entry_change_snapshot_stats", lambda *_args: entry_change)

    legacy = web_app._build_race_detail_tag_snapshot(race_id)
    optimized_conn = EntryConnection()
    prefetched = {
        "race_info": {race_id: info},
        "tag_entries": {race_id: entries},
        "accident_by_racer": accidents,
        "ace_thresholds": {(1, "2026-08-16"): 40.0},
        "escape_by_race": {race_id: escape},
        "entry_change_by_racer": entry_change,
    }
    with web_app._use_race_detail_prewarm_context(optimized_conn, prefetched):
        optimized = web_app._build_race_detail_tag_snapshot(race_id)

    encode = lambda payload: json.dumps(  # noqa: E731
        payload, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    assert encode(optimized) == encode(legacy)
    # プリウォーム文脈が無い経路は、選手コース別スナップショットの
    # まとめ読みが 1 回増えた (2026-09-01)。レースごとの N+1 ではなく、
    # その日の出走選手ぶんを 1 クエリで引く形なので 1 -> 2 で正しい。
    assert legacy_conn.execute_count == 2
    # ここは絶対に緩めない。プリウォーム経路がレースごとに DB を引き始めると
    # 接続枠の取り合いで画面が止まる (過去の「準備しています」障害の原因)。
    assert optimized_conn.execute_count == 0


def test_tag_prewarm_uses_one_connection_for_selection_prefetch_and_loop(monkeypatch):
    conn = _RaceRowsConnection()
    opens = []
    monkeypatch.setattr(tag_prewarm, "db_connect", lambda: opens.append(conn) or conn)
    monkeypatch.setattr(tag_prewarm, "_missing_cached_race_ids", lambda ids, **_kwargs: ids)
    monkeypatch.setattr(tag_prewarm, "_prefetch_race_detail_tag_inputs", lambda _ids, got: {"seen": got})
    monkeypatch.setattr(
        tag_prewarm,
        "_race_detail_tag_snapshot",
        lambda *_args, **_kwargs: {"boats": {"1": {}}},
    )

    summary = tag_prewarm.prewarm("2026-08-16")

    assert summary["cached"] == 3
    assert opens == [conn]


def test_escape_tag_uses_monthly_frozen_boat1_profile():
    source = (ROOT / "src" / "web" / "app.py").read_text(encoding="utf-8")

    # boats[n] に nigashi_tag が増えたので版数を上げた (2026-09-01)。
    # 上げ忘れると既存キャッシュが優先され、新タグが画面に出ない。
    assert 'RACE_DETAIL_TAG_CACHE_VERSION = "v7"' in source
    assert "def _boat1_monthly_escape_profile" in source
    assert "def _monthly_snapshot_window" in source
    assert "WHERE race_id = ? AND boat_number = 1" in source
    assert "COALESCE(NULLIF(rr1.course_number, 0), e1.boat_number) = 1" in source
    # 閾値は定数 1 箇所に集約した。直書きに戻すと逃げ/逃がしで食い違うため、
    # 定数が存在すること + 70.0 の直書きが定数定義以外に無いことを固定する。
    assert "ESCAPE_WIN_RATE_MIN = 0.70" in source
    assert "ESCAPE_WIN_RATE_MIN" in source.split("ESCAPE_WIN_RATE_MIN = 0.70", 1)[1], (
        "定数が定義されただけで使われていない"
    )
    assert "escape_rate >= 70.0" not in source, "閾値を直書きに戻してはいけない"
    assert '"snapshot_month": str(boat1_escape.get("snapshot_month") or "")' in source
    assert "escape_context_tag" in source
    assert "preferred_course" in source
    assert "entry_change_tag" in source
