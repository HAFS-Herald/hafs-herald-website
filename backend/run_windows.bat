@echo off
setlocal
cd /d "%~dp0"

REM Change this before sharing the server
set HAFS_ADMIN_PASSWORD=changeme

where py >nul 2>nul
if errorlevel 1 (
  echo ERROR: Python launcher "py" not found.
  echo Install Python from python.org and check "Add Python to PATH".
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  py -3 -m venv .venv
  if errorlevel 1 goto :err
)

call .venv\Scripts\activate
if errorlevel 1 goto :err

echo Installing requirements...
python -m pip install -r requirements.txt
if errorlevel 1 goto :err

echo Starting server...
python app.py
echo Server stopped.
pause
exit /b 0

:err
echo.
echo ERROR: Something failed above. Copy the message and send it to me.
pause
exit /b 1
