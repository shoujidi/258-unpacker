@echo off
cd /d "%~dp0"

python build_single_exe.py
if errorlevel 1 goto fail

echo.
pause
exit /b 0

:fail
echo.
echo build failed
pause
exit /b 1
