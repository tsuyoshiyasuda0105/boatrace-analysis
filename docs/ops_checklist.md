# Operations Checklist

- [ ] Confirm repository status and active-file conflicts.
- [ ] Reproduce the production symptom with read-only queries.
- [ ] Identify root cause before editing.
- [ ] Apply the smallest scoped fix.
- [ ] Run targeted tests.
- [ ] Separate failures caused by the scoped change from pre-existing/upstream test drift.
- [ ] Verify result ingestion, accident snapshot freshness, and ROI ledger integrity.
- [ ] Confirm no local scheduler or production writer was started.
- [ ] Stop any temporary tools/processes.
- [ ] Update `docs/handoff.md` with outcome and remaining issues.
