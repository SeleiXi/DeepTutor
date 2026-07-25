@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0feature-lab.ps1" %*
exit /b %ERRORLEVEL%
