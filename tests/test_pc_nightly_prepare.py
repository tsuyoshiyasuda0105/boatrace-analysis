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
    sync_args, allow_prod = calls[-1]

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
