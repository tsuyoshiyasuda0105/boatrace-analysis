@echo off
if exist C:\boat_project\boatrace-analysis\.pc_schedule_paused exit /b 0
REM Live beforeinfo monitor + exhibition rank sync + X motor rise queue.
if not exist C:\boat_project\boatrace-analysis\logs mkdir C:\boat_project\boatrace-analysis\logs
set TS=%date:~0,4%%date:~5,2%%date:~8,2%
set LOG=C:\boat_project\boatrace-analysis\logs\beforeinfo_live_%TS%.log
C:\boat_project\boatrace-analysis\.venv\Scripts\python.exe C:\boat_project\boatrace-analysis\scripts\self_heal_today_data.py >> "%LOG%" 2>&1
C:\boat_project\boatrace-analysis\.venv\Scripts\python.exe C:\boat_project\boatrace-analysis\scripts\run_monitor_beforeinfo_live_wrapper.py >> "%LOG%" 2>&1
exit /b %errorlevel%
