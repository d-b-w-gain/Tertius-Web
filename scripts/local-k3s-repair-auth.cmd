@echo off
setlocal
wsl.exe -d Ubuntu-24.04 -u root -- bash -lc "cd /mnt/c/Users/ben/Documents/Projects/Tertius-Web && PUBLIC_BASE_URL=http://localhost:18080 bash ./scripts/local-k3s-repair-auth-wsl.sh"
exit /b %ERRORLEVEL%
