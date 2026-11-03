[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RunId,
    [string]$ApiUrl = $(if ($env:CCTV_API_URL) { $env:CCTV_API_URL } else { "http://127.0.0.1:8000" }),
    [int]$TimeoutSeconds = $(if ($env:CCTV_API_TIMEOUT_SECONDS) { [int]$env:CCTV_API_TIMEOUT_SECONDS } else { 15 })
)

$ErrorActionPreference = "Stop"
$Uri = [Uri]$ApiUrl
if ($Uri.Scheme -ne "http" -or $Uri.Host -notin @("127.0.0.1", "localhost", "::1")) {
    throw "Reset is restricted to a plain-http loopback API URL"
}
if ($Uri.UserInfo -or $Uri.Query -or $Uri.Fragment) { throw "API URL must not contain credentials, query, or fragment" }
if ($TimeoutSeconds -lt 1) { throw "TimeoutSeconds must be positive" }
$Headers = @{}
if ($env:CCTV_API_TOKEN) { $Headers.Authorization = "Bearer $env:CCTV_API_TOKEN" }
$Base = $ApiUrl.TrimEnd("/")
$Snapshot = Invoke-RestMethod -Uri "$Base/api/v1/facility/snapshot" -Headers $Headers -TimeoutSec $TimeoutSeconds
if ($Snapshot.run_id -ne $RunId) {
    throw "Reset target mismatch: requested '$RunId', active '$($Snapshot.run_id)'; no reset was sent"
}
$Body = @{ run_id = $RunId } | ConvertTo-Json -Compress
$Response = Invoke-RestMethod -Uri "$Base/api/v1/demo/reset" -Method Post -Body $Body -ContentType "application/json" -Headers $Headers -TimeoutSec $TimeoutSeconds
Write-Host "API acknowledged run-scoped reset for '$RunId'."
Write-Host "This script deleted no files; source footage, models, configs, and calibration were not touched directly."
