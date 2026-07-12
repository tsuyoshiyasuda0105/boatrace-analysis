# Server / Local Operation Plan (2026-07-12)

## 1. Root Cause of the 2026-07-12 Miss

- Local same-day collection did not fail from Windows reboot alone.
- The real blocker was `.pc_schedule_paused`.
- All local wrappers below exited immediately when that flag existed:
  - `run_daily_collect.bat`
  - `run_morning_task.bat`
  - `run_hourly_task.bat`
  - `run_beforeinfo_live.bat`
  - `run_odds_scheduler.bat`
  - `run_poll_results.bat`
  - `run_sync_to_supabase.bat`
  - `run_startup_catchup.bat`
- Result:
  - local DB had `races=0 / entries=0 / previews=0 / predictions=0` for `2026-07-12`
  - app looked like "no adopted races", but the correct interpretation was "today data not loaded yet"

## 2. What Is Already Server-Side

Render currently owns the main production path:

- `boatrace-web`
- `boatrace-odds-cron` (every minute)
- `boatrace-regular-cron` (every 5 minutes)
- `boatrace-roi-prewarm-cron` (every 30 minutes)

`render_regular_scheduler.py` already covers:

- same-day race collection
- prediction cache generation
- tide import
- beforeinfo scraping
- result polling
- data quality checks

## 3. What Should Stay Local

Keep local-PC jobs only for:

- heavy backfill
- exploratory verification
- emergency manual recovery
- local historical archive generation

## 4. Practical Target Architecture

### Production primary

- Render cron is the source of truth for:
  - today race data
  - previews
  - tide rows
  - predictions
  - result polling

### Local backup / research only

- Windows Task Scheduler remains only as:
  - startup catch-up helper
  - manual backup collector
  - historical backfill launcher

## 5. Restart / Pause Safety Rules

### Rule A

If `.pc_schedule_paused` exists, local tasks are intentionally disabled.

### Rule B

When the app has:

- zero or partial same-day predictions
- zero previews
- zero tide rows

it must not be shown as a clean "no candidates" state.

It should be treated as:

- data missing
- or decision pending

### Rule C

Render health must be checked from counts, not only HTTP 200.

Recommended checks:

- today races count
- today predictions count
- today previews count
- today tides count

## 6. Changes Added in This Pass

### App UI

- If the ROI-high list is empty but same-day base data is still incomplete,
  the table now shows:
  - `当日データがまだ揃っていないため判定不能です`
  instead of a misleading "no candidates".

### Health endpoint

- `/healthz` now returns `today_counts`:
  - races
  - predictions
  - previews
  - tides

This makes production monitoring easier and allows faster diagnosis when same-day adopted races appear to vanish.

## 7. Next Operational Steps

1. Keep Render as the production primary path.
2. Treat local jobs as fallback / research only.
3. Watch `/healthz` for today counts every morning.
4. Surface local pause state clearly in any local-only admin workflow.
5. Do not use "candidate count = 0" as proof of no adopted races unless same-day counts are complete.
