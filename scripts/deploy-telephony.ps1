[CmdletBinding()]
param(
    [string]$ManifestDirectory = ""
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($ManifestDirectory)) {
    $ManifestDirectory = Join-Path $PSScriptRoot "..\infra\telephony"
}

kubectl apply -f (Join-Path $ManifestDirectory "namespace.yaml") | Out-Host

kubectl -n telephony get secret asterisk-secrets --output name *> $null
if ($LASTEXITCODE -ne 0) {
    throw "The telephony/asterisk-secrets Secret is missing. Copy the upstream SIP password, then run initialize-telephony-secrets.ps1 with the upstream username."
}

kubectl apply -k $ManifestDirectory | Out-Host
kubectl -n telephony rollout restart deployment/asterisk | Out-Host
kubectl -n telephony rollout status deployment/asterisk --timeout=180s | Out-Host

Write-Host "Asterisk is deployed. Existing SIP credentials were preserved."
