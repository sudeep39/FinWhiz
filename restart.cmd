@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\restart-dev.ps1" %*
exit /b %ERRORLEVEL%
