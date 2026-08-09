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
