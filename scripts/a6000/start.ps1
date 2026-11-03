[CmdletBinding()]
param([switch]$Dev)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'env.ps1')
Set-Location $AegisRoot
$AegisPort = if ($Dev -and $env:CCTV_API_PORT) { [int]$env:CCTV_API_PORT } elseif (-not $Dev -and $env:BACKEND_PORT) { [int]$env:BACKEND_PORT } else { 8000 }
$Listeners = @(Get-NetTCPConnection -State Listen -LocalPort $AegisPort -ErrorAction SilentlyContinue)
if ($Listeners.Count -gt 0) {
    # A second backend must not run startup recovery against an active instance's database.
    Write-Host "Port $AegisPort is already in use; no second backend was started."
    Write-Host "If Aegis Ops is already running, open http://127.0.0.1:$AegisPort/"
    Write-Host 'Stop it first with: .venv-windows\Scripts\python.exe scripts\a6000\stop-server.py'
    exit 0
}
Write-Host "Profile: $AegisProfile"
if ($Dev) {
    Write-Host 'Aegis Ops development dashboard: http://127.0.0.1:7100/'
    & node (Join-Path $AegisRoot 'scripts\dev.mjs')
} else {
    Write-Host 'Aegis Ops dashboard: http://127.0.0.1:8000/  (Ctrl+C stops the server)'
    & (Join-Path $AegisRoot 'scripts\run-backend.ps1')
}
exit $LASTEXITCODE
