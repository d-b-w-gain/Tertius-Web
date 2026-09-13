[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateNotNullOrEmpty()]
    [string]$UpstreamUsername,

    [string]$ProvisioningFile = "$env:LOCALAPPDATA\Spruik\extensions.txt",
    [string]$ManifestDirectory = ""
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($ManifestDirectory)) {
    $ManifestDirectory = Join-Path $PSScriptRoot "..\infra\telephony"
}

function New-SipPassword {
    $bytes = [byte[]]::new(18)
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
    }
    finally {
        $generator.Dispose()
    }
    return ([BitConverter]::ToString($bytes) -replace '-', '').ToLowerInvariant()
}

$upstreamPassword = (Get-Clipboard -Raw).Trim()
if ([string]::IsNullOrWhiteSpace($upstreamPassword) -or $upstreamPassword.Contains("`n")) {
    throw "The clipboard must contain only the upstream SIP password."
}

$provisioningDirectory = Split-Path -Parent $ProvisioningFile
New-Item -ItemType Directory -Path $provisioningDirectory -Force | Out-Null

$existing = @{}
if (Test-Path -LiteralPath $ProvisioningFile) {
    foreach ($line in Get-Content -LiteralPath $ProvisioningFile) {
        if ($line -match '^([^=]+)=(.*)$') {
            $existing[$Matches[1]] = $Matches[2]
        }
    }
}

$pcPassword = if ($existing.PC_PASSWORD) { $existing.PC_PASSWORD } else { New-SipPassword }
$iphonePassword = if ($existing.IPHONE_PASSWORD) { $existing.IPHONE_PASSWORD } else { New-SipPassword }

@(
    "PBX_HOST=192.168.88.29"
    "SIP_PORT=5060"
    "TRANSPORT=UDP"
    "PC_EXTENSION=101"
    "PC_PASSWORD=$pcPassword"
    "IPHONE_EXTENSION=102"
    "IPHONE_PASSWORD=$iphonePassword"
) | Set-Content -LiteralPath $ProvisioningFile -Encoding utf8

$secretConfig = @"
[aussie-auth]
type=auth
auth_type=userpass
username=$UpstreamUsername
password=$upstreamPassword

[pc-auth]
type=auth
auth_type=userpass
username=101
password=$pcPassword

[iphone-auth]
type=auth
auth_type=userpass
username=102
password=$iphonePassword
"@

$temporaryDirectory = Join-Path ([IO.Path]::GetTempPath()) ("spruik-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $temporaryDirectory | Out-Null
try {
    $secretConfigPath = Join-Path $temporaryDirectory "pjsip-secrets.conf"
    $secretManifestPath = Join-Path $temporaryDirectory "asterisk-secrets.yaml"
    Set-Content -LiteralPath $secretConfigPath -Value $secretConfig -Encoding utf8

    kubectl apply -f (Join-Path $ManifestDirectory "namespace.yaml") | Out-Host
    kubectl -n telephony create secret generic asterisk-secrets `
        --from-file="pjsip-secrets.conf=$secretConfigPath" `
        --dry-run=client -o yaml | Set-Content -LiteralPath $secretManifestPath -Encoding utf8
    kubectl apply -f $secretManifestPath | Out-Host
}
finally {
    $upstreamPassword = $null
    $secretConfig = $null
    if (Test-Path -LiteralPath $temporaryDirectory) {
        Remove-Item -LiteralPath $temporaryDirectory -Recurse -Force
    }
}

Write-Host "The Kubernetes SIP Secret is ready; its values were not displayed."
Write-Host "Endpoint credentials were saved to: $ProvisioningFile"
