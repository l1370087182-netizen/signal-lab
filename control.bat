@echo off
setlocal EnableExtensions
cd /d "%~dp0"
echo SIGNAL LAB Control
echo.
where python >nul 2>&1
if errorlevel 1 (
  echo [ERROR] python not found
  pause
  exit /b 1
)
python -u tools\control.py
set ERR=%ERRORLEVEL%
if not "%ERR%"=="0" (
  echo.
  echo Exit code %ERR%
  pause
)
exit /b %ERR%
