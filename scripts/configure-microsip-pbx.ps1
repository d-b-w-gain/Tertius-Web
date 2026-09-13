[CmdletBinding()]
param(
    [string]$MicroSipConfig = "",
    [string]$MicroSipExecutable = "$env:LOCALAPPDATA\MicroSIP\MicroSIP.exe",
    [string]$ProvisioningFile = "$env:LOCALAPPDATA\Spruik\extensions.txt",
    [switch]$RepairAutoStart,
    [switch]$StartAfterUpdate
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $ProvisioningFile)) {
    throw "PBX provisioning file not found: $ProvisioningFile"
}

$provisioning = @{}
foreach ($line in Get-Content -LiteralPath $ProvisioningFile) {
    if ($line -match '^([^=]+)=(.*)$') {
        $provisioning[$Matches[1]] = $Matches[2]
    }
}
foreach ($required in @("PBX_HOST", "SIP_PORT", "PC_EXTENSION", "PC_PASSWORD")) {
    if ([string]::IsNullOrWhiteSpace($provisioning[$required])) {
        throw "Provisioning file is missing $required"
    }
}

$microSipProcess = Get-Process -Name "MicroSIP" -ErrorAction SilentlyContinue
$resolvedMicroSipExecutable = if ($microSipProcess) {
    $microSipProcess.Path | Select-Object -First 1
}
else {
    $MicroSipExecutable
}

if (-not (Test-Path -LiteralPath $resolvedMicroSipExecutable)) {
    throw "MicroSIP executable not found: $resolvedMicroSipExecutable"
}

$microSipDirectory = Split-Path -Parent $resolvedMicroSipExecutable
if ([string]::IsNullOrWhiteSpace($MicroSipConfig)) {
    $portableConfig = Join-Path $microSipDirectory "MicroSIP.ini"
    $installedConfig = Join-Path $env:APPDATA "MicroSIP\MicroSIP.ini"
    $MicroSipConfig = if (Test-Path -LiteralPath $portableConfig) {
        $portableConfig
    }
    else {
        $installedConfig
    }
}

if (-not (Test-Path -LiteralPath $MicroSipConfig)) {
    throw "MicroSIP configuration not found: $MicroSipConfig"
}

if ($microSipProcess) {
    $microSipProcess | Stop-Process -Force
    $microSipProcess | Wait-Process -ErrorAction SilentlyContinue
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backupPath = "$MicroSipConfig.pre-pbx-$timestamp"
Copy-Item -LiteralPath $MicroSipConfig -Destination $backupPath

$lines = Get-Content -LiteralPath $MicroSipConfig
$result = [Collections.Generic.List[string]]::new()
$insideAccount = $false
$accountWritten = $false

$pbxAccount = @(
    "[Account1]"
    "label=Tertius PBX"
    "server=$($provisioning.PBX_HOST):$($provisioning.SIP_PORT)"
    "proxy="
    "domain=$($provisioning.PBX_HOST)"
    "username=$($provisioning.PC_EXTENSION)"
    "password=$($provisioning.PC_PASSWORD)"
    "authID=$($provisioning.PC_EXTENSION)"
    "displayName=DBG Systems Engineering"
    "dialingPrefix="
    "dialPlan="
    "hideCID="
    "voicemailNumber="
    "transport=udp"
    "publicAddr="
    "SRTP="
    "registerRefresh=300"
    "keepAlive=15"
    "publish=0"
    "ICE=0"
    "allowRewrite=0"
    "disableSessionTimer=0"
)

foreach ($line in $lines) {
    if ($line -eq "[Account1]") {
        foreach ($accountLine in $pbxAccount) {
            $result.Add($accountLine)
        }
        $insideAccount = $true
        $accountWritten = $true
        continue
    }
    if ($insideAccount) {
        if ($line -match '^\[') {
            $insideAccount = $false
            $result.Add($line)
        }
        continue
    }
    $result.Add($line)
}

if (-not $accountWritten) {
    throw "MicroSIP Account1 section was not found"
}

$result | Set-Content -LiteralPath $MicroSipConfig -Encoding utf8

Write-Host "MicroSIP now uses PBX extension $($provisioning.PC_EXTENSION)."
Write-Host "The direct Aussie Broadband configuration was backed up to: $backupPath"

if ($RepairAutoStart) {
    $runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
    $runEntry = Get-ItemProperty -Path $runKey -Name "MicroSIP" -ErrorAction SilentlyContinue
    if ($null -ne $runEntry) {
        Remove-ItemProperty -Path $runKey -Name "MicroSIP"
        Write-Host "Removed the duplicate MicroSIP registry startup entry."
    }

    $startupDirectory = [Environment]::GetFolderPath("Startup")
    $shortcutPath = Join-Path $startupDirectory "MicroSIP.lnk"
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $resolvedMicroSipExecutable
    $shortcut.Arguments = "/minimized"
    $shortcut.WorkingDirectory = $microSipDirectory
    $shortcut.Save()
    Write-Host "MicroSIP startup now uses the authoritative portable configuration directory."
}

if ($StartAfterUpdate) {
    Start-Process -FilePath $resolvedMicroSipExecutable -ArgumentList "/minimized" -WorkingDirectory $microSipDirectory
    Write-Host "MicroSIP started."
}
