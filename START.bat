@echo off
setlocal EnableExtensions
title Computer Use Workbench
cd /d "%~dp0"

echo [INFO] Preparing Computer Use Workbench...
call "%~dp0setup.bat" --bootstrap-only
if errorlevel 1 goto failed

echo [INFO] Starting the app. Keep this window open; press Ctrl+C to stop.
call "%~dp0dev.bat" --open-browser
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" goto failed_with_code
endlocal
exit /b 0

:failed
set "EXIT_CODE=%ERRORLEVEL%"

:failed_with_code
echo.
echo [ERROR] Startup stopped with exit code %EXIT_CODE%.
echo [INFO] Fix the message above, then double-click START.bat again.
pause
endlocal
exit /b %EXIT_CODE%
