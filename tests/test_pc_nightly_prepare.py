from scripts import pc_nightly_prepare as nightly


def test_pc_nightly_prepare_uses_supported_cli_arguments(monkeypatch):
    calls = []

    monkeypatch.setattr(nightly, "_run_local", lambda args, allow_prod_sync=False: calls.append((args, allow_prod_sync)) or True)

    monkeypatch.setattr(
        nightly,
        "parse_args",
        lambda: type(
            "Args",
            (),
            {
                "date": "2026-08-10",
                "sync_start": None,
                "sync_end": None,
                "skip_sync": True,
            },
        )(),
    )

    assert nightly.main() == 0
    step_args = [args for args, _allow_prod in calls]

    assert ["scripts/build_racer_entry_change_stats.py", "--date", "2026-08-10"] in step_args
    assert all("--db-path" not in args for args in step_args if args[:1] == ["scripts/build_racer_entry_change_stats.py"])
    assert ["scripts/build_top_page_snapshot.py", "--date", "2026-08-10"] in step_args
    assert ["scripts/cache_predictions.py", "--date", "2026-08-10"] in step_args
    assert not any(args[:1] == ["scripts/render_cache_predictions.py"] for args in step_args)


def test_pc_nightly_prepare_syncs_selected_tables(monkeypatch):
    calls = []

    monkeypatch.setattr(nightly, "_run_local", lambda args, allow_prod_sync=False: calls.append((args, allow_prod_sync)) or True)
    monkeypatch.setattr(
        nightly,
        "parse_args",
        lambda: type(
            "Args",
            (),
            {
                "date": "2026-08-10",
                "sync_start": "2026-08-09",
                "sync_end": "2026-08-10",
                "skip_sync": False,
            },
        )(),
    )

    assert nightly.main() == 0
    sync_args, allow_prod = next(
        call for call in calls if call[0][:1] == ["scripts/sync_to_supabase.py"]
    )

    assert allow_prod is True
    assert sync_args[:2] == ["scripts/sync_to_supabase.py", "--start"]
    table_csv = sync_args[sync_args.index("--tables") + 1]
    assert "race_tides" in table_csv
    assert "derived_start_stats" in table_csv


def test_default_target_date_prepares_current_day_after_midnight():
    from datetime import datetime

    # 定時 01:00 実行: 始まったばかりの「その日」を準備する
    assert nightly._default_target_date(datetime(2026, 8, 14, 1, 0)) == "2026-08-14"
    # 早朝リトライも同様
    assert nightly._default_target_date(datetime(2026, 8, 14, 6, 30)) == "2026-08-14"
    # 夕方の手動実行: 翌日分を準備する (番組表公開後)
    assert nightly._default_target_date(datetime(2026, 8, 13, 19, 56)) == "2026-08-14"
    # 正午が切り替え境界
    assert nightly._default_target_date(datetime(2026, 8, 14, 11, 59)) == "2026-08-14"
    assert nightly._default_target_date(datetime(2026, 8, 14, 12, 0)) == "2026-08-15"


# ===== 履歴の取りこぼしを毎晩さかのぼって埋める =====
#
# 夜間は「昨日」ぶんしか進めないので、01:00 の時点で K 成績ファイルが未公開
# だとその日の履歴は誰も取りに戻らない。2026-07-22〜09-07 に 30 日ぶん欠け、
# 平均ST・決まり手率・事故率が薄い履歴で計算されていた (2026-09-10 に発覚)。

import sqlite3

import pytest


def _dbs(tmp_path, raced: list[str], have_history: list[str]):
    source = tmp_path / "boatrace.db"
    search = tmp_path / "search.db"
    conn = sqlite3.connect(source)
    try:
        conn.execute("CREATE TABLE races (race_id TEXT PRIMARY KEY, race_date TEXT)")
        conn.executemany(
            "INSERT INTO races VALUES (?, ?)",
            [(f"{day}-{i}", day) for i, day in enumerate(raced)],
        )
        conn.commit()
    finally:
        conn.close()
    conn = sqlite3.connect(search)
    try:
        conn.execute(
            "CREATE TABLE start_timing_events "
            "(race_id TEXT, race_date TEXT, racer_number INTEGER)"
        )
        conn.executemany(
            "INSERT INTO start_timing_events VALUES (?, ?, 1)",
            [(f"{day}-{i}", day) for i, day in enumerate(have_history)],
        )
        conn.commit()
    finally:
        conn.close()
    return source, search


def _gaps(tmp_path, raced, have_history, completed="2026-09-10", **kw):
    source, search = _dbs(tmp_path, raced, have_history)
    return nightly.history_gap_days(
        completed, source_db=source, search_db=search, **kw
    )


def test_nothing_to_do_when_every_day_has_history(tmp_path):
    days = ["2026-09-07", "2026-09-08", "2026-09-09"]
    assert _gaps(tmp_path, days, days) == []


def test_a_day_whose_history_never_arrived_is_found(tmp_path):
    assert _gaps(
        tmp_path,
        ["2026-09-07", "2026-09-08", "2026-09-09"],
        ["2026-09-07", "2026-09-09"],
    ) == ["2026-09-08"]


def test_gaps_come_back_oldest_first(tmp_path):
    """365日窓の集計が歯抜けにならないよう、古い日から入れる。"""
    assert _gaps(
        tmp_path,
        ["2026-09-05", "2026-09-06", "2026-09-07", "2026-09-08"],
        ["2026-09-07"],
    ) == ["2026-09-05", "2026-09-06", "2026-09-08"]


def test_a_long_gap_is_filled_a_few_days_at_a_time(tmp_path):
    """一晩で全部やると朝の取り込みに食い込む。残りは翌晩に持ち越す。"""
    raced = [f"2026-09-{day:02d}" for day in range(1, 10)]
    found = _gaps(tmp_path, raced, [], limit=3)
    assert found == ["2026-09-01", "2026-09-02", "2026-09-03"]


def test_a_day_with_no_racing_is_not_a_gap(tmp_path):
    """休催日には履歴が無くて当たり前。埋めにいかない。"""
    assert _gaps(tmp_path, ["2026-09-08"], ["2026-09-08"]) == []


def test_the_completed_day_itself_is_left_to_the_normal_path(tmp_path):
    """完成日は通常手順が進める。二重に走らせない。"""
    assert "2026-09-10" not in _gaps(tmp_path, ["2026-09-10"], [])


def test_days_older_than_the_lookback_are_left_alone(tmp_path):
    """古すぎる穴は毎晩掘り返さない。必要なら手作業で埋める。"""
    assert _gaps(tmp_path, ["2026-08-01", "2026-09-08"], [], lookback=7) == ["2026-09-08"]


@pytest.mark.parametrize("missing", ["source", "search"])
def test_a_missing_database_does_not_crash_the_nightly(tmp_path, missing):
    source, search = _dbs(tmp_path, ["2026-09-08"], [])
    (source if missing == "source" else search).unlink()
    assert nightly.history_gap_days(
        "2026-09-10", source_db=source, search_db=search
    ) == ([] if missing == "source" else ["2026-09-08"])


def test_the_catch_up_runs_before_the_completed_day(monkeypatch):
    """穴埋めが先。完成日を先に計算すると、歯抜けの履歴で率が出る。"""
    monkeypatch.setattr(nightly, "history_gap_days", lambda completed, **kw: ["2026-09-08"])
    monkeypatch.setattr(nightly, "_refresh_and_upload", lambda *a, **k: True)
    seen: list[str] = []

    def fake_run(args, allow_prod_sync=False):
        if args[:1] == ["scripts/restore_start_timing.py"]:
            seen.append(args[args.index("--from") + 1])
        return True

    monkeypatch.setattr(nightly, "_run_local", fake_run)
    nightly._run_kachisuji_daily("2026-09-09")

    assert seen == ["2026-09-08", "2026-09-09"]


def test_a_failed_catch_up_does_not_stop_the_night(monkeypatch):
    """1日埋まらなくても、完成日と当日の準備は続ける。"""
    monkeypatch.setattr(nightly, "history_gap_days", lambda completed, **kw: ["2026-09-08"])
    refreshed: list[str] = []
    monkeypatch.setattr(
        nightly, "_refresh_and_upload", lambda day, *a, **k: refreshed.append(day) or True
    )
    monkeypatch.setattr(nightly, "_run_local", lambda args, allow_prod_sync=False: False)

    assert nightly._run_kachisuji_daily("2026-09-09", "2026-09-10") is True
    assert refreshed == ["2026-09-09", "2026-09-10"]


def test_a_search_database_without_the_history_table_does_not_crash(tmp_path):
    """復元前の DB でも夜間を落とさない。全部が穴として素直に埋まる。"""
    source, search = _dbs(tmp_path, ["2026-09-08"], [])
    conn = sqlite3.connect(search)
    try:
        conn.execute("DROP TABLE start_timing_events")
        conn.commit()
    finally:
        conn.close()

    assert nightly.history_gap_days(
        "2026-09-10", source_db=source, search_db=search
    ) == ["2026-09-08"]


def test_the_racer_name_sync_runs_once_not_once_per_gap_day(monkeypatch):
    """選手名の同期は日付に関係ない。穴の日数ぶん繰り返さない。"""
    monkeypatch.setattr(
        nightly, "history_gap_days", lambda completed, **kw: ["2026-09-06", "2026-09-07"]
    )
    monkeypatch.setattr(nightly, "_refresh_and_upload", lambda *a, **k: True)
    runs: list[list[str]] = []
    monkeypatch.setattr(
        nightly, "_run_local", lambda args, allow_prod_sync=False: runs.append(args) or True
    )

    nightly._run_kachisuji_daily("2026-09-09")

    sync = [args for args in runs if args[:1] == ["scripts/sync_kachisuji_racers.py"]]
    assert len(sync) == 1
