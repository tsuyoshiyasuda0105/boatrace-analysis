# Kachisuji test collection fix work log (2026-08-16)

## Cause

`tests/test_kachisuji_correctness_round3.py` built its parameter date lists at
module import time. `_representative_dates()` asserted that the local
`asof_race_features` snapshot contained enough dates. On machines without that
snapshot, `assert len(dates) >= leading + trailing` failed during pytest
collection and prevented the rest of the suite from being collected.

An audit of the other `test_kachisuji*.py` files found no additional
data-dependent assertions executed at module/collection time.

## Fix

- Replaced the collection-time data-availability assertion with
  `pytest.skip(..., allow_module_level=True)` when representative dates are
  insufficient.
- Also skip cleanly when the snapshot database cannot be opened or queried,
  including a missing database/table.
- Kept the existing representative-date selection and every correctness
  assertion unchanged when sufficient data is present.
- Changed no production code, ROI/prediction logic, schema, database data, or
  `render.yaml`.

## Verification

1. Focused collection/execution on the current data-incomplete environment:
   the module reports `1 skipped` rather than a collection error.
2. Required command:
   `.venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/e2e --ignore=tests/round3_e2e`
   collected 939 tests plus the intended module skip, proving collection
   continuity. Its first execution later hit the repository's known shared
   Windows pytest-temp ACL issue (198 setup errors from `PermissionError`).
3. The same selected suite rerun with the repository-contained basetemp passed:
   `939 passed, 1 skipped, 1 warning in 15.96s`. The warning was the pre-existing
   `.pytest_cache` creation warning.
4. Finally, with only process-local `TEMP`/`TMP` redirected to a clean
   repository-contained directory, the literal requested pytest command passed:
   `939 passed, 1 skipped, 1 warning in 16.19s`. The temporary directory was
   removed afterward.

## Safety and commit

- Push: not performed.
- Production logic and prohibited files: unchanged.
- Local commit: this report and the scoped test fix are committed together;
  the concrete commit ID is reported in the final task summary.
