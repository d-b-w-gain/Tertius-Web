@echo off
setlocal
wsl.exe -d Ubuntu-24.04 -u root -- bash -lc "cd /mnt/c/Users/ben/Documents/Projects/Tertius-Web && bash ./scripts/local-k3s-start-wsl.sh"
if errorlevel 1 exit /b %ERRORLEVEL%

call "%~dp0local-k3s-patch-ui.cmd"
exit /b %ERRORLEVEL%
