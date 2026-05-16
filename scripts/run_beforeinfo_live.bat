@echo off
REM Live beforeinfo scraper (every ~10min via Task Scheduler BoatraceBeforeinfoLive).
REM Scrapes boatrace.jp `beforeinfo` for races closing in 5-30 min, overwrites
REM race_previews wind/wave/weather, then re-predicts and syncs to Supabase.
REM
REM Task Scheduler calls this through wscript.exe + run_hidden.vbs so cmd is
REM completely hidden. Tail logs\beforeinfo_live.log to confirm activity.

cd /d C:\boat_project\boatrace-analysis
if not exist logs mkdir logs

set TS=%date:~0,4%%date:~5,2%%date:~8,2%
set LOG=logs\beforeinfo_live_%TS%.log

.venv\Scripts\python.exe scripts\scrape_beforeinfo_live.py >> "%LOG%" 2>&1
exit /b %errorlevel%
