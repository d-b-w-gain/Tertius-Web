@echo off
setlocal
set "REPO_ROOT=%~dp0.."
for /f "usebackq delims=" %%i in (`wsl.exe -d Ubuntu-24.04 -- wslpath -a "%REPO_ROOT%"`) do set "WSL_REPO_ROOT=%%i"
wsl.exe -d Ubuntu-24.04 -u root -- bash -lc "cd '%WSL_REPO_ROOT%' && PUBLIC_BASE_URL=http://localhost:18080 bash ./scripts/local-k3s-repair-auth-wsl.sh"
exit /b %ERRORLEVEL%