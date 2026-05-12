@echo off
cd /d C:\boat_project\boatrace-analysis
.venv\Scripts\python.exe scripts\send_l4_alerts.py >> logs\alert.log 2>&1
