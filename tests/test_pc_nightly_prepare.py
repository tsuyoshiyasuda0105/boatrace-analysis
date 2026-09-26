from pathlib import Path
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


# ===== 着順に決まり手が入っていない日も穴として拾う =====
#
# 2025-07〜2026-04 は進入データはあるのに、着順の決まり手と備考 (事故) が
# 10か月まるごと空だった。1号艇の逃げ率が 52% → 9% まで落ちていた。
# 進入データの有無だけを見ていると見つからない。


def _add_results(source, rows):
    """(race_id, kimarite) を着順の表に入れる。"""
    conn = sqlite3.connect(source)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS race_results "
            "(race_id TEXT, boat_number INTEGER, kimarite TEXT)"
        )
        conn.executemany(
            "INSERT INTO race_results VALUES (?, 1, ?)", rows
        )
        conn.commit()
    finally:
        conn.close()


def test_a_day_with_start_timings_but_no_kimarite_is_a_gap(tmp_path):
    """実際に起きた形。進入はあるのに決まり手が無い = K が入っていない。"""
    source, search = _dbs(tmp_path, ["2026-09-08", "2026-09-09"], ["2026-09-08", "2026-09-09"])
    _add_results(source, [("20260908-01-01", "逃げ"), ("20260909-01-01", None)])
    assert nightly.history_gap_days(
        "2026-09-10", source_db=source, search_db=search
    ) == ["2026-09-09"]


def test_a_day_whose_results_never_arrived_is_a_gap(tmp_path):
    source, search = _dbs(tmp_path, ["2026-09-08", "2026-09-09"], ["2026-09-08", "2026-09-09"])
    _add_results(source, [("20260908-01-01", "逃げ")])
    assert nightly.history_gap_days(
        "2026-09-10", source_db=source, search_db=search
    ) == ["2026-09-09"]


def test_an_empty_string_kimarite_counts_as_missing(tmp_path):
    source, search = _dbs(tmp_path, ["2026-09-09"], ["2026-09-09"])
    _add_results(source, [("20260909-01-01", "")])
    assert nightly.history_gap_days(
        "2026-09-10", source_db=source, search_db=search
    ) == ["2026-09-09"]


def test_both_kinds_of_gap_are_merged_without_duplicates(tmp_path):
    source, search = _dbs(
        tmp_path, ["2026-09-07", "2026-09-08", "2026-09-09"], ["2026-09-08", "2026-09-09"]
    )
    _add_results(source, [("20260907-01-01", None), ("20260908-01-01", "差し"),
                          ("20260909-01-01", None)])
    assert nightly.history_gap_days(
        "2026-09-10", source_db=source, search_db=search
    ) == ["2026-09-07", "2026-09-09"]


def test_a_source_without_the_results_table_only_uses_the_timing_check(tmp_path):
    """着順の表が無ければ判断できない。そのぶんは穴扱いにしない。"""
    source, search = _dbs(tmp_path, ["2026-09-08", "2026-09-09"], ["2026-09-08"])
    assert nightly.history_gap_days(
        "2026-09-10", source_db=source, search_db=search
    ) == ["2026-09-09"]


def test_pc_nightly_records_forward_exacta_after_kachisuji(monkeypatch):
    """夜間の最後に二連単の前向き記録 (確定+当日候補) を本番向けに走らせる。"""
    calls = []
    monkeypatch.setattr(
        nightly, "_run_local",
        lambda args, allow_prod_sync=False: calls.append((args, allow_prod_sync)) or True,
    )
    monkeypatch.setattr(nightly, "_run_kachisuji_daily", lambda *a, **k: True)
    monkeypatch.setattr("sys.argv", ["pc_nightly_prepare.py", "--date", "2026-09-19", "--skip-sync"])

    assert nightly.main() == 0

    fx = [(args, allow) for args, allow in calls if args[:1] == ["scripts/forward_exacta_picks.py"]]
    # 対象日に加えて、止まった晩の穴を埋めるため直近数日もなぞる (2026-09-20)
    assert fx[-1] == (
        ["scripts/forward_exacta_picks.py", "--settle", "--date", "2026-09-19"], True
    )
    assert [args[3] for args, _ in fx] == nightly._recent_days(
        "2026-09-19", nightly.FORWARD_CATCHUP_DAYS
    )
    # 本番へ書くので allow_prod_sync=True で呼ぶこと (ローカル固定パスから読む)
    assert all(allow for _, allow in fx)


# ---- 固まった夜の手当て (2026-09-15/19/20) ---------------------------------


def test_steps_run_through_the_watchdog_runner(monkeypatch, capsys):
    """子は -u で、見張り役ごしに走らせる (固まった場所がログに残る)。"""
    seen = {}

    class _Proc:
        pid = 4242

        def __init__(self, cmd, **kwargs):
            seen["cmd"] = cmd
            seen["env"] = kwargs.get("env")

        def wait(self, timeout=None):
            seen["timeout"] = timeout
            return 0

    monkeypatch.setattr(nightly.subprocess, "Popen", _Proc)
    assert nightly._run_local(["scripts/daily_collect.py", "--date", "2026-09-20"]) is True
    # 子の起動が済んだことがログに残る (起動で固まったのかを切り分けるため)
    assert "[step] child pid=4242 started" in capsys.readouterr().out

    assert seen["cmd"][1] == "-u"
    assert seen["cmd"][2] == "scripts/run_step_watchdog.py"
    assert seen["cmd"][3] == str(nightly.STEP_WATCHDOG_SECONDS)
    assert seen["cmd"][4:] == ["scripts/daily_collect.py", "--date", "2026-09-20"]
    assert seen["env"]["PYTHONUNBUFFERED"] == "1"
    # 見張り役は親に切られる前に鳴らすこと
    assert nightly.STEP_WATCHDOG_SECONDS < seen["timeout"] == nightly.STEP_TIMEOUT_SECONDS


def test_the_watchdog_runner_exists_and_dumps_where_it_hangs():
    source = (Path("scripts") / "run_step_watchdog.py").read_text(encoding="utf-8")
    assert "faulthandler.dump_traceback_later" in source
    assert "exit=True" in source


def test_a_failing_official_download_does_not_stop_the_rest_of_the_night(monkeypatch):
    """公式DLが固まっても、予測キャッシュと前向き記録まで進む。

    2026-09-15/19/20 は初手で止まり、その晩の下流が丸ごと作られなかった。
    """
    calls = []

    def fake_run(args, allow_prod_sync=False):
        calls.append(args[0])
        return args[0] != "scripts/backfill_official.py"

    monkeypatch.setattr(nightly, "_run_local", fake_run)
    monkeypatch.setattr(nightly, "_run_kachisuji_daily", lambda *a, **k: True)
    monkeypatch.setattr("sys.argv", ["pc_nightly_prepare.py", "--date", "2026-09-20", "--skip-sync"])

    assert nightly.main() == 0
    assert "scripts/cache_predictions.py" in calls
    assert "scripts/forward_exacta_picks.py" in calls


def test_a_failing_required_step_still_stops_the_night(monkeypatch):
    calls = []

    def fake_run(args, allow_prod_sync=False):
        calls.append(args[0])
        return args[0] != "scripts/daily_collect.py"

    monkeypatch.setattr(nightly, "_run_local", fake_run)
    monkeypatch.setattr(nightly, "_run_kachisuji_daily", lambda *a, **k: True)
    monkeypatch.setattr("sys.argv", ["pc_nightly_prepare.py", "--date", "2026-09-20", "--skip-sync"])

    assert nightly.main() == 1
    assert "scripts/cache_predictions.py" not in calls


def test_forward_exacta_fills_the_days_the_night_missed(monkeypatch):
    """止まった晩の穴が、次の晩に自分で埋まる (記録は INSERT OR IGNORE)。"""
    calls = []
    monkeypatch.setattr(
        nightly, "_run_local",
        lambda args, allow_prod_sync=False: calls.append((args, allow_prod_sync)) or True,
    )
    monkeypatch.setattr(nightly, "_run_kachisuji_daily", lambda *a, **k: True)
    monkeypatch.setattr("sys.argv", ["pc_nightly_prepare.py", "--date", "2026-09-21", "--skip-sync"])

    assert nightly.main() == 0

    days = [args[3] for args, _ in calls if args[0] == "scripts/forward_exacta_picks.py"]
    assert days == ["2026-09-19", "2026-09-20", "2026-09-21"]
    assert all(allow for args, allow in calls if args[0] == "scripts/forward_exacta_picks.py")


def test_recent_days_is_oldest_first_and_includes_the_target():
    assert nightly._recent_days("2026-09-21", 3) == ["2026-09-19", "2026-09-20", "2026-09-21"]
    assert nightly._recent_days("2026-03-01", 2) == ["2026-02-28", "2026-03-01"]


def test_a_step_that_never_returns_is_killed_and_reported(monkeypatch, capsys):
    killed = []

    class _Proc:
        pid = 7

        def __init__(self, cmd, **kwargs):
            pass

        def wait(self, timeout=None):
            if not killed:
                raise nightly.subprocess.TimeoutExpired("cmd", timeout)
            return -9

        def kill(self):
            killed.append(True)

    monkeypatch.setattr(nightly.subprocess, "Popen", _Proc)
    assert nightly._run_local(["scripts/backfill_official.py"]) is False
    assert killed == [True]
    assert "timeout=1800s (step killed): scripts/backfill_official.py" in capsys.readouterr().out


def test_heartbeat_prints_until_stopped(capsys):
    """親が生きている限り、一定間隔で時刻を残す (2026-09-23/24 の切り分け用)。"""
    import time

    stop = nightly._start_heartbeat(interval=0.05)
    time.sleep(0.18)
    stop.set()
    time.sleep(0.08)
    beats = capsys.readouterr().out.count("[heartbeat]")
    assert beats >= 2
    time.sleep(0.12)
    assert capsys.readouterr().out.count("[heartbeat]") == 0

