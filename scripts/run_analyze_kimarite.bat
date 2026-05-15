@echo off
REM Launcher for analyze_kimarite.py — runs the kimarite cross-tab analysis
REM and appends the result to logs\analyze_kimarite.log.
REM
REM Designed to be invoked hidden via wscript.exe + run_hidden.vbs, so the
REM user can schedule it without any cmd window appearing.
REM
REM Single log file (appended). Python writes session timestamp to the log
REM itself, so multiple runs are distinguishable. Rotate manually if needed.

cd /d C:\boat_project\boatrace-analysis
if not exist logs mkdir logs

REM Session header: ISO timestamp via Python (more reliable than wmic in
REM the hidden VBS context where stdout pipes can fail silently).
.venv\Scripts\python.exe -c "from datetime import datetime; print('\n====== analyze_kimarite run ' + datetime.now().isoformat(timespec='seconds') + ' ======')" >> logs\analyze_kimarite.log 2>&1

.venv\Scripts\python.exe scripts\analyze_kimarite.py >> logs\analyze_kimarite.log 2>&1
exit /b %errorlevel%
