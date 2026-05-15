@echo off
REM Wrapper: backfill_official.py をローカル SQLite 強制で実行
REM DATABASE_URL を空にして config.py の load_dotenv より優先

set DATABASE_URL=
cd /d C:\boat_project\boatrace-analysis
set PYTHONUNBUFFERED=1
set PYTHONIOENCODING=utf-8

.venv\Scripts\python.exe scripts\backfill_official.py ^
    --start %1 --end %2 ^
    --skip-existing --verbose ^
    --log-file logs\backfill_local_%3.log
