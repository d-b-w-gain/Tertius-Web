@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0local-k3s-start.ps1" %*
exit /b %ERRORLEVEL%
