@echo off
setlocal
cd /d "%~dp0"

echo Checking Python...
python --version
if errorlevel 1 (
  echo Python was not found in PATH.
  echo Please install Python 3.12 from python.org.
  pause
  exit /b 1
)

echo [1/3] Installing Python packages...
python -m pip install --upgrade pip
if errorlevel 1 goto :error
python -m pip install playwright beautifulsoup4
if errorlevel 1 goto :error

echo [2/3] Installing Chromium...
python -m playwright install chromium
if errorlevel 1 goto :error

echo [3/3] Starting Latest Active Tenders test...
python backend\latest_active_manual_test.py
if errorlevel 1 goto :error

echo.
echo Test finished.
pause
exit /b 0

:error
echo.
echo Test stopped because a command failed.
pause
exit /b 1
