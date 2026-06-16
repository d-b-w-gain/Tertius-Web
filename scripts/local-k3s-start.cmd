@echo off
setlocal
set "REPO_ROOT=%~dp0.."
for /f "usebackq delims=" %%i in (`wsl.exe -d Ubuntu-24.04 -- wslpath -a "%REPO_ROOT%"`) do set "WSL_REPO_ROOT=%%i"
wsl.exe -d Ubuntu-24.04 -u root -- bash -lc "cd '%WSL_REPO_ROOT%' && bash ./scripts/local-k3s-start-wsl.sh"
if errorlevel 1 exit /b %ERRORLEVEL%

call "%~dp0local-k3s-patch-ui.cmd"
exit /b %ERRORLEVEL%