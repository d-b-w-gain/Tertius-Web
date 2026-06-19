@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0local-k3s-patch-ui.ps1" %*
exit /b %ERRORLEVEL%
