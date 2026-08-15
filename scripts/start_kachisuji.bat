@echo off
setlocal
chcp 65001 >nul
for /f "delims=" %%T in ('powershell -NoProfile -Command "[Console]::OutputEncoding=[Text.Encoding]::UTF8; [string]::Concat([char]0x52DD,[char]0x3061,[char]0x7B4B,[char]0x30B5,[char]0x30FC,[char]0x30C1)"') do title %%T
cd /d "%~dp0.."

powershell -NoProfile -Command "try { $r=Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 http://127.0.0.1:8080/healthz; if ($r.StatusCode -eq 200) { exit 0 } } catch {}; exit 1"
if not errorlevel 1 (
  start "" http://localhost:8080
  exit /b 0
)

if not exist ".venv\Scripts\python.exe" (
  echo Python environment not found: .venv\Scripts\python.exe
  pause
  exit /b 1
)

REM Closing this window stops the server process running below.
start "" /b powershell -NoProfile -WindowStyle Hidden -Command "for ($i=0; $i -lt 20; $i++) { try { $r=Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 http://127.0.0.1:8080/healthz; if ($r.StatusCode -eq 200) { Start-Process http://localhost:8080; exit 0 } } catch {}; Start-Sleep -Milliseconds 500 }; exit 1"
".venv\Scripts\python.exe" scripts\run_kachisuji_web.py --port 8080
endlocal
