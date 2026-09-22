@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>&1
if errorlevel 1 (
  echo Python नहीं मिला.
  echo पहले Python 3.11/3.12 install करें: https://www.python.org/downloads/windows/
  pause
  exit /b 1
)

echo [1/3] Python packages install हो रहे हैं...
py -m pip install --upgrade pip
py -m pip install playwright beautifulsoup4

echo [2/3] Chromium browser install हो रहा है...
py -m playwright install chromium

echo [3/3] Latest Active Tenders test शुरू...
py backend\latest_active_manual_test.py

echo.
echo Test समाप्त.
pause
