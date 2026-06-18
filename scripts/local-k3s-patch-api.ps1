param(
    [string]$Namespace = "tertius",
    [string]$Deployment = "tertius-api",
    [string]$ScaledJob = "tertius-api-compile"
)

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

function Write-Warn {
    param([string]$Message)
    Write-Host "WARN: $Message" -ForegroundColor Yellow
}

function Test-Command {
    param([string]$Name)
    $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Invoke-Native {
    param(
        [scriptblock]$Command,
        [string]$Description
    )
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE."
    }
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$repoRootForwardSlashes = $repoRoot.Replace('\', '/')
$wslRepoRoot = (wsl.exe -d Ubuntu-24.04 -- wslpath -a "$repoRootForwardSlashes").Trim()
$tag = "local-$((Get-Date).ToUniversalTime().ToString('yyyyMMddHHmmss'))"
$image = "localhost/tertius-api:$tag"
$windowsDocker = Test-Command "docker"
$wslDocker = -not [string]::IsNullOrWhiteSpace((wsl.exe -d Ubuntu-24.04 -- bash -lc "command -v docker || true"))

if ($windowsDocker) {
    $tarPath = Join-Path ([System.IO.Path]::GetTempPath()) "tertius-api-$tag.tar"
    $tarPathForwardSlashes = $tarPath.Replace('\', '/')
    $wslTarPath = (wsl.exe -d Ubuntu-24.04 -- wslpath -a "$tarPathForwardSlashes").Trim()
}
elseif ($wslDocker) {
    $tarPath = $null
    $wslTarPath = "/tmp/tertius-api-$tag.tar"
}
else {
    throw "Docker CLI was not found in Windows or WSL. Start Docker Desktop or install Docker CLI, then retry."
}

try {
    Write-Step "Building patched API image $image"
    if ($windowsDocker) {
        Invoke-Native { docker build -f (Join-Path $repoRoot "Dockerfile.api") -t $image $repoRoot } "Docker build"
    }
    else {
        Invoke-Native { wsl.exe -d Ubuntu-24.04 -u root -- bash -lc "cd '$wslRepoRoot' && docker build -f Dockerfile.api -t '$image' ." } "WSL Docker build"
    }

    Write-Step "Saving API image for k3s import"
    if ($tarPath -and (Test-Path $tarPath)) {
        Remove-Item -LiteralPath $tarPath -Force
    }
    if ($windowsDocker) {
        Invoke-Native { docker save -o $tarPath $image } "Docker save"
    }
    else {
        Invoke-Native { wsl.exe -d Ubuntu-24.04 -u root -- bash -lc "rm -f '$wslTarPath' && docker save -o '$wslTarPath' '$image'" } "WSL Docker save"
    }

    Write-Step "Importing API image into k3s containerd"
    Invoke-Native { wsl.exe -d Ubuntu-24.04 -u root -- k3s ctr -n k8s.io images import "$wslTarPath" } "k3s image import"

    Write-Step "Patching API deployment image"
    Invoke-Native { wsl.exe -d Ubuntu-24.04 -u root -- kubectl -n $Namespace set image "deployment/$Deployment" "api=$image" } "API deployment patch"

    Write-Step "Patching compile ScaledJob image when present"
    $scaledJobExists = wsl.exe -d Ubuntu-24.04 -u root -- kubectl -n $Namespace get scaledjob $ScaledJob --ignore-not-found -o name
    if (-not [string]::IsNullOrWhiteSpace($scaledJobExists)) {
        Write-Warn "Compile ScaledJob was not patched. Run a full local redeploy when compile-worker code changes need testing."
    }
    else {
        Write-Ok "Compile ScaledJob not present; skipped"
    }

    Write-Step "Waiting for API rollout"
    Invoke-Native { wsl.exe -d Ubuntu-24.04 -u root -- kubectl -n $Namespace rollout status "deployment/$Deployment" --timeout=180s } "API rollout"

    Write-Step "Checking patched API is served through localhost:18080"
    $response = Invoke-WebRequest -Uri "http://localhost:18080/api/" -UseBasicParsing -TimeoutSec 10
    if ($response.StatusCode -ne 200) {
        throw "Patched API did not return HTTP 200."
    }

    Write-Ok "Patched API is live at http://localhost:18080/api/"
    Write-Host "Image: $image"
}
finally {
    if ($tarPath -and (Test-Path $tarPath)) {
        Remove-Item -LiteralPath $tarPath -Force -ErrorAction SilentlyContinue
    }
    if ($wslTarPath) {
        wsl.exe -d Ubuntu-24.04 -- rm -f "$wslTarPath" 2>$null
    }
}
