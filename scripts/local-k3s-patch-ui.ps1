$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Message)
    Write-Host "OK: $Message" -ForegroundColor Green
}

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$uiDir = Join-Path $repoRoot "ui"
$wslRepoRoot = (wsl -d Ubuntu-24.04 -- wslpath -a $repoRoot).Trim()
Write-Step "Building patched UI bundle"
Push-Location $uiDir
try {
    $previousApiUrl = $env:VITE_API_URL
    $previousAuthority = $env:VITE_KEYCLOAK_AUTHORITY
    $previousClientId = $env:VITE_KEYCLOAK_CLIENT_ID

    $env:VITE_API_URL = "/api"
    $env:VITE_KEYCLOAK_AUTHORITY = "/realms/tertius"
    $env:VITE_KEYCLOAK_CLIENT_ID = "tertius-ui"

    npm.cmd run build
}
finally {
    $env:VITE_API_URL = $previousApiUrl
    $env:VITE_KEYCLOAK_AUTHORITY = $previousAuthority
    $env:VITE_KEYCLOAK_CLIENT_ID = $previousClientId
    Pop-Location
}

Write-Step "Finding running UI pod"
$pod = wsl -d Ubuntu-24.04 -u root -- kubectl -n tertius get pod -l app.kubernetes.io/component=ui -o jsonpath='{.items[0].metadata.name}'
if ([string]::IsNullOrWhiteSpace($pod)) {
    throw "Could not find running Tertius UI pod."
}
Write-Ok "Using UI pod $pod"

Write-Step "Copying patched UI bundle into pod"
wsl -d Ubuntu-24.04 -u root -- kubectl -n tertius exec $pod -- sh -lc "rm -rf /usr/share/nginx/html/*"
wsl -d Ubuntu-24.04 -u root -- kubectl -n tertius cp "$wslRepoRoot/ui/dist/." "${pod}:/usr/share/nginx/html"

Write-Step "Checking patched UI is served"
$response = Invoke-WebRequest -Uri "http://localhost:18080/" -UseBasicParsing -TimeoutSec 10
if ($response.StatusCode -ne 200) {
    throw "Patched UI did not return HTTP 200."
}
Write-Ok "Patched UI is live at http://localhost:18080/"

