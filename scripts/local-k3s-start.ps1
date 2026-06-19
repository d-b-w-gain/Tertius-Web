$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$distro = $env:WSL_DISTRO

if (-not $distro) {
    $distros = @(wsl.exe -l -q 2>$null) |
        ForEach-Object { ($_ -replace [char]0, "").Trim() } |
        Where-Object { $_ }

    $distro = $distros | Where-Object { $_ -eq "Ubuntu-24.04" } | Select-Object -First 1
    if (-not $distro) {
        $distro = $distros | Where-Object { $_ -like "Ubuntu*" } | Select-Object -First 1
    }
    if (-not $distro) {
        $distro = $distros | Select-Object -First 1
    }
}

if (-not $distro) {
    Write-Error "No WSL distro was found. Install or repair WSL, or set WSL_DISTRO to the distro that hosts k3s."
    exit 1
}

$distro = $distro.Trim()
Write-Host "Using WSL distro: $distro"

$repoRootForWsl = $repoRoot.Path.Replace("\", "/")
$wslRepoRoot = (& wsl.exe -d $distro -- wslpath -a $repoRootForWsl 2>$null)
if ($LASTEXITCODE -ne 0 -or -not $wslRepoRoot) {
    Write-Error "Failed to map repo path into WSL using distro '$distro'."
    exit 1
}

$wslRepoRoot = (($wslRepoRoot | Select-Object -First 1) -replace [char]0, "").Trim()
$quotedWslRepoRoot = "'" + ($wslRepoRoot -replace "'", "'\''") + "'"

& wsl.exe -d $distro -u root -- bash -lc "cd $quotedWslRepoRoot && bash ./scripts/local-k3s-start-wsl.sh"
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

& (Join-Path $PSScriptRoot "local-k3s-patch-ui.ps1") @args
exit $LASTEXITCODE
