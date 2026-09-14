[CmdletBinding()]
param(
    [ValidateSet("101", "102")]
    [string]$Extension = "102"
)

$ErrorActionPreference = "Stop"

kubectl -n telephony exec deployment/asterisk -c asterisk -- asterisk -rx "channel originate PJSIP/$Extension extension 600@from-internal"

if ($LASTEXITCODE -ne 0) {
    throw "Asterisk could not start the announcement call to extension $Extension."
}

Write-Host "Calling extension $Extension. Answer to hear the Kokoro announcement."
