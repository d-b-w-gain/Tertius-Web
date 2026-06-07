# start.ps1
# This script launches the Backend, Frontend, and Cloudflare Tunnel in separate windows.

Write-Host "Starting Tertius Web Services..." -ForegroundColor Green

# 1. Start Backend (Docker)
$containerName = "tertius-backend"
$isRunning = docker ps -q -f name=$containerName
$exists = docker ps -aq -f name=$containerName

if ($isRunning) {
    Write-Host "Backend ($containerName) is already running!" -ForegroundColor Green
} elseif ($exists) {
    Write-Host "Backend ($containerName) exists but is stopped. Starting it..." -ForegroundColor Yellow
    docker start $containerName
} else {
    Write-Host "Starting Backend on port 8000..." -ForegroundColor Cyan
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "docker build -t tertius-server .; docker run --name $containerName -p 8000:8000 tertius-server" -WindowStyle Normal
}

# 2. Start Frontend (Vite)
Write-Host "Starting Frontend (Vite) on port 5173..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd ui; npm install; npm run dev" -WindowStyle Normal

# 3. Start Cloudflare Tunnel
# Assuming you use a named tunnel or a config file to forward both frontend and backend.
# Since Vite proxies the backend (see vite.config.ts), we only need to tunnel the frontend!
$TunnelCommand = "cloudflared tunnel --url http://localhost:5173" 
Start-Process powershell -ArgumentList "-NoExit", "-Command", $TunnelCommand -WindowStyle Normal

Write-Host "All services have been launched in separate windows!" -ForegroundColor Green
