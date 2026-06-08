# start.ps1
# This script launches the Backend, Frontend, Databases, and Cloudflare Tunnel.

Write-Host "Starting Tertius Web Services via Docker Compose..." -ForegroundColor Green

# 1. Start all services via docker-compose (Backend, Frontend, Postgres, Keycloak)
docker-compose up -d --build

# 2. Wait for services to initialize
Write-Host "Waiting a few seconds for services to boot up..." -ForegroundColor Yellow
Start-Sleep -Seconds 5

# 3. Start Cloudflare Tunnel
# Since Vite is exposed on 5173 via docker-compose, we tunnel that port.
Write-Host "Starting Cloudflare Tunnel on port 5173..." -ForegroundColor Cyan
$TunnelCommand = "cloudflared tunnel --url http://localhost:5173" 
Start-Process powershell -ArgumentList "-NoExit", "-Command", $TunnelCommand -WindowStyle Normal

Write-Host "All services have been launched! The UI is building in the background container." -ForegroundColor Green
Write-Host "You can view logs by running: docker-compose logs -f" -ForegroundColor Gray
