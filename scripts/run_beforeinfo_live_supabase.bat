@echo off
REM Live beforeinfo scraper for Supabase-only environments (Render clone, etc.).
REM Scrapes boatrace.jp `beforeinfo` for races closing in 5-9 min and writes
REM only to DATABASE_URL destination without mirroring into local SQLite.

cd /d C:\boat_project\boatrace-analysis
if not exist logs mkdir logs

set TS=%date:~0,4%%date:~5,2%%date:~8,2%
set LOG=logs\beforeinfo_live_supabase_%TS%.log

.venv\Scripts\python.exe scripts\scrape_beforeinfo_live.py --supabase-only >> "%LOG%" 2>&1
exit /b %errorlevel%
