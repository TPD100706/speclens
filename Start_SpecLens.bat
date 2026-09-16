@echo off
setlocal
title SpecLens Local Launcher
cd /d "%~dp0"

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_local.ps1"
set "SPECLENS_EXIT=%ERRORLEVEL%"

if not "%SPECLENS_EXIT%"=="0" (
    echo.
    echo SpecLens failed to start. Exit code: %SPECLENS_EXIT%
    echo Please take a screenshot of this window and send it to the project maintainer.
    pause
)

exit /b %SPECLENS_EXIT%
